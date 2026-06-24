from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    local_storage: Path
    azure_storage_account_name: str
    azure_container_name: str
    azure_container_sas_token: str
    azure_container_sas_url: str
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
    azure_openai_api_key: str


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}.") from None


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        local_storage=Path(_env("LOCAL_STORAGE", "input_data/local_storage")),
        azure_storage_account_name=_env("AZURE_STORAGE_ACCOUNT_NAME"),
        azure_container_name=_env("AZURE_CONTAINER_NAME"),
        azure_container_sas_token=_env("AZURE_CONTAINER_SAS_TOKEN"),
        azure_container_sas_url=_env("AZURE_CONTAINER_SAS_URL"),
        search_endpoint=_env("AZURE_SEARCH_ENDPOINT").rstrip("/"),
        search_admin_key=_env("AZURE_SEARCH_ADMIN_KEY"),
        index_name=_env("AZURE_SEARCH_INDEX_NAME"),
        data_source_name=_env("AZURE_SEARCH_DATASOURCE_NAME"),
        skillset_name=_env("AZURE_SEARCH_SKILLSET_NAME"),
        indexer_name=_env("AZURE_SEARCH_INDEXER_NAME"),
        knowledge_source_name=_env("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME"),
        knowledge_base_name=_env("AZURE_SEARCH_KNOWLEDGE_BASE_NAME"),
        azure_openai_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        azure_openai_embedding_deployment=_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_openai_embedding_model=_env("AZURE_OPENAI_EMBEDDING_MODEL"),
        azure_openai_embedding_dimensions=_env_int("AZURE_OPENAI_EMBEDDING_DIMENSIONS", 1536),
        azure_openai_chat_deployment=_env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        azure_openai_api_key=_env("AZURE_OPENAI_API_KEY"),
    )
