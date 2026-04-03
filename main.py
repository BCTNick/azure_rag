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


def _normalize_azure_openai_endpoint(raw_endpoint: str) -> str:
    endpoint = raw_endpoint.strip().rstrip("/")
    lower = endpoint.lower()
    if lower.endswith("/openai/v1"):
        return endpoint[:-10]
    if lower.endswith("/openai"):
        return endpoint[:-7]
    return endpoint


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_storage_connection_string() -> str:
    """Returns storage connection string from either direct value or account credentials."""
    direct = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if direct:
        return direct

    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
    if account_name and account_key:
        protocol = os.getenv("AZURE_STORAGE_PROTOCOL", "https")
        endpoint_suffix = os.getenv("AZURE_STORAGE_ENDPOINT_SUFFIX", "core.windows.net")
        return (
            f"DefaultEndpointsProtocol={protocol};"
            f"AccountName={account_name};"
            f"AccountKey={account_key};"
            f"EndpointSuffix={endpoint_suffix}"
        )

    raise ValueError(
        "Missing storage configuration: set AZURE_STORAGE_CONNECTION_STRING "
        "or both AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY"
    )


def _resolve_embedding_dimensions() -> int:
    # Hardcoded to ADA-compatible embedding width.
    return 1536


def _validate_embedding_configuration(deployment_name: str, model_name: str) -> None:
    deployment = deployment_name.strip().lower()
    model = model_name.strip().lower()
    if "ada-002" in deployment and model != "text-embedding-ada-002":
        raise ValueError(
            "Embedding deployment/model mismatch: deployment appears to be 'text-embedding-ada-002' "
            "but AZURE_OPENAI_EMBEDDING_MODEL is not 'text-embedding-ada-002'. "
            "Use a text-embedding-3-* deployment or update model/dimensions to ada-002-compatible values."
        )

def main() -> None:
    # Load settings from environment variables
    load_dotenv()
    root = Path(__file__).resolve().parent
    run_ingestion = _as_bool(os.getenv("RUN_INGESTION"), default=True)
    embedding_deployment = _require_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    _validate_embedding_configuration(embedding_deployment, embedding_model)
    settings = Settings(
        search_endpoint=_require_env("AZURE_SEARCH_ENDPOINT").rstrip("/"),
        search_admin_key=_require_env("AZURE_SEARCH_ADMIN_KEY"),
        index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "eiopa-rag-index"),
        data_source_name=os.getenv("AZURE_SEARCH_DATASOURCE_NAME", "eiopa-rag-datasource"),
        skillset_name=os.getenv("AZURE_SEARCH_SKILLSET_NAME", "eiopa-rag-skillset"),
        indexer_name=os.getenv("AZURE_SEARCH_INDEXER_NAME", "eiopa-rag-indexer"),
        knowledge_source_name=os.getenv("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME", "eiopa-rag-ks"),
        knowledge_base_name=os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "eiopa-rag-kb"),
        azure_openai_endpoint=_normalize_azure_openai_endpoint(_require_env("AZURE_OPENAI_ENDPOINT")),
        azure_openai_embedding_deployment=embedding_deployment,
        azure_openai_embedding_model=embedding_model,
        azure_openai_embedding_dimensions=_resolve_embedding_dimensions(),
        azure_openai_chat_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4.1-mini"),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        storage_connection_string=_resolve_storage_connection_string(),
        storage_container_name=os.getenv("AZURE_STORAGE_CONTAINER_NAME", "blob-container"),
        local_storage=Path(os.getenv("LOCAL_STORAGE", str(root / "data" / "blob_container"))),
    )

    # If enabled in the environment variables, run the ingestion pipeline
    if run_ingestion:
        run_ingestion_pipeline(settings)

    print("Pipeline ready. Starting direct key-based chat mode.")
    chat_in_terminal(settings)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(1)
    except Exception as ex:
        print(f"ERROR: {ex}")
        sys.exit(1)