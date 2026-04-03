from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

if TYPE_CHECKING:
    from main import Settings


def _openai_deployment_url(endpoint: str, deployment: str, operation: str) -> str:
    return f"{endpoint}/openai/deployments/{deployment}/{operation}?api-version=2024-10-21"


def _create_embedding(settings: Settings, text: str) -> list[float]:
    if not settings.azure_openai_api_key:
        raise ValueError("AZURE_OPENAI_API_KEY is required for direct chat mode.")

    url = _openai_deployment_url(
        settings.azure_openai_endpoint,
        settings.azure_openai_embedding_deployment,
        "embeddings",
    )
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "api-key": settings.azure_openai_api_key,
        },
        json={"input": text},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return data["data"][0]["embedding"]


def _retrieve_context(settings: Settings, query: str, top_k: int = 5) -> str:
    search_client = SearchClient(
        endpoint=settings.search_endpoint,
        index_name=settings.index_name,
        credential=AzureKeyCredential(settings.search_admin_key),
    )

    vector = _create_embedding(settings, query)
    vector_query = VectorizedQuery(
        vector=vector,
        k_nearest_neighbors=10,
        fields="embedding",
    )
    results = search_client.search(
        search_text=query,
        vector_queries=[vector_query],
        select=["doc_name", "corpus", "regulation_reference", "article_num", "page_num"],
        top=top_k,
    )

    chunks: list[str] = []
    for idx, result in enumerate(results, start=1):
        doc_name = result.get("doc_name") or "unknown"
        page_num = result.get("page_num")
        reference = result.get("regulation_reference")
        article_num = result.get("article_num")
        corpus = result.get("corpus") or ""
        chunks.append(
            f"[{idx}] doc={doc_name} page={page_num} article={article_num} ref={reference}\n{corpus}"
        )

    if not chunks:
        return "No relevant context retrieved from Azure AI Search."
    return "\n\n".join(chunks)


def _extract_assistant_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()
    return str(content).strip()


def chat_in_terminal(settings: Settings) -> None:
    if not settings.azure_openai_api_key:
        raise ValueError("AZURE_OPENAI_API_KEY is required for direct chat mode")

    print("Starting direct key-based chat. Type 'exit' to stop or 'clear' to reset context.")
    system_prompt = (
        "You are a helpful assistant. Use only the retrieved context to answer. "
        "If context is insufficient, respond with 'I don't know'. "
        "Cite the source chunk numbers in square brackets like [1], [2]."
    )
    history: list[dict[str, str]] = []

    while True:
        user_text = input("You: ").strip()
        normalized = user_text.lower()
        if normalized in {"exit", "quit", "q"}:
            print("Bye.")
            return
        if normalized == "clear":
            history.clear()
            print("Conversation context cleared.")
            continue
        if not user_text:
            continue

        context_text = _retrieve_context(settings, user_text)
        augmented_user = (
            f"Question:\n{user_text}\n\n"
            f"Retrieved context:\n{context_text}\n\n"
            "Answer using only retrieved context."
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-8:])
        messages.append({"role": "user", "content": augmented_user})

        url = _openai_deployment_url(
            settings.azure_openai_endpoint,
            settings.azure_openai_chat_deployment,
            "chat/completions",
        )
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "api-key": settings.azure_openai_api_key,
            },
            json={"messages": messages, "temperature": 0.1},
            timeout=120,
        )
        response.raise_for_status()
        assistant_text = _extract_assistant_text(response.json())
        print(f"Assistant: {assistant_text}\n")

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
