from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

from pypdf import PdfReader
from src.local_chunker import chunk_single_document
from src.utils import load_json_template, search_rest_get, search_rest_put

if TYPE_CHECKING:
    from src.config import Settings


_CHUNK_METADATA_RE = re.compile(
    r"^\[page_row_num=(?P<page>[^\]|]+)(?:\s*\|\s*chapter_num=(?P<chapter>[^\]|]+)\s*\|\s*article_num=(?P<article>[^\]|]+)\s*\|\s*annex_num=(?P<annex>[^\]]+))?\]\s*",
    re.IGNORECASE,
)


class LocalIngester:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.output_dir = Path(__file__).resolve().parent.parent / "output_data" / "ingestion_payloads"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.search_client = SearchClient(
            endpoint=settings.search_endpoint,
            index_name=settings.index_name,
            credential=AzureKeyCredential(settings.search_admin_key),
        )

    def ensure_ingestion_resources(self) -> None:
        settings = self.settings
        tokens: dict[str, Any] = {
            "__INDEX_NAME__": settings.index_name,
            "__AZURE_OPENAI_ENDPOINT__": settings.azure_openai_endpoint[: -len("/openai/v1")],
            "__AZURE_OPENAI_EMBEDDING_DEPLOYMENT__": settings.azure_openai_embedding_deployment,
            "__AZURE_OPENAI_EMBEDDING_MODEL__": settings.azure_openai_embedding_model,
            "__AZURE_OPENAI_EMBEDDING_DIMENSIONS__": settings.azure_openai_embedding_dimensions,
        }

        index_payload = load_json_template("index.json", tokens)

        try:
            search_rest_get(settings.search_endpoint, settings.search_admin_key, f"indexes/{settings.index_name}")
            print(f"Index '{settings.index_name}' exists.")
        except RuntimeError as ex:
            if "status 404" not in str(ex).lower():
                raise
            print(f"Index '{settings.index_name}' not found. Creating it...")
            search_rest_put(settings.search_endpoint, settings.search_admin_key, f"indexes/{settings.index_name}", index_payload)
            print(f"Index '{settings.index_name}' created.")

    def collect_local_files(self) -> list[Path]:
        root = self.settings.local_storage
        if not root.exists():
            raise FileNotFoundError(f"Local folder not found: {root}")

        files = [
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in {".pdf", ".xlsx", ".xlsm", ".txt", ".md", ".json", ".csv", ".html", ".htm", ".xml"}
        ]
        if not files:
            raise ValueError(f"No supported files found in {root}")

        return sorted(files)

    def build_chunk_documents(self, source_file: Path) -> list[dict[str, Any]]:
        file_bytes = source_file.read_bytes()
        file_text = self._extract_source_text(source_file, file_bytes)

        chunk_result = chunk_single_document(
            {
                "text": file_text,
                "doc_name": source_file.name,
                "source_path": str(source_file),
                "file_data": {"data": base64.b64encode(file_bytes).decode("ascii")},
            }
        )

        legal_chunks: list[str] = chunk_result.get("legal_chunks", [])
        suffix = source_file.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            doc_summary = ""
        else:
            summary_source_text = str(chunk_result.get("corpus") or file_text)
            doc_summary = self._create_doc_summary(summary_source_text)

        output_documents: list[dict[str, Any]] = []
        for idx, chunk in enumerate(legal_chunks, start=1):
            meta = self._parse_chunk_metadata(chunk)
            chunk_body = meta.pop("body")
            text_for_embedding = f"{doc_summary}\n\n{chunk_body}".strip()
            embedding = self._create_embedding(text_for_embedding)

            stable_base = f"{source_file.as_posix()}::{idx}::{hashlib.sha1(chunk_body.encode('utf-8')).hexdigest()}"
            doc_id = hashlib.sha1(stable_base.encode("utf-8")).hexdigest()

            output_documents.append(
                {
                    "id": doc_id,
                    "source_type": source_file.suffix.lower().lstrip("."),
                    "doc_name": source_file.name,
                    "page_row_num": meta["page_row_num"],
                    "chapter_num": meta["chapter_num"],
                    "article_num": meta["article_num"],
                    "annex_num": meta["annex_num"],
                    "doc_summary": doc_summary,
                    "corpus": chunk_body,
                    "embedding": embedding,
                }
            )

        return output_documents

    def upload_documents(self, documents: list[dict[str, Any]]) -> None:
        if not documents:
            return

        # Remove older chunks for the same source document before pushing refreshed chunks.
        doc_name = documents[0]["doc_name"]
        to_delete: list[dict[str, str]] = []
        escaped_doc_name = doc_name.replace("'", "''")
        existing = self.search_client.search(search_text="*", filter=f"doc_name eq '{escaped_doc_name}'", select=["id"], top=1000)
        for hit in existing:
            hit_id = hit.get("id")
            if hit_id:
                to_delete.append({"id": hit_id})

        if to_delete:
            self.search_client.delete_documents(documents=to_delete)
            print(f"Deleted {len(to_delete)} stale chunks for '{doc_name}'.")

        batch_size = 100
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            result = self.search_client.upload_documents(documents=batch)
            failed = [r for r in result if not r.succeeded]
            if failed:
                raise RuntimeError(f"Failed uploading {len(failed)} chunks for '{doc_name}'.")

        print(f"Uploaded {len(documents)} chunks for '{doc_name}'.")

    def run(self) -> None:
        # Step 1: Ensure index exists and schema is valid.
        self.ensure_ingestion_resources()

        # Step 2: Collect supported local source files.
        source_files = self.collect_local_files()

        # Step 3: Chunk locally, embed locally, upload directly to index.
        for source_file in source_files:
            chunk_documents = self.build_chunk_documents(source_file)
            self.save_source_payload(source_file, chunk_documents)
            self.upload_documents(chunk_documents)

    def save_source_payload(self, source_file: Path, documents: list[dict[str, Any]]) -> None:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source_file.name)
        run_output_dir = self.output_dir / self.run_id
        run_output_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_output_dir / f"{safe_name}.json"
        payload = {
            "run_id": self.run_id,
            "source_file": str(source_file),
            "index_name": self.settings.index_name,
            "document_count": len(documents),
            "value": [{"@search.action": "upload", **self._document_for_audit(doc)} for doc in documents],
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved upload payload: {output_path}")

    @staticmethod
    def _document_for_audit(document: dict[str, Any]) -> dict[str, Any]:
        readable = dict(document)
        embedding = readable.pop("embedding", None)
        if isinstance(embedding, list):
            readable["embedding_dimensions"] = len(embedding)
        return readable

    def _extract_source_text(self, source_file: Path, file_bytes: bytes) -> str:
        suffix = source_file.suffix.lower()
        if suffix in {".txt", ".md", ".json", ".csv", ".html", ".htm", ".xml"}:
            return file_bytes.decode("utf-8", errors="ignore")

        if suffix == ".pdf":
            reader = PdfReader(str(source_file))
            pages: list[str] = []
            for page in reader.pages:
                pages.append((page.extract_text() or "").strip())
            return "\f".join([p for p in pages if p])

        return ""

    def _parse_chunk_metadata(self, chunk: str) -> dict[str, Any]:
        match = _CHUNK_METADATA_RE.match(chunk.strip())
        if not match:
            return {
                "page_row_num": 1,
                "chapter_num": 0,
                "article_num": "NA",
                "annex_num": "NA",
                "body": chunk.strip(),
            }

        body = chunk[match.end() :].strip()
        page_raw = (match.group("page") or "1").strip()
        chapter_raw = (match.group("chapter") or "0").strip()
        article_raw = (match.group("article") or "NA").strip()
        annex_raw = (match.group("annex") or "NA").strip()

        return {
            "page_row_num": self._safe_int(page_raw, default=1),
            "chapter_num": self._safe_int(chapter_raw, default=0),
            "article_num": article_raw if article_raw else "NA",
            "annex_num": annex_raw if annex_raw else "NA",
            "body": body,
        }

    def _create_embedding(self, text: str) -> list[float]:
        s = self.settings
        if not s.azure_openai_api_key:
            raise ValueError("AZURE_OPENAI_API_KEY is required for local ingestion embeddings.")

        endpoint = s.azure_openai_endpoint
        if endpoint.endswith("/"):
            endpoint = endpoint[:-1]
        if endpoint.endswith("/openai/v1"):
            endpoint = endpoint[: -len("/openai/v1")]

        url = f"{endpoint}/openai/deployments/{s.azure_openai_embedding_deployment}/embeddings?api-version=2024-10-21"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "api-key": s.azure_openai_api_key},
            json={"input": text},
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["data"][0]["embedding"]

    def _create_doc_summary(self, text: str) -> str:
        s = self.settings
        if not text.strip():
            return ""
        if not s.azure_openai_api_key:
            raise ValueError("AZURE_OPENAI_API_KEY is required for doc summary generation.")

        endpoint = s.azure_openai_endpoint
        if endpoint.endswith("/"):
            endpoint = endpoint[:-1]
        if endpoint.endswith("/openai/v1"):
            endpoint = endpoint[: -len("/openai/v1")]

        # Keep prompt input bounded so very large files do not overflow request limits.
        excerpt = text[:12000]
        url = f"{endpoint}/openai/deployments/{s.azure_openai_chat_deployment}/chat/completions?api-version=2024-10-21"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "api-key": s.azure_openai_api_key},
            json={
                "messages": [
                    {
                        "role": "system",
                        "content": "You create concise factual summaries for retrieval systems. Return plain text only.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Summarize the document content below in 5-8 sentences, preserving key obligations, definitions, and scope.\n\n"
                            f"{excerpt}"
                        ),
                    },
                ],
                "temperature": 0.0,
            },
            timeout=120,
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            return "\n".join(text_parts).strip()
        return str(content).strip()

    @staticmethod
    def _safe_int(value: str, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
