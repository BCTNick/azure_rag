import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    KnowledgeBase,
    KnowledgeRetrievalMinimalReasoningEffort,
    KnowledgeRetrievalOutputMode,
    KnowledgeSourceReference,
    SearchField,
    SearchIndex,
    SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv


@dataclass
class Settings:
    search_endpoint: str
    search_admin_key: str
    index_name: str
    data_source_name: str
    skillset_name: str
    indexer_name: str
    knowledge_source_name: str
    knowledge_base_name: str
    azure_openai_endpoint: str
    azure_openai_embedding_deployment: str
    azure_openai_embedding_model: str
    azure_openai_api_key: str | None
    storage_connection_string: str
    storage_container_name: str
    project_endpoint: str
    project_resource_id: str
    project_connection_name: str
    agent_name: str
    agent_model: str
    local_blob_folder: Path


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    load_dotenv()
    root = Path(__file__).resolve().parent
    return Settings(
        search_endpoint=_require_env("AZURE_SEARCH_ENDPOINT").rstrip("/"),
        search_admin_key=_require_env("AZURE_SEARCH_ADMIN_KEY"),
        index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "eiopa-rag-index"),
        data_source_name=os.getenv("AZURE_SEARCH_DATASOURCE_NAME", "eiopa-rag-datasource"),
        skillset_name=os.getenv("AZURE_SEARCH_SKILLSET_NAME", "eiopa-rag-skillset"),
        indexer_name=os.getenv("AZURE_SEARCH_INDEXER_NAME", "eiopa-rag-indexer"),
        knowledge_source_name=os.getenv("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME", "eiopa-rag-ks"),
        knowledge_base_name=os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "eiopa-rag-kb"),
        azure_openai_endpoint=_require_env("AZURE_OPENAI_ENDPOINT").rstrip("/"),
        azure_openai_embedding_deployment=_require_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_openai_embedding_model=os.getenv("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        storage_connection_string=_require_env("AZURE_STORAGE_CONNECTION_STRING"),
        storage_container_name=os.getenv("AZURE_STORAGE_CONTAINER_NAME", "blob-container"),
        project_endpoint=_require_env("PROJECT_ENDPOINT"),
        project_resource_id=_require_env("PROJECT_RESOURCE_ID"),
        project_connection_name=os.getenv("PROJECT_CONNECTION_NAME", "search-kb-mcp"),
        agent_name=os.getenv("AGENT_NAME", "eiopa-rag-agent"),
        agent_model=os.getenv("AGENT_MODEL", "gpt-4.1-mini"),
        local_blob_folder=Path(os.getenv("LOCAL_BLOB_FOLDER", str(root / "data" / "blob_container"))),
    )

def main() -> None:
    settings = load_settings()
    credential = DefaultAzureCredential()

    upload_local_files_to_blob(settings)
    create_search_index(settings)
    create_data_source_skillset_and_indexer(settings)
    run_indexer_and_wait(settings)

    mcp_endpoint = create_knowledge_source_and_base(settings)

    project_client = AIProjectClient(endpoint=settings.project_endpoint, credential=credential)
    create_foundry_connection(settings, credential, mcp_endpoint)
    agent = create_or_update_agent(settings, project_client, mcp_endpoint)

    print(
        "Pipeline ready. Indexed data from local folder to Search, created knowledge base, "
        "and connected Foundry agent."
    )
    chat_in_terminal(project_client, agent)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(1)
    except Exception as ex:
        print(f"ERROR: {ex}")
        sys.exit(1)