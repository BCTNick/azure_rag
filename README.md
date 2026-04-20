# Azure RAG Pipeline (Direct Azure OpenAI + Azure AI Search)

This repository implements an end-to-end Retrieval Augmented Generation (RAG) pipeline on Azure.
It ingests local documents into Azure Blob Storage and Azure AI Search, enriches content with an Azure OpenAI embedding skill, and serves interactive terminal chat backed by retrieval.

Core code paths:

- `main.py`: startup, environment loading, strict settings construction, orchestration.
- `src/ingester.py`: ingestion pipeline and Azure resource provisioning/synchronization.
- `src/chat.py`: direct key-based retrieval + generation chat loop.
- `input_data/jsons/*.json`: Search resource templates (index, datasource, skillset, indexer, knowledge source).

## Requirements

Python 3.10+ is recommended.

Install dependencies:

```bash
pip install -r requirements.txt
```

## All Environment Variables Are Required

Yes. In the current implementation, all runtime environment variables below are required for successful startup and execution.

### Search configuration

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_ADMIN_KEY`
- `AZURE_SEARCH_INDEX_NAME`
- `AZURE_SEARCH_DATASOURCE_NAME`
- `AZURE_SEARCH_SKILLSET_NAME`
- `AZURE_SEARCH_INDEXER_NAME`
- `AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME`
- `AZURE_SEARCH_KNOWLEDGE_BASE_NAME`

### Azure OpenAI configuration

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_EMBEDDING_MODEL`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `AZURE_OPENAI_API_KEY`

### Storage configuration

- `AZURE_STORAGE_ACCOUNT_NAME`
- `AZURE_STORAGE_ACCOUNT_KEY`
- `AZURE_STORAGE_CONTAINER_NAME`

### Local input and execution flags

- `LOCAL_STORAGE`
- `RUN_INGESTION`

Notes:

- `RUN_INGESTION` controls whether ingestion runs before chat. Use values like `true` or `false`.
- `AZURE_OPENAI_ENDPOINT` should be provided in the same format expected by current code in `src/chat.py`.

## Azure Pipeline Explained

This project has two connected flows: ingestion and chat.

### 1) Ingestion flow (data preparation)

Entry point: `run_ingestion_pipeline(settings)` in `src/ingester.py`.

Step-by-step:

1. Ensure base storage is available.
	- Checks whether the Azure Blob container exists.
	- Creates it if missing.

2. Ensure Search resources are synchronized with local templates.
	- Index (`input_data/jsons/index.json`)
	- Data source (`input_data/jsons/datasource.json`)
	- Skillset (`input_data/jsons/skillset.json`)
	- Indexer (`input_data/jsons/indexer.json`)
	- Knowledge source (`input_data/jsons/knowledge_source.json`)

3. Token replacement is applied to templates.
	- Placeholders such as `__INDEX_NAME__` and `__AZURE_OPENAI_EMBEDDING_DEPLOYMENT__` are replaced from `Settings`.

4. Search index lifecycle is handled safely.
	- If the index schema changed incompatibly and the index is empty, it can be recreated.
	- If knowledge artifacts block deletion, the pipeline removes blocking knowledge resources and retries.

5. Skillset embedding behavior is model-aware.
	- For `text-embedding-ada-002`, model/dimension hints are removed from skill payload to avoid service-side incompatibilities.

6. Upload local files to Blob Storage.
	- Reads all files recursively from `LOCAL_STORAGE`.
	- Uploads blobs into `AZURE_STORAGE_CONTAINER_NAME`.

7. Run indexer and wait for completion.
	- Starts the indexer and polls status.
	- Handles already-running indexer scenarios and waits until terminal state.

8. Provision knowledge source and knowledge base.
	- Uses Azure Search SDK for knowledge artifacts.
	- Returns an MCP endpoint string for the created knowledge base.

### 2) Chat flow (runtime Q&A)

Entry point: `chat_in_terminal(settings)` in `src/chat.py`.

Step-by-step:

1. Receive user question in terminal.
2. Create query embedding using Azure OpenAI embeddings deployment.
3. Execute hybrid retrieval in Azure AI Search using:
	- `search_text` (keyword/semantic-text side)
	- `VectorizedQuery` on the `embedding` field (vector side)
4. Build augmented prompt with retrieved chunks.
5. Call Azure OpenAI chat completion deployment.
6. Print answer and keep short conversation history.

Commands in terminal chat:

- `exit`, `quit`, `q`: stop chat.
- `clear`: clear in-memory chat history.

## How Azure Services Connect

The integration graph is:

1. Local files -> Azure Blob Storage.
2. Azure AI Search data source points to Blob Storage.
3. Azure AI Search indexer reads blobs via the data source.
4. Skillset runs chunking + Azure OpenAI embedding skill during indexing.
5. Enriched chunks are written into the Azure AI Search index.
6. Chat runtime creates a new query embedding with Azure OpenAI.
7. Azure AI Search returns relevant chunks.
8. Azure OpenAI chat deployment generates final grounded answer.

This creates a closed Azure-native RAG loop: index-time enrichment + query-time retrieval + grounded generation.

## Run

```bash
python main.py
```

Runtime behavior:

- If `RUN_INGESTION=true`, it executes ingestion first, then starts chat.
- If `RUN_INGESTION=false`, it skips ingestion and starts chat directly.

## Operational Notes

- Resource synchronization is performed on each ingestion run to keep Azure resources aligned with local templates.
- Resource provisioning/update for index, datasource, skillset, and indexer is done with Search REST APIs.
- Knowledge source/base operations are done via `SearchIndexClient` SDK methods.
- The code is direct key-based for Azure OpenAI and does not depend on Azure CLI authentication.
