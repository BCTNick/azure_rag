import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from src.ingester import run_ingestion_pipeline
from src.chat import chat_in_terminal


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
    azure_openai_embedding_dimensions: int
    azure_openai_chat_deployment: str
    azure_openai_api_key: str | None
    storage_connection_string: str
    storage_container_name: str
    local_storage: Path


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value

def _resolve_storage_connection_string() -> str:

    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
    if account_name and account_key:
        return (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={account_name};"
            f"AccountKey={account_key};"
            f"EndpointSuffix=core.windows.net"
        )

    raise ValueError(
        "Missing storage configuration: set AZURE_STORAGE_CONNECTION_STRING "
        "or both AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY"
    )

if __name__ == "__main__":
    try:
        # Load settings from environment variables
        load_dotenv()
        settings = Settings(
            search_endpoint=_require_env("AZURE_SEARCH_ENDPOINT").rstrip("/"),
            search_admin_key=_require_env("AZURE_SEARCH_ADMIN_KEY"),
            index_name=_require_env("AZURE_SEARCH_INDEX_NAME"),
            data_source_name=_require_env("AZURE_SEARCH_DATASOURCE_NAME"),
            skillset_name=_require_env("AZURE_SEARCH_SKILLSET_NAME"),
            indexer_name=_require_env("AZURE_SEARCH_INDEXER_NAME"),
            knowledge_source_name=_require_env("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME"),
            knowledge_base_name=_require_env("AZURE_SEARCH_KNOWLEDGE_BASE_NAME"),
            azure_openai_endpoint=_require_env("AZURE_OPENAI_ENDPOINT"),
            azure_openai_embedding_deployment= _require_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"), #TODO: remove one between model and deployment 
            azure_openai_embedding_model=_require_env("AZURE_OPENAI_EMBEDDING_MODEL"),
            azure_openai_embedding_dimensions=1536, #TODO: get the dimension from model name
            azure_openai_chat_deployment=_require_env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
            azure_openai_api_key=_require_env("AZURE_OPENAI_API_KEY"),
            storage_connection_string=_resolve_storage_connection_string(),
            storage_container_name=_require_env("AZURE_STORAGE_CONTAINER_NAME"),
            local_storage=Path(_require_env("LOCAL_STORAGE")),
        )

        # If enabled in the environment variables, run the ingestion pipeline
        if os.getenv("RUN_INGESTION").strip().lower() in {"1", "true", "TRUE", "True", "yes", "y", "on"}:
            run_ingestion_pipeline(settings)

        print("Pipeline ready. Starting direct key-based chat mode.")
        chat_in_terminal(settings)

    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(1)
    except Exception as ex:
        print(f"ERROR: {ex}")
        sys.exit(1)