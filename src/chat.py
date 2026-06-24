from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

if TYPE_CHECKING:
    from src.config import Settings


@dataclass
class QueryProfile:
    complexity: str
    expertise: str
    specificity: str
    references: list[str]


@dataclass
class RetrievedChunk:
    key: str
    doc_name: str
    source_type: str
    page_row_num: Any
    chapter_num: Any
    article_num: Any
    annex_num: Any
    corpus: str
    lexical_rank: int | None = None
    vector_rank: int | None = None
    lexical_score: float = 0.0
    vector_score: float = 0.0
    fused_score: float = 0.0


class ChatQueryEngine:
    LEGAL_REFERENCE_PATTERNS = [
        r"\barticle\s+\d+[a-z]?\b",
        r"\bart\.?\s+\d+[a-z]?\b",
        r"\bannex\s+[ivxlcdm\d]+\b",
        r"\bchapter\s+[ivxlcdm\d]+\b",
        r"\bregulation\s*\(eu\)\s*\d{4}/\d+\b",
        r"\bdirective\s*\(eu\)\s*\d{4}/\d+\b",
        r"\b(?:rts|its|guideline|guidelines|delegated\s+regulation)\b",
    ]

    def __init__(
        self,
        settings: Settings,
        enable_trace_log: bool = False,
        trace_max_chars: int = 12000,
    ) -> None:
        self.settings = settings
        self.enable_trace_log = enable_trace_log
        self.trace_max_chars = trace_max_chars
        self._trace_turn_counter = 0
        self._trace_file_path: Path | None = None

        if self.enable_trace_log:
            log_dir = Path("output_data") / "chat_traces"
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._trace_file_path = log_dir / f"chat_{timestamp}.txt"
            self._append_trace(
                "Session Started",
                [
                    f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
                    f"chat_deployment: {self.settings.azure_openai_chat_deployment}",
                    f"index_name: {self.settings.index_name}",
                ],
            )

    def _truncate(self, value: str) -> str:
        if len(value) <= self.trace_max_chars:
            return value
        return f"{value[:self.trace_max_chars]}\n... [truncated {len(value) - self.trace_max_chars} chars]"

    def _append_trace(self, title: str, lines: list[str]) -> None:
        if not self.enable_trace_log or not self._trace_file_path:
            return

        section = [
            "=" * 88,
            f"{title}",
            f"logged_at: {datetime.now().isoformat(timespec='seconds')}",
            "-" * 88,
            *lines,
            "",
        ]
        with self._trace_file_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(section))

    def _format_messages_for_trace(self, messages: list[dict[str, str]]) -> str:
        return self._truncate(json.dumps(messages, indent=2, ensure_ascii=False))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    @staticmethod
    def _extract_assistant_text(response_json: dict[str, Any]) -> str:
        choices = response_json.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        content = message.get("content", "")

        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(parts).strip()
        return str(content).strip()

    def _post_chat_completion(self, messages: list[dict[str, str]], temperature: float = 0.1) -> dict[str, Any]:
        url = (
            f"{self.settings.azure_openai_endpoint[:-3]}/deployments/"
            f"{self.settings.azure_openai_chat_deployment}/chat/completions?api-version=2024-10-21"
        )
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "api-key": self.settings.azure_openai_api_key,
            },
            json={"messages": messages, "temperature": temperature},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def _create_embedding(self, text: str) -> list[float]:
        url = (
            f"{self.settings.azure_openai_endpoint[:-3]}/deployments/"
            f"{self.settings.azure_openai_embedding_deployment}/embeddings?api-version=2024-10-21"
        )

        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "api-key": self.settings.azure_openai_api_key,
            },
            json={"input": text},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]

    def _extract_references(self, query: str) -> list[str]:
        refs: list[str] = []
        for pattern in self.LEGAL_REFERENCE_PATTERNS:
            refs.extend(re.findall(pattern, query, flags=re.IGNORECASE))

        deduped: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            normalized = " ".join(ref.lower().split())
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(ref.strip())
        return deduped

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
        text = text.strip()

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                return None

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None

        return None

    def _classify_query_with_foundry(self, query: str) -> tuple[str, str] | None:
        messages = [
            {
                "role": "system",
                "content": (
                    "Classify a legal query for retrieval routing. "
                    "Return only JSON with keys user_expertise and query_complexity. "
                    "Allowed user_expertise: expert, layperson. "
                    "Allowed query_complexity: none, low, medium, high."
                ),
            },
            {"role": "user", "content": query},
        ]

        try:
            raw = self._extract_assistant_text(self._post_chat_completion(messages, temperature=0.0))
            payload = self._extract_json_object(raw)
            if not payload:
                return None

            expertise_raw = str(payload.get("user_expertise", "")).strip().lower().replace("-", "_").replace(" ", "_")
            if expertise_raw in {"expert", "specialist", "advanced", "professional"}:
                expertise = "expert"
            elif expertise_raw in {"layperson", "non_expert", "nonexpert", "beginner", "general"}:
                expertise = "layperson"
            else:
                expertise = "layperson"

            complexity_raw = str(payload.get("query_complexity", "")).strip().lower().replace("-", "_").replace(" ", "_")
            if complexity_raw in {"none", "no_retrieval", "n/a", "na", "skip"}:
                complexity = "none"
            elif complexity_raw in {"high", "complex", "multi_hop", "multi-hop"}:
                complexity = "high"
            elif complexity_raw in {"medium", "moderate", "mid"}:
                complexity = "medium"
            elif complexity_raw in {"low", "simple", "basic"}:
                complexity = "low"
            else:
                complexity = "medium"

            return (expertise, complexity)
        except Exception:
            return None

    def classify_query(self, query: str) -> QueryProfile:
        lowered = query.lower()
        token_count = len(self._tokenize(query))
        refs = self._extract_references(query)

        complex_markers = [" and ", " or ", " exception", "unless", "provided that", "however", "compare", "versus"]
        foundry_result = self._classify_query_with_foundry(query)

        if foundry_result:
            expertise, complexity = foundry_result
        else:
            # Skip retrieval for clear non-retrieval chat turns.
            no_retrieval_markers = {
                "hi",
                "hello",
                "hey",
                "thanks",
                "thank you",
                "bye",
                "ok",
                "okay",
                "great",
            }
            if lowered.strip() in no_retrieval_markers:
                complexity = "none"
            else:
                complexity = "high" if token_count > 28 or any(marker in lowered for marker in complex_markers) else "low"
            expert_terms = ["article", "annex", "recital", "delegated regulation", "rts", "its", "prudential", "supervisory", "compliance"]
            expertise = "expert" if any(term in lowered for term in expert_terms) else "layperson"

        if refs:
            specificity = "explicit"
        elif token_count < 8 or any(x in lowered for x in ["what does this", "explain", "help me understand"]):
            specificity = "vague"
        else:
            specificity = "verbose"

        return QueryProfile(
            complexity=complexity,
            expertise=expertise,
            specificity=specificity,
            references=refs,
        )

    def _decompose_query(self, query: str) -> list[str]:
        parts = [p.strip(" .") for p in re.split(r"\?|;|\band\b|\bor\b|\bthen\b", query, flags=re.IGNORECASE)]
        meaningful = [p for p in parts if len(self._tokenize(p)) >= 4]
        return meaningful[:3]

    def generate_step_back_query(self, query: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "Rewrite the user legal question into one concise, higher-level legal retrieval query. "
                    "Keep legal intent, avoid adding new facts, return one line only."
                ),
            },
            {"role": "user", "content": query},
        ]
        try:
            text = self._extract_assistant_text(self._post_chat_completion(messages, temperature=0.0))
            return text.strip().replace("\n", " ")
        except Exception:
            return ""

    def _build_query_variants(self, query: str, profile: QueryProfile) -> list[str]:
        variants: list[str] = [query]

        if profile.references:
            variants.append(" ".join(profile.references))

        if profile.complexity in {"high", "multi-hop"}:
            variants.extend(self._decompose_query(query))

        if profile.complexity in {"high", "multi-hop"} or profile.specificity == "vague":
            step_back = self.generate_step_back_query(query)
            if step_back:
                variants.append(step_back)

        deduped: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            cleaned = " ".join(variant.split())
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cleaned)

        return deduped[:5]

    @staticmethod
    def _chunk_key(result: Any) -> str:
        if result.get("id"):
            return str(result["id"])

        doc_name = str(result.get("doc_name") or "")
        source_type = str(result.get("source_type") or "")
        page_row_num = str(result.get("page_row_num") or "")
        article_num = str(result.get("article_num") or "")
        corpus_prefix = str(result.get("corpus") or "")[:80]
        return f"{doc_name}|{source_type}|{page_row_num}|{article_num}|{corpus_prefix}"

    def _normalize_chunk(self, result: Any) -> RetrievedChunk:
        return RetrievedChunk(
            key=self._chunk_key(result),
            doc_name=result.get("doc_name") or "unknown",
            source_type=result.get("source_type") or "unknown",
            page_row_num=result.get("page_row_num"),
            chapter_num=result.get("chapter_num"),
            article_num=result.get("article_num"),
            annex_num=result.get("annex_num"),
            corpus=result.get("corpus") or "",
        )

    def _search_lexical(self, search_client: SearchClient, query: str, top: int) -> list[RetrievedChunk]:
        results = search_client.search(
            search_text=query,
            select=["id", "doc_name", "source_type", "corpus", "article_num", "annex_num", "chapter_num", "page_row_num"],
            top=top,
        )
        return [self._normalize_chunk(row) for row in results]

    def _search_vector(self, search_client: SearchClient, query: str, top: int) -> list[RetrievedChunk]:
        vector = self._create_embedding(query)
        vector_query = VectorizedQuery(vector=vector, fields="embedding", k=top)
        results = search_client.search(
            search_text="",
            vector_queries=[vector_query],
            select=["id", "doc_name", "source_type", "corpus", "article_num", "annex_num", "chapter_num", "page_row_num"],
            top=top,
        )
        return [self._normalize_chunk(row) for row in results]

    @staticmethod
    def _choose_retrieval_depth(profile: QueryProfile) -> tuple[int, int, int]:
        if profile.complexity in {"high", "multi-hop"}:
            final_k, base_pool = 10, 26
        elif profile.complexity == "medium":
            final_k, base_pool = 8, 20
        else:
            final_k, base_pool = 6, 14

        if profile.expertise == "expert":
            lexical_pool = int(base_pool * 1.2)
            vector_pool = max(8, int(base_pool * 0.8))
        else:
            lexical_pool = max(8, int(base_pool * 0.8))
            vector_pool = int(base_pool * 1.2)

        if profile.specificity == "explicit":
            lexical_pool += 2
        if profile.specificity == "vague":
            vector_pool += 2

        return final_k, lexical_pool, vector_pool

    @staticmethod
    def _rrf_score(rank: int, k: int = 60) -> float:
        return 1.0 / (k + rank)

    def _fuse_ranked(self, lexical_chunks: list[RetrievedChunk], vector_chunks: list[RetrievedChunk], profile: QueryProfile) -> dict[str, RetrievedChunk]:
        lexical_weight = 1.0
        vector_weight = 1.0

        if profile.expertise == "expert":
            lexical_weight += 0.20
            vector_weight -= 0.10
        else:
            lexical_weight -= 0.10
            vector_weight += 0.20

        if profile.specificity == "explicit":
            lexical_weight = 1.25
            vector_weight = 0.9
        elif profile.complexity in {"high", "multi-hop"} or profile.specificity == "vague":
            lexical_weight = 0.9
            vector_weight = 1.25

        fused: dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(lexical_chunks, start=1):
            target = fused.setdefault(chunk.key, chunk)
            target.lexical_rank = rank
            target.lexical_score = lexical_weight * self._rrf_score(rank)

        for rank, chunk in enumerate(vector_chunks, start=1):
            target = fused.setdefault(chunk.key, chunk)
            target.vector_rank = rank
            target.vector_score = vector_weight * self._rrf_score(rank)

        for chunk in fused.values():
            chunk.fused_score = chunk.lexical_score + chunk.vector_score

        return fused

    def _lexical_overlap(self, query: str, corpus: str) -> float:
        query_tokens = self._tokenize(query)
        corpus_tokens = self._tokenize(corpus)
        if not query_tokens or not corpus_tokens:
            return 0.0
        return len(query_tokens & corpus_tokens) / len(query_tokens | corpus_tokens)

    def _reference_match_bonus(self, chunk: RetrievedChunk, references: list[str]) -> float:
        searchable = " ".join(
            [
                chunk.doc_name or "",
                str(chunk.article_num or ""),
                str(chunk.chapter_num or ""),
                str(chunk.annex_num or ""),
                chunk.source_type or "",
                chunk.corpus[:220],
            ]
        ).lower()

        bonus = 0.0
        for ref in references:
            if ref.lower() in searchable:
                bonus += 0.08
        return min(bonus, 0.24)

    def _rerank_chunks(self, query: str, chunks: list[RetrievedChunk], profile: QueryProfile) -> list[RetrievedChunk]:
        for chunk in chunks:
            overlap = self._lexical_overlap(query, chunk.corpus)
            ref_bonus = self._reference_match_bonus(chunk, profile.references)
            chunk.fused_score = (0.72 * chunk.fused_score) + (0.28 * overlap) + ref_bonus

        return sorted(chunks, key=lambda c: c.fused_score, reverse=True)

    def _mmr_select(self, chunks: list[RetrievedChunk], final_k: int, profile: QueryProfile) -> list[RetrievedChunk]:
        if not chunks:
            return []

        relevance_lambda = 0.8 if profile.complexity in {"low", "simple"} else 0.65
        if profile.specificity == "vague":
            relevance_lambda = 0.6

        selected: list[RetrievedChunk] = []
        remaining = chunks[:]

        while remaining and len(selected) < final_k:
            best_idx = 0
            best_value = -1e9

            for idx, candidate in enumerate(remaining):
                candidate_tokens = self._tokenize(candidate.corpus)
                max_similarity = 0.0

                for chosen in selected:
                    chosen_tokens = self._tokenize(chosen.corpus)
                    union = candidate_tokens | chosen_tokens
                    if not union:
                        continue
                    similarity = len(candidate_tokens & chosen_tokens) / len(union)
                    if similarity > max_similarity:
                        max_similarity = similarity

                value = (relevance_lambda * candidate.fused_score) - ((1.0 - relevance_lambda) * max_similarity)
                if value > best_value:
                    best_value = value
                    best_idx = idx

            selected.append(remaining.pop(best_idx))

        return selected

    def retrieve_context(self, query: str, profile: QueryProfile) -> str:
        search_client = SearchClient(
            endpoint=self.settings.search_endpoint,
            index_name=self.settings.index_name,
            credential=AzureKeyCredential(self.settings.search_admin_key),
        )

        final_k, lexical_pool, vector_pool = self._choose_retrieval_depth(profile)
        query_variants = self._build_query_variants(query, profile)

        aggregated: dict[str, RetrievedChunk] = {}
        for variant in query_variants:
            lexical = self._search_lexical(search_client, variant, top=lexical_pool)
            vector = self._search_vector(search_client, variant, top=vector_pool)
            fused = self._fuse_ranked(lexical, vector, profile)

            for key, chunk in fused.items():
                if key not in aggregated:
                    aggregated[key] = chunk
                else:
                    existing = aggregated[key]
                    existing.lexical_score = max(existing.lexical_score, chunk.lexical_score)
                    existing.vector_score = max(existing.vector_score, chunk.vector_score)
                    existing.fused_score = existing.lexical_score + existing.vector_score

        ranked = sorted(aggregated.values(), key=lambda x: x.fused_score, reverse=True)
        reranked = self._rerank_chunks(query, ranked, profile)
        selected = self._mmr_select(reranked, final_k=final_k, profile=profile)

        if not selected:
            return "No relevant context retrieved from Azure AI Search."

        chunks: list[str] = []
        for idx, result in enumerate(selected, start=1):
            chunks.append(
                f"[{idx}] doc={result.doc_name} type={result.source_type} page_row={result.page_row_num} "
                f"chapter={result.chapter_num} article={result.article_num} annex={result.annex_num}\n"
                f"{result.corpus}"
            )

        return "\n\n".join(chunks)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a legal regulatory assistant. Use only the retrieved context to answer. "
            "If context is insufficient, respond with 'I don't know based on the retrieved context'. "
            "If retrieval is explicitly skipped, provide a brief generic reply and do not invent legal citations. "
            "When multiple sources exist, prioritize binding legal sources over non-binding guidance. "
            "Cite source chunk numbers in square brackets like [1], [2]."
        )

    @staticmethod
    def _citations_from_context(context_text: str) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        header_re = re.compile(
            r"^\[(?P<source_id>\d+)\]\s+doc=(?P<doc_name>.*?)\s+type=(?P<source_type>.*?)\s+"
            r"page_row=(?P<page_row_num>.*?)\s+chapter=(?P<chapter_num>.*?)\s+"
            r"article=(?P<article_num>.*?)\s+annex=(?P<annex_num>.*)$"
        )

        for line in context_text.splitlines():
            match = header_re.match(line.strip())
            if not match:
                continue
            row = match.groupdict()
            row["source_id"] = int(row["source_id"])
            citations.append(row)

        return citations

    def answer_once(
        self,
        user_text: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Run one RAG turn for API or terminal callers."""

        user_text = user_text.strip()
        if not user_text:
            raise ValueError("Question cannot be empty.")

        history = history or []
        self._trace_turn_counter += 1
        turn_id = self._trace_turn_counter
        trace_id = f"turn-{turn_id}"

        self._append_trace(
            f"Turn {turn_id}: User Input",
            [
                f"turn_id: {turn_id}",
                f"user_query: {user_text}",
            ],
        )

        active_query = user_text
        profile = self.classify_query(active_query)
        self._append_trace(
            f"Turn {turn_id}: Classification",
            [
                f"active_query: {active_query}",
                f"complexity: {profile.complexity}",
                f"expertise: {profile.expertise}",
                f"specificity: {profile.specificity}",
                f"references: {', '.join(profile.references) if profile.references else 'none'}",
            ],
        )

        step_back_used = "not used"
        if profile.complexity == "high":
            step_back = self.generate_step_back_query(active_query)
            if step_back:
                active_query = step_back
                step_back_used = step_back

        self._append_trace(
            f"Turn {turn_id}: Step-Back",
            [
                f"step_back_query: {step_back_used}",
                f"active_query_after_step_back: {active_query}",
            ],
        )

        retrieval_skipped = profile.complexity == "none"
        if retrieval_skipped:
            context_text = "Retrieval skipped because complexity=none."
        else:
            context_text = self.retrieve_context(active_query, profile)

        self._append_trace(
            f"Turn {turn_id}: Retrieved Context",
            [
                f"retrieval_skipped: {retrieval_skipped}",
                f"context_chars: {len(context_text)}",
                self._truncate(context_text),
            ],
        )

        augmented_user = (
            f"Question:\n{user_text}\n\n"
            f"Active retrieval query:\n{active_query}\n"
            f"Query profile: complexity={profile.complexity}, expertise={profile.expertise}, specificity={profile.specificity}\n"
            f"Retrieved context:\n{context_text}\n\n"
            "Answer using only retrieved context unless retrieval was skipped."
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt()}]
        messages.extend(history[-8:])
        messages.append({"role": "user", "content": augmented_user})

        self._append_trace(
            f"Turn {turn_id}: Prompt Sent To LLM",
            [self._format_messages_for_trace(messages)],
        )

        assistant_text = self._extract_assistant_text(self._post_chat_completion(messages, temperature=0.1))
        self._append_trace(
            f"Turn {turn_id}: LLM Response",
            [self._truncate(assistant_text)],
        )
        self._append_trace(
            f"Turn {turn_id}: Completed",
            [
                f"assistant_response_chars: {len(assistant_text)}",
                f"history_messages: {len(history) + 2}",
            ],
        )

        return {
            "answer": assistant_text,
            "citations": [] if retrieval_skipped else self._citations_from_context(context_text),
            "retrieved_context": "" if retrieval_skipped else context_text,
            "trace_id": trace_id,
            "profile": {
                "complexity": profile.complexity,
                "expertise": profile.expertise,
                "specificity": profile.specificity,
                "references": profile.references,
            },
            "active_query": active_query,
            "retrieval_skipped": retrieval_skipped,
        }

    def chat_in_terminal(self) -> None:
        print("Starting direct key-based chat. Type 'exit' to stop or 'clear' to reset context.")
        if self.enable_trace_log and self._trace_file_path:
            print(f"Trace log enabled: {self._trace_file_path}")
        history: list[dict[str, str]] = []

        while True:
            user_text = input("You: ").strip()
            normalized = user_text.lower()

            if normalized in {"exit", "quit", "q"}:
                self._append_trace("Session Ended", ["reason: user_exit"])
                print("Bye.")
                return
            if normalized == "clear":
                history.clear()
                self._append_trace("History Cleared", ["reason: user_clear"]) 
                print("Conversation context cleared.")
                continue
            if not user_text:
                continue

            result = self.answer_once(user_text, history=history)
            assistant_text = result["answer"]

            print(f"Assistant: {assistant_text}\n")
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": assistant_text})
