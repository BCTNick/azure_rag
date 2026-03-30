# Microsoft Foundry RAG Pipeline

This repository provides an Azure AI Search + Foundry Agent RAG workflow.
The pipeline is orchestrated from `main.py` and ingestion logic is centralized in `src/ingester.py`.

## Architecture Overview

The current flow uses a single ingestion entry point:

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

## What `main.py` Does

`main.py`:

1. Loads environment configuration into `Settings`
2. Optionally runs ingestion (`run_ingestion=True`)
3. Builds MCP connection in Foundry
4. Creates/updates the Foundry agent
5. Starts interactive terminal chat

Important runtime flag behavior:

- `run_ingestion=False` (default): skips ingestion and assumes resources already exist
- `run_ingestion=True`: executes the full ingestion pipeline first

## Requirements

Dependencies are pinned in `requirements.txt`:

- `azure-ai-projects==2.0.0b1`
- `azure-search-documents==11.7.0b2`
- `azure-identity`
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
- `AZURE_STORAGE_CONNECTION_STRING`
- `PROJECT_ENDPOINT`
- `PROJECT_RESOURCE_ID`

Common optional overrides (defaults are defined in `main.py`):

- `AZURE_SEARCH_INDEX_NAME`
- `AZURE_SEARCH_DATASOURCE_NAME`
- `AZURE_SEARCH_SKILLSET_NAME`
- `AZURE_SEARCH_INDEXER_NAME`
- `AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME`
- `AZURE_SEARCH_KNOWLEDGE_BASE_NAME`
- `AZURE_OPENAI_EMBEDDING_MODEL`
- `AZURE_OPENAI_API_KEY`
- `AZURE_STORAGE_CONTAINER_NAME`
- `PROJECT_CONNECTION_NAME`
- `AGENT_NAME`
- `AGENT_MODEL`
- `LOCAL_STORAGE`

## Run

```bash
python main.py
```

By default, ingestion is off in the current `__main__` block.
If you want to ingest data before chat, set `run_ingestion = True` in `main.py`.

## Notes

- Index, datasource, skillset, and indexer are managed with Search REST calls.
- Knowledge source and knowledge base are managed through `SearchIndexClient` SDK methods.
- Uploaded files are read from `LOCAL_STORAGE` (default: `data/blob_container`).
