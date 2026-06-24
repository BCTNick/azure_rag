from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pypdf import PdfReader

from src.local_chunker import chunk_single_document


SUPPORTED_SUFFIXES = {".pdf", ".xlsx", ".xlsm", ".txt", ".md", ".json", ".csv", ".html", ".htm", ".xml"}
CHUNK_METADATA_RE = re.compile(
    r"^\[page_row_num=(?P<page>[^\]|]+)(?:\s*\|\s*chapter_num=(?P<chapter>[^\]|]+)\s*\|\s*article_num=(?P<article>[^\]|]+)\s*\|\s*annex_num=(?P<annex>[^\]]+))?\]\s*",
    re.IGNORECASE,
)


def main() -> None:
    load_dotenv()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    local_storage = Path(os.getenv("LOCAL_STORAGE", "input_data/local_storage"))
    output_dir = Path("output_data") / "chunk_previews" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    source_files = collect_local_files(local_storage)
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "local_storage": str(local_storage),
        "source_file_count": len(source_files),
        "files": [],
    }

    for source_file in source_files:
        preview = build_preview(source_file, run_id)
        output_path = output_dir / f"{safe_filename(source_file.name)}.json"
        output_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["files"].append(
            {
                "source_file": str(source_file),
                "preview_file": str(output_path),
                "chunk_count": preview["chunk_count"],
            }
        )
        print(f"Saved chunk preview: {output_path}")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved chunk preview manifest: {manifest_path}")


def collect_local_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Local folder not found: {root}")

    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES]
    if not files:
        raise ValueError(f"No supported files found in {root}")
    return sorted(files)


def build_preview(source_file: Path, run_id: str) -> dict[str, Any]:
    file_bytes = source_file.read_bytes()
    file_text = extract_source_text(source_file, file_bytes)
    chunk_result = chunk_single_document(
        {
            "text": file_text,
            "doc_name": source_file.name,
            "source_path": str(source_file),
            "file_data": {"data": base64.b64encode(file_bytes).decode("ascii")},
        }
    )

    chunks = [parse_chunk(chunk, idx) for idx, chunk in enumerate(chunk_result.get("legal_chunks", []), start=1)]
    return {
        "run_id": run_id,
        "source_file": str(source_file),
        "doc_name": source_file.name,
        "source_type": source_file.suffix.lower().lstrip("."),
        "chunk_count": len(chunks),
        "chunker_doc_summary": chunk_result.get("doc_summary", ""),
        "chunks": chunks,
    }


def extract_source_text(source_file: Path, file_bytes: bytes) -> str:
    suffix = source_file.suffix.lower()
    if suffix in {".txt", ".md", ".json", ".csv", ".html", ".htm", ".xml"}:
        return file_bytes.decode("utf-8", errors="ignore")

    if suffix == ".pdf":
        reader = PdfReader(str(source_file))
        pages: list[str] = []
        for page in reader.pages:
            pages.append((page.extract_text() or "").strip())
        return "\f".join([page for page in pages if page])

    return ""


def parse_chunk(chunk: str, chunk_id: int) -> dict[str, Any]:
    match = CHUNK_METADATA_RE.match(chunk.strip())
    if not match:
        body = chunk.strip()
        return {
            "chunk_id": chunk_id,
            "page_row_num": 1,
            "chapter_num": 0,
            "article_num": "NA",
            "annex_num": "NA",
            "char_count": len(body),
            "text": body,
        }

    body = chunk[match.end() :].strip()
    return {
        "chunk_id": chunk_id,
        "page_row_num": safe_int((match.group("page") or "1").strip(), default=1),
        "chapter_num": safe_int((match.group("chapter") or "0").strip(), default=0),
        "article_num": (match.group("article") or "NA").strip() or "NA",
        "annex_num": (match.group("annex") or "NA").strip() or "NA",
        "char_count": len(body),
        "text": body,
    }


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(1)
    except Exception as ex:
        print(f"ERROR: {ex}")
        sys.exit(1)
