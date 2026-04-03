# Direct RAG Pipeline

This repository provides a direct Azure AI Search + Azure OpenAI RAG workflow.
The pipeline is orchestrated from `main.py`, with ingestion logic in `src/ingester.py` and direct chat logic in `src/chat.py`.

## Architecture Overview

The current flow uses one ingestion entry point:

1. `ingestion(settings)` in `src/ingester.py`
2. `ensure_ingestion_resources(settings)` validates/creates required Azure resources
3. `upload_local_files_to_knowledge_base(settings)` uploads local files and runs the indexer
4. Returns the MCP endpoint for the knowledge base

Resource definitions are loaded from JSON templates under `input_data/jsons/`:

- `index.json`
- `datasource.json`
- `skillset.json`
- `indexer.json`
- `knowledge_source.json`

Template placeholders (for example `__INDEX_NAME__`) are replaced at runtime using values from environment settings.

## What main.py Does

main.py:

1. Loads environment configuration into `Settings`
2. Runs ingestion by default
3. Starts direct key-based chat in terminal (Azure OpenAI + Azure Search)

Runtime flag behavior:

- `RUN_INGESTION=true` (default): executes full ingestion first
- `RUN_INGESTION=false`: skips ingestion and assumes resources already exist

## Requirements

Dependencies are pinned in `requirements.txt`:

- `azure-search-documents==11.7.0b2`
- `azure-storage-blob`
- `python-dotenv`
- `requests`

Install with:

```bash
pip install -r requirements.txt
```

## Required Environment Variables

Minimum required values:

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_ADMIN_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_STORAGE_CONNECTION_STRING` or both `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY`

Common optional overrides (defaults are defined in `main.py`):

- `AZURE_SEARCH_INDEX_NAME`
- `AZURE_SEARCH_DATASOURCE_NAME`
- `AZURE_SEARCH_SKILLSET_NAME`
- `AZURE_SEARCH_INDEXER_NAME`
- `AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME`
- `AZURE_SEARCH_KNOWLEDGE_BASE_NAME`
- `AZURE_OPENAI_EMBEDDING_MODEL`
- `AZURE_STORAGE_CONTAINER_NAME`
- `AZURE_STORAGE_PROTOCOL` (default: `https`)
- `AZURE_STORAGE_ENDPOINT_SUFFIX` (default: `core.windows.net`)
- `LOCAL_STORAGE`

## Run

```bash
python main.py
```

Ingestion runs by default. To skip it:

```bash
set RUN_INGESTION=false
python main.py
```

## Notes

- Index, datasource, skillset, and indexer are managed with Search REST calls.
- Knowledge source and knowledge base are managed through `SearchIndexClient` SDK methods.
- Uploaded files are read from `LOCAL_STORAGE` (default: `data/blob_container`).
- Terminal chat is direct key-based (no Azure CLI required).
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` and `AZURE_OPENAI_EMBEDDING_MODEL` must refer to the same model family.
- Embedding dimensions are currently hardcoded to `1536` in the pipeline settings.
