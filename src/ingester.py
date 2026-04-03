from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    KnowledgeBase,
    KnowledgeRetrievalMinimalReasoningEffort,
    KnowledgeRetrievalOutputMode,
    KnowledgeSourceReference,
    SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
)
from azure.storage.blob import BlobServiceClient

if TYPE_CHECKING:
    from main import Settings


def _json_templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "input_data" / "jsons"


def _template_tokens(settings: Settings) -> dict[str, Any]:
    return {
        "__INDEX_NAME__": settings.index_name,
        "__DATASOURCE_NAME__": settings.data_source_name,
        "__SKILLSET_NAME__": settings.skillset_name,
        "__INDEXER_NAME__": settings.indexer_name,
        "__KNOWLEDGE_SOURCE_NAME__": settings.knowledge_source_name,
        "__STORAGE_CONNECTION_STRING__": settings.storage_connection_string,
        "__STORAGE_CONTAINER_NAME__": settings.storage_container_name,
        "__AZURE_OPENAI_ENDPOINT__": settings.azure_openai_endpoint,
        "__AZURE_OPENAI_EMBEDDING_DEPLOYMENT__": settings.azure_openai_embedding_deployment,
        "__AZURE_OPENAI_EMBEDDING_MODEL__": settings.azure_openai_embedding_model,
        "__AZURE_OPENAI_EMBEDDING_DIMENSIONS__": settings.azure_openai_embedding_dimensions,
        "__AZURE_OPENAI_API_KEY__": settings.azure_openai_api_key or "",
    }


def _replace_tokens(value: Any, tokens: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _replace_tokens(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_tokens(item, tokens) for item in value]
    if isinstance(value, str):
        if value in tokens:
            return tokens[value]
        rendered = value
        for token, replacement in tokens.items():
            rendered = rendered.replace(token, str(replacement))
        return rendered
    return value


def _load_json_template(template_name: str, settings: Settings) -> dict[str, Any]:
    template_path = _json_templates_dir() / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    raw = template_path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"Template file is empty: {template_path}")

    parsed = json.loads(raw)
    return _replace_tokens(parsed, _template_tokens(settings))


def _get_local_folder(settings: Settings) -> Path:
    local_folder = getattr(settings, "local_storage", None)
    if local_folder is None:
        raise AttributeError("Settings must define 'local_storage'.")
    return Path(local_folder)


def _search_rest_head_exists(settings: Settings, path: str, api_version: str = "2025-09-01") -> bool:
    """Returns True if a Search REST resource exists, False on 404."""
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"api-key": settings.search_admin_key}
    response = requests.get(url, headers=headers, timeout=120)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def _search_rest_put(settings: Settings, path: str, payload: dict, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"Content-Type": "application/json", "api-key": settings.search_admin_key}
    response = requests.put(url, headers=headers, json=payload, timeout=120)
    try:
        response.raise_for_status()
    except requests.HTTPError as ex:
        details = (response.text or "").strip()
        raise RuntimeError(
            f"Search PUT failed for '{path}' with status {response.status_code}. Response: {details}"
        ) from ex
    return response.json() if response.text else {}


def _search_rest_post(settings: Settings, path: str, payload: dict | None = None, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"Content-Type": "application/json", "api-key": settings.search_admin_key}
    response = requests.post(url, headers=headers, json=payload or {}, timeout=120)
    try:
        response.raise_for_status()
    except requests.HTTPError as ex:
        details = (response.text or "").strip()
        raise RuntimeError(
            f"Search POST failed for '{path}' with status {response.status_code}. Response: {details}"
        ) from ex
    return response.json() if response.text else {}


def _search_rest_delete(settings: Settings, path: str, api_version: str = "2025-09-01") -> None:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"api-key": settings.search_admin_key}
    response = requests.delete(url, headers=headers, timeout=120)
    if response.status_code in {200, 202, 204, 404}:
        return
    try:
        response.raise_for_status()
    except requests.HTTPError as ex:
        details = (response.text or "").strip()
        raise RuntimeError(
            f"Search DELETE failed for '{path}' with status {response.status_code}. Response: {details}"
        ) from ex


def _search_rest_get(settings: Settings, path: str, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"api-key": settings.search_admin_key}
    response = requests.get(url, headers=headers, timeout=120)
    try:
        response.raise_for_status()
    except requests.HTTPError as ex:
        details = (response.text or "").strip()
        raise RuntimeError(
            f"Search GET failed for '{path}' with status {response.status_code}. Response: {details}"
        ) from ex
    return response.json() if response.text else {}


def _blob_container_exists(settings: Settings) -> bool:
    blob_service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    container_client = blob_service.get_container_client(settings.storage_container_name)
    return container_client.exists()


def _knowledge_source_exists(settings: Settings, index_client: SearchIndexClient) -> bool:
    try:
        index_client.get_knowledge_source(settings.knowledge_source_name)
        return True
    except HttpResponseError as ex:
        if ex.status_code == 404:
            return False
        raise


def _knowledge_base_exists(settings: Settings, index_client: SearchIndexClient) -> bool:
    try:
        index_client.get_knowledge_base(settings.knowledge_base_name)
        return True
    except HttpResponseError as ex:
        if ex.status_code == 404:
            return False
        raise


def _delete_knowledge_artifacts(settings: Settings) -> None:
    """Deletes KB and KS when an index schema reset is blocked by references."""
    index_client = SearchIndexClient(
        endpoint=settings.search_endpoint,
        credential=AzureKeyCredential(settings.search_admin_key),
    )

    try:
        index_client.delete_knowledge_base(settings.knowledge_base_name)
        print(f"Deleted knowledge base '{settings.knowledge_base_name}'.")
    except HttpResponseError as ex:
        if ex.status_code != 404:
            raise

    try:
        index_client.delete_knowledge_source(knowledge_source=settings.knowledge_source_name)
        print(f"Deleted knowledge source '{settings.knowledge_source_name}'.")
    except HttpResponseError as ex:
        if ex.status_code != 404:
            raise


def _index_has_documents(settings: Settings) -> bool:
    url = f"{settings.search_endpoint}/indexes/{settings.index_name}/docs/$count?api-version=2025-09-01"
    headers = {"api-key": settings.search_admin_key}
    response = requests.get(url, headers=headers, timeout=120)
    response.raise_for_status()
    count_text = response.text.strip()
    try:
        return int(count_text) > 0
    except ValueError:
        return False


def create_blob_container(settings: Settings) -> None:
    print("Creating blob container if it doesn't exist...")
    blob_service_client = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    container_client = blob_service_client.get_container_client(settings.storage_container_name)
    if not container_client.exists():
        container_client.create_container()
        print(f"Blob container '{settings.storage_container_name}' created.")
    else:
        print(f"Blob container '{settings.storage_container_name}' already exists.")


def upload_local_files_to_blob(settings: Settings) -> None:
    print("Uploading files from local folder to Azure Blob Storage...")
    local_folder = _get_local_folder(settings)
    if not local_folder.exists():
        raise FileNotFoundError(f"Local folder not found: {local_folder}")

    blob_service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    container_client = blob_service.get_container_client(settings.storage_container_name)
    if not container_client.exists():
        container_client.create_container()

    files = [p for p in local_folder.rglob("*") if p.is_file()]
    if not files:
        raise ValueError(f"No files found in {local_folder}")

    for path in files:
        blob_name = str(path.relative_to(local_folder)).replace("\\", "/")
        with path.open("rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        print(f"Uploaded: {blob_name}")


def create_search_index(settings: Settings) -> None:
    print("Creating or updating Azure AI Search index...")
    payload = _load_json_template("index.json", settings)
    try:
        _search_rest_put(settings, f"indexes/{settings.index_name}", payload)
    except RuntimeError as ex:
        error_text = str(ex)
        if "CannotChangeExistingField" not in error_text and "cannot be changed" not in error_text:
            raise

        if _index_has_documents(settings):
            raise RuntimeError(
                "Index schema is incompatible with current template and the index already has documents. "
                "Use a new AZURE_SEARCH_INDEX_NAME or delete/recreate the existing index manually."
            ) from ex

        print("Existing empty index has incompatible schema. Recreating index...")
        try:
            _search_rest_delete(settings, f"indexes/{settings.index_name}")
        except RuntimeError as delete_ex:
            delete_text = str(delete_ex)
            if "CannotDeleteIndex" in delete_text and "knowledge source" in delete_text.lower():
                print("Index is referenced by knowledge artifacts. Deleting KB/KS references and retrying...")
                _delete_knowledge_artifacts(settings)
                _search_rest_delete(settings, f"indexes/{settings.index_name}")
            else:
                raise
        _search_rest_put(settings, f"indexes/{settings.index_name}", payload)


def create_data_source_skillset_and_indexer(settings: Settings) -> None:
    print("Creating or updating data source...")
    data_source_payload = _load_json_template("datasource.json", settings)
    _search_rest_put(settings, f"datasources/{settings.data_source_name}", data_source_payload)

    print("Creating or updating skillset (split + embedding + projection)...")
    skillset_payload = _load_json_template("skillset.json", settings)
    for skill in skillset_payload.get("skills", []):
        if not (isinstance(skill, dict) and skill.get("@odata.type") == "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill"):
            continue

        if not settings.azure_openai_api_key:
            skill.pop("apiKey", None)

        # ada-002 rejects requests that specify dimensions, so omit model/dimension hints.
        if settings.azure_openai_embedding_model.strip().lower() == "text-embedding-ada-002":
            skill.pop("modelName", None)
            skill.pop("dimensions", None)
    _search_rest_put(settings, f"skillsets/{settings.skillset_name}", skillset_payload)

    print("Creating or updating indexer...")
    indexer_payload = _load_json_template("indexer.json", settings)
    _search_rest_put(settings, f"indexers/{settings.indexer_name}", indexer_payload)


def run_indexer_and_wait(settings: Settings, timeout_seconds: int = 1800) -> None:
    print("Running indexer...")
    try:
        _search_rest_post(settings, f"indexers/{settings.indexer_name}/run")
    except RuntimeError as ex:
        error_text = str(ex).lower()
        if "status 409" in error_text and "concurrent invocations" in error_text:
            print("Indexer is already running. Waiting for current run to complete...")
        else:
            raise

    start = time.time()
    while True:
        status = _search_rest_get(settings, f"indexers/{settings.indexer_name}/status")
        execution_state = (status.get("status") or "").lower()
        last_result = status.get("lastResult", {})
        state = (last_result.get("status") or "").lower()

        # While running, lastResult can still refer to a previous invocation.
        if execution_state in {"running", "inprogress"}:
            if time.time() - start > timeout_seconds:
                raise TimeoutError("Timed out waiting for indexer completion.")
            print("Indexer still running... waiting 15s")
            time.sleep(15)
            continue

        if state == "success":
            print("Indexer completed successfully.")
            return
        if state in {"transientfailure", "error", "failed"}:
            raise RuntimeError(f"Indexer failed: {last_result}")

        if execution_state in {"error", "failed"}:
            raise RuntimeError(f"Indexer execution state indicates failure: {status}")

        if time.time() - start > timeout_seconds:
            raise TimeoutError("Timed out waiting for indexer completion.")

        print("Indexer still running... waiting 15s")
        time.sleep(15)


def create_knowledge_source_and_base(settings: Settings) -> str:
    print("Creating or updating knowledge source and knowledge base...")
    index_client = SearchIndexClient(
        endpoint=settings.search_endpoint,
        credential=AzureKeyCredential(settings.search_admin_key),
    )

    knowledge_source_payload = _load_json_template("knowledge_source.json", settings)
    search_index_params = knowledge_source_payload["search_index_parameters"]
    source_data_fields = [
        SearchIndexFieldReference(name=field_name)
        for field_name in search_index_params.get("source_data_fields", [])
    ]

    ks = SearchIndexKnowledgeSource(
        name=knowledge_source_payload["name"],
        description=knowledge_source_payload.get("description"),
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=search_index_params["search_index_name"],
            source_data_fields=source_data_fields,
        ),
    )
    index_client.create_or_update_knowledge_source(knowledge_source=ks)

    kb = KnowledgeBase(
        name=settings.knowledge_base_name,
        knowledge_sources=[KnowledgeSourceReference(name=settings.knowledge_source_name)],
        output_mode=KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA,
        retrieval_reasoning_effort=KnowledgeRetrievalMinimalReasoningEffort(),
    )
    index_client.create_or_update_knowledge_base(knowledge_base=kb)

    return f"{settings.search_endpoint}/knowledgebases/{settings.knowledge_base_name}/mcp?api-version=2025-11-01-Preview"

def ensure_ingestion_resources(settings: Settings) -> str:
    if not _blob_container_exists(settings):
        create_blob_container(settings)

    # Keep core Search resources synchronized with local template/env configuration.
    _search_rest_head_exists(settings, f"indexes/{settings.index_name}")
    _search_rest_head_exists(settings, f"datasources/{settings.data_source_name}")
    _search_rest_head_exists(settings, f"skillsets/{settings.skillset_name}")
    _search_rest_head_exists(settings, f"indexers/{settings.indexer_name}")

    # Keep index definition in sync with local template (create or update).
    create_search_index(settings)

    # Always update these resources so model/deployment/template changes are applied.
    create_data_source_skillset_and_indexer(settings)

    index_client = SearchIndexClient(
        endpoint=settings.search_endpoint,
        credential=AzureKeyCredential(settings.search_admin_key),
    )

    knowledge_source_exists = _knowledge_source_exists(settings, index_client)
    knowledge_base_exists = _knowledge_base_exists(settings, index_client)
    if not (knowledge_source_exists and knowledge_base_exists):
        return create_knowledge_source_and_base(settings)

    return f"{settings.search_endpoint}/knowledgebases/{settings.knowledge_base_name}/mcp?api-version=2025-11-01-Preview"

def run_ingestion_pipeline(settings: Settings) -> str:
    """End-to-end ingestion entry point."""
    
    # Step 1: Ensure required Azure resources exist.
    mcp_endpoint = ensure_ingestion_resources(settings)

    # Step 2:If the index already has documents, we assume ingestion has already been done and skip to avoid duplicates.
    # TODO: In a production scenario, you would want a more robust way to determine if ingestion is needed, and to handle updates to the content over time.
    if _index_has_documents(settings):
        print("Index already contains documents. Skipping upload and indexing to avoid duplicates.")
        return mcp_endpoint

    upload_local_files_to_blob(settings)
    run_indexer_and_wait(settings, timeout_seconds=1800)
    return mcp_endpoint