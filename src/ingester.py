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

# this function returns the path where json definitions are stored
def _json_templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "input_data" / "jsons"


def _template_tokens(settings: Settings) -> dict[str, str]:
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
        "__AZURE_OPENAI_API_KEY__": settings.azure_openai_api_key or "",
    }


def _replace_tokens(value: Any, tokens: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _replace_tokens(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_tokens(item, tokens) for item in value]
    if isinstance(value, str):
        rendered = value
        for token, replacement in tokens.items():
            rendered = rendered.replace(token, replacement)
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
    try:
        _search_rest_get(settings, path, api_version=api_version)
        return True
    except requests.HTTPError as ex:
        response = getattr(ex, "response", None)
        if response is not None and response.status_code == 404:
            return False
        raise


def _search_rest_put(settings: Settings, path: str, payload: dict, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"Content-Type": "application/json", "api-key": settings.search_admin_key}
    response = requests.put(url, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    return response.json() if response.text else {}


def _search_rest_post(settings: Settings, path: str, payload: dict | None = None, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"Content-Type": "application/json", "api-key": settings.search_admin_key}
    response = requests.post(url, headers=headers, json=payload or {}, timeout=120)
    response.raise_for_status()
    return response.json() if response.text else {}


def _search_rest_get(settings: Settings, path: str, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"api-key": settings.search_admin_key}
    response = requests.get(url, headers=headers, timeout=120)
    response.raise_for_status()
    return response.json() if response.text else {}




def upload_local_files_to_knowledge_base(settings: Settings) -> None:

    # TODO: check files 
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
        
        
    print("Running indexer...")
    _search_rest_post(settings, f"indexers/{settings.indexer_name}/run")

    start = time.time()
    while True:
        status = _search_rest_get(settings, f"indexers/{settings.indexer_name}/status")
        last_result = status.get("lastResult", {})
        state = (last_result.get("status") or "").lower()

        if state == "success":
            print("Indexer completed successfully.")
            return
        if state in {"transientfailure", "error", "failed"}:
            raise RuntimeError(f"Indexer failed: {last_result}")

        if time.time() - start > timeout_seconds:
            raise TimeoutError("Timed out waiting for indexer completion.")

        print("Indexer still running... waiting 15s")
        time.sleep(15)

def ensure_ingestion_resources(settings: Settings) -> str:

    # Blob container inside a storage account
    blob_service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    container_client = blob_service.get_container_client(settings.storage_container_name)
    
    if not container_client.exists():
        print("Creating blob container...")
        blob_service_client = BlobServiceClient.from_connection_string(settings.storage_connection_string)
        container_client = blob_service_client.get_container_client(settings.storage_container_name)
        try:
            container_client.create_container()
            print(f"Blob container '{settings.storage_container_name}' created.")
        except Exception as e:
            print(f"Blob container '{settings.storage_container_name}' failed to create: {e}")
    else: print(f"Blob container '{settings.storage_container_name}' already exists.")

    # Check existence of Search resources (index, datasource, skillset, indexer)
    index_exists = _search_rest_head_exists(settings, f"indexes/{settings.index_name}")
    datasource_exists = _search_rest_head_exists(settings, f"datasources/{settings.data_source_name}")
    skillset_exists = _search_rest_head_exists(settings, f"skillsets/{settings.skillset_name}")
    indexer_exists = _search_rest_head_exists(settings, f"indexers/{settings.indexer_name}")

    # Create if missing
    if not index_exists:
        print("Creating Azure AI Search index...")
        payload = _load_json_template("index.json", settings)
        _search_rest_put(settings, f"indexes/{settings.index_name}", payload)

    if not (datasource_exists and skillset_exists and indexer_exists):
        print("Creating data source...")
        data_source_payload = _load_json_template("datasource.json", settings)
        _search_rest_put(settings, f"datasources/{settings.data_source_name}", data_source_payload)

        print("Creating skillset (split + embedding + projection)...")
        skillset_payload = _load_json_template("skillset.json", settings)
        if not settings.azure_openai_api_key:
            for skill in skillset_payload.get("skills", []):
                if isinstance(skill, dict) and skill.get("@odata.type") == "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill":
                    skill.pop("apiKey", None)
        _search_rest_put(settings, f"skillsets/{settings.skillset_name}", skillset_payload)

        print("Creating indexer...")
        indexer_payload = _load_json_template("indexer.json", settings)
        _search_rest_put(settings, f"indexers/{settings.indexer_name}", indexer_payload)

    # Index client is used for knowledge base/resource management which is not yet supported in Search REST API
    index_client = SearchIndexClient(
        endpoint=settings.search_endpoint,
        credential=AzureKeyCredential(settings.search_admin_key),
    )

    try:
        index_client.get_knowledge_source(settings.knowledge_source_name)
        knowledge_source = True
    except HttpResponseError as ex:
        if ex.status_code == 404:
            knowledge_source =  False

    try:
        index_client.get_knowledge_base(settings.knowledge_base_name)
        knowledge_base = True
    except HttpResponseError as ex:
        if ex.status_code == 404:
            knowledge_base = False
        else:
            raise

    if not (knowledge_source and knowledge_base):
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

    return f"{settings.search_endpoint}/knowledgebases/{settings.knowledge_base_name}/mcp?api-version=2025-11-01-Preview"


def ingestion(settings: Settings) -> str:
    """End-to-end ingestion entry point.

    1) Ensure required Azure resources exist.
    2) Upload and index local content that is nor already part of the knowledge base
    3) Return MCP endpoint for the created/existing knowledge base.
    """
    mcp_endpoint = ensure_ingestion_resources(settings)
    upload_local_files_to_knowledge_base(settings)
    return mcp_endpoint