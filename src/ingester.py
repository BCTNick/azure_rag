from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
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
from azure.storage.blob import ContainerClient

from src.utils import (
    load_json_template,
    search_rest_get,
    search_rest_post,
    search_rest_put,
)

if TYPE_CHECKING:
    from src.config import Settings


class Ingester:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure_ingestion_resources(self):

        settings = self.settings

        # Build template tokens for all Search resources we manage.
        tokens: dict[str, Any] = {
            "__INDEX_NAME__": settings.index_name,
            "__DATASOURCE_NAME__": settings.data_source_name,
            "__SKILLSET_NAME__": settings.skillset_name,
            "__INDEXER_NAME__": settings.indexer_name,
            "__KNOWLEDGE_SOURCE_NAME__": settings.knowledge_source_name,
            "__STORAGE_CONNECTION_STRING__": f"BlobEndpoint=https://{settings.azure_storage_account_name}.blob.core.windows.net/;SharedAccessSignature={settings.azure_container_sas_token}",
            "__STORAGE_CONTAINER_NAME__": settings.azure_container_name,
            "__AZURE_OPENAI_ENDPOINT__": settings.azure_openai_endpoint[: -len("/openai/v1")],
            "__AZURE_OPENAI_EMBEDDING_DEPLOYMENT__": settings.azure_openai_embedding_deployment,
            "__AZURE_OPENAI_EMBEDDING_MODEL__": settings.azure_openai_embedding_model,
            "__AZURE_OPENAI_EMBEDDING_DIMENSIONS__": settings.azure_openai_embedding_dimensions,
            "__AZURE_OPENAI_API_KEY__": settings.azure_openai_api_key or "",
            "__CUSTOM_CHUNKER_URI__": (os.getenv("CUSTOM_CHUNKER_URI") or "https://localhost:8000/chunk").strip(),
        }

        # Confirm Search control-plane reachability and key validity up front.
        try:
            search_rest_get(settings.search_endpoint, settings.search_admin_key, "indexes")
        except RuntimeError as ex:
            raise RuntimeError(
                "Cannot access Azure AI Search with the configured endpoint/key. "
                "Check AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_ADMIN_KEY."
            ) from ex

        # check if index exists and has the definition that we want, otherwise create it
        try:
            existing_index = search_rest_get(settings.search_endpoint, settings.search_admin_key, f"indexes/{settings.index_name}")
            print(f"Index '{settings.index_name}' exists. Validating operability with update call...")
            expected_index = load_json_template("index.json", tokens)
            if json.dumps(existing_index.get("fields", []), sort_keys=True) != json.dumps(expected_index.get("fields", []), sort_keys=True):
                raise RuntimeError("Existing index schema does not match index.json (fields differ).")
        except RuntimeError as ex:
            if "status 404" in str(ex).lower():
                print(f"Index '{settings.index_name}' not found. It will be created.")
                print("Creating or updating Azure AI Search index...")

            index_payload = load_json_template("index.json", tokens)
            search_rest_put(settings.search_endpoint, settings.search_admin_key, f"indexes/{settings.index_name}", index_payload)

        
        # check datasource
        datasource_exists = False

        try:
            search_rest_get(settings.search_endpoint, settings.search_admin_key, f"datasources/{settings.data_source_name}")
            datasource_exists = True
            print(f"Datasource '{settings.data_source_name}' exists. Validating operability with update call...")
        except RuntimeError as ex:
            if "status 404" in str(ex).lower():
                print(f"Datasource '{settings.data_source_name}' not found. It will be created.")
            else:
                raise
        
        print("Creating or updating data source...")
        data_source_payload = load_json_template("datasource.json", tokens)
        search_rest_put(
            settings.search_endpoint,
            settings.search_admin_key,
            f"datasources/{settings.data_source_name}",
            data_source_payload,
        )
        if not datasource_exists:
            print(f"Datasource '{settings.data_source_name}' created.")
        else:
            print(f"Datasource '{settings.data_source_name}' updated and reachable.")


        # check skillset

        skillset_exists = False
        try:
            search_rest_get(settings.search_endpoint, settings.search_admin_key, f"skillsets/{settings.skillset_name}")
            skillset_exists = True
            print(f"Skillset '{settings.skillset_name}' exists. Validating operability with update call...")
        except RuntimeError as ex:
            if "status 404" in str(ex).lower():
                print(f"Skillset '{settings.skillset_name}' not found. It will be created.")
            else:
                raise
        print("Creating or updating skillset (custom chunker + embedding)...")
        skillset_payload = load_json_template("skillset.json", tokens)
        for skill in skillset_payload.get("skills", []):
            if not (isinstance(skill, dict) and skill.get("@odata.type") == "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill"):
                continue
            if not settings.azure_openai_api_key:
                skill.pop("apiKey", None)
            if settings.azure_openai_embedding_model.strip().lower() == "text-embedding-ada-002":
                skill.pop("dimensions", None)
        search_rest_put(settings.search_endpoint, settings.search_admin_key, f"skillsets/{settings.skillset_name}", skillset_payload)
        if not skillset_exists:
            print(f"Skillset '{settings.skillset_name}' created.")
        else:
            print(f"Skillset '{settings.skillset_name}' updated and reachable.")

        #check indexer
        indexer_exists = False
        try:
            search_rest_get(settings.search_endpoint, settings.search_admin_key, f"indexers/{settings.indexer_name}")
            indexer_exists = True
            print(f"Indexer '{settings.indexer_name}' exists. Validating operability with update call...")
        except RuntimeError as ex:
            if "status 404" in str(ex).lower():
                print(f"Indexer '{settings.indexer_name}' not found. It will be created.")
            else:
                raise

        print("Creating or updating indexer...")
        indexer_payload = load_json_template("indexer.json", tokens)
        search_rest_put(settings.search_endpoint, settings.search_admin_key, f"indexers/{settings.indexer_name}", indexer_payload)
        if not indexer_exists:
            print(f"Indexer '{settings.indexer_name}' created.")
        else:
            print(f"Indexer '{settings.indexer_name}' updated and reachable.")

        index_client = SearchIndexClient(
            endpoint=settings.search_endpoint,
            credential=AzureKeyCredential(settings.search_admin_key),
        )
        try:
            index_client.get_knowledge_source(settings.knowledge_source_name)
            knowledge_source_exists = True
        except HttpResponseError as ex:
            if ex.status_code != 404:
                raise
            knowledge_source_exists = False
                
        # check knowledge source and knowledge base
        try:
            index_client.get_knowledge_base(settings.knowledge_base_name)
            knowledge_base_exists = True
        except HttpResponseError as ex:
            if ex.status_code != 404:
                raise
            knowledge_base_exists = False
            
        if not (knowledge_source_exists and knowledge_base_exists):
            print("Creating or updating knowledge source and knowledge base...")
            knowledge_source_payload = load_json_template("knowledge_source.json", tokens)
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

    def upload_local_files_to_blob(self) -> None:
        settings = self.settings

        # check if local storage folder exists and has files
        if not settings.local_storage.exists():
            raise FileNotFoundError(f"Local folder not found: {settings.local_storage}")
        files = [p for p in settings.local_storage.rglob("*") if p.is_file()]
        if not files:
            raise ValueError(f"No files found in {settings.local_storage}")

        # Build blob names from relative paths so local subfolders are mirrored as blob prefixes.
        local_files = {
            str(path.relative_to(settings.local_storage)).replace("\\", "/"): path
            for path in files
        }

        # mirror the local files to the blob container with overwrite
        container_client = ContainerClient.from_container_url(settings.azure_container_sas_url)

        print("Mirroring local files (including subfolders) to Azure Blob container...")
        can_list = True
        try:
            existing_blobs = {blob.name: blob for blob in container_client.list_blobs()}
        except (ClientAuthenticationError, HttpResponseError):
            existing_blobs = {}
            can_list = False
            print("SAS does not allow list operations. Uploading all local files with overwrite=True fallback.")

        if can_list:
            removed_blob_names = sorted(name for name in existing_blobs if name not in local_files)
            for blob_name in removed_blob_names:
                try:
                    container_client.delete_blob(blob_name)
                    print(f"Deleted from blob: {blob_name}")
                except (ClientAuthenticationError, HttpResponseError) as ex:
                    raise RuntimeError(
                        "Blob delete not authorized by SAS while mirroring. Ensure SAS grants delete permission "
                        "(for example, include 'd') or disable strict mirror deletion."
                    ) from ex

        for blob_name, path in local_files.items():
            should_upload = True
            if can_list and blob_name in existing_blobs:
                remote_size = getattr(existing_blobs[blob_name], "size", None)
                local_size = path.stat().st_size
                if remote_size == local_size:
                    should_upload = False

            if not should_upload:
                continue

            with path.open("rb") as data:
                try:
                    container_client.upload_blob(name=blob_name, data=data, overwrite=True)
                except (ClientAuthenticationError, HttpResponseError) as ex:
                    raise RuntimeError(
                        "Blob upload not authorized by SAS. Ensure SAS grants create/write permissions "
                        "for the target container (for example, include 'c' and 'w')."
                    ) from ex
            print(f"Uploaded: {blob_name}")


    def index_documents(self, timeout_seconds: int = 360) -> None:
        settings = self.settings
        pre_status = search_rest_get(settings.search_endpoint, settings.search_admin_key, f"indexers/{settings.indexer_name}/status")
        pre_history = pre_status.get("executionHistory") or []
        pre_latest = pre_history[0] if pre_history else {}
        baseline_marker = pre_latest.get("startTime") or (pre_status.get("lastResult") or {}).get("startTime") or ""

        print("Running indexer...")
        run_mode = "triggered"
        try:
            search_rest_post(settings.search_endpoint, settings.search_admin_key, f"indexers/{settings.indexer_name}/run")
        except RuntimeError as ex:
            error_text = str(ex).lower()
            if "status 409" in error_text and "concurrent invocations" in error_text:
                run_mode = "already-running"
                print("Indexer is already running. Waiting for current run to complete...")
            elif "status 429" in error_text and "on-demand indexer invocation" in error_text:
                run_mode = "rate-limited"
                print("Indexer run is rate-limited on this tier. Waiting for current/scheduled run status...")
            else:
                raise

        start = time.time()
        poll_count = 0
        while True:
            status = search_rest_get(settings.search_endpoint, settings.search_admin_key, f"indexers/{settings.indexer_name}/status")
            execution_state = (status.get("status") or "").lower()
            last_result = status.get("lastResult", {})
            last_result_state = (last_result.get("status") or "").lower()
            execution_history = status.get("executionHistory") or []
            latest_history = execution_history[0] if execution_history else {}
            latest_history_state = (latest_history.get("status") or "").lower()
            current_marker = latest_history.get("startTime") or last_result.get("startTime") or ""
            has_new_execution = bool(current_marker and current_marker != baseline_marker)

            if latest_history_state == "success" or last_result_state == "success":
                if run_mode == "triggered" and not has_new_execution:
                    print("Latest reported indexer result is successful (no newer execution marker detected).")
                elif run_mode == "rate-limited" and not has_new_execution:
                    print("On-demand run not started due rate limit; latest completed indexer run is successful.")
                print("Indexer completed successfully.")
                return

            if execution_state in {"running", "inprogress"}:
                if time.time() - start > timeout_seconds:
                    raise TimeoutError("Timed out waiting for indexer completion.")
                poll_count += 1
                if poll_count % 3 == 0:
                    print(
                        "Indexer still running... "
                        f"execution_state={execution_state}, "
                        f"latest_history_status={latest_history_state or 'n/a'}, "
                        f"last_result_status={last_result_state or 'n/a'}. waiting 10s"
                    )
                else:
                    print("Indexer still running... waiting 10s")
                time.sleep(10)
                continue

            if latest_history_state in {"transientfailure", "error", "failed"}:
                details = (
                    latest_history.get("errorMessage")
                    or latest_history.get("message")
                    or latest_history.get("errors")
                    or json.dumps(latest_history)
                )
                raise RuntimeError(f"Indexer failed (execution history): {details}")

            if last_result_state in {"transientfailure", "error", "failed"}:
                details = (
                    last_result.get("errorMessage")
                    or last_result.get("message")
                    or last_result.get("errors")
                    or json.dumps(last_result)
                )
                raise RuntimeError(f"Indexer failed (last result): {details}")

            if execution_state in {"error", "failed"}:
                payload = latest_history or last_result
                details = payload.get("errorMessage") or payload.get("message") or payload.get("errors") or json.dumps(payload)
                raise RuntimeError(f"Indexer execution state indicates failure: {details}")

            if time.time() - start > timeout_seconds:
                raise TimeoutError("Timed out waiting for indexer completion.")

            print("Indexer still running... waiting 10s")
            time.sleep(10)


    def run(self) -> None:
        """End-to-end ingestion entry point."""

        # Step 1: Ensure required Azure resources exist.
        self.ensure_ingestion_resources()

        # Step 2: Upload new local files that are not already in the blob container.
        self.upload_local_files_to_blob()

        # Step 3: Run indexer to process uploaded documents.
        self.index_documents(timeout_seconds=1800)

