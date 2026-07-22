# Azure Legal RAG for DORA Q&A

This repository contains a legal retrieval-augmented generation (RAG) prototype for answering DORA-related regulatory questions. The system is designed around Azure AI Search, Azure OpenAI / Azure AI Foundry, and a Teams-ready FastAPI service.

The project supports three main workflows:

- Build an indexed legal knowledge base from DORA regulations, technical standards, guidelines, opinions, and related public supervisory materials.
- Ask grounded legal questions through a terminal chat, HTTP API, or Microsoft Teams bot endpoint.
- Evaluate the RAG against official EIOPA/EBA Q&A benchmark answers and compare it with NotebookLM answers.

> This is a research prototype for legal information retrieval and grounded answer generation. It is not a legal advice system and its answers should be reviewed against the cited sources.

## Features

- Legal-structure-aware chunking for PDF and EUR-Lex HTML/XML-style files.
- Row-preserving parsing for Excel workbooks.
- Metadata-rich Azure AI Search index with `doc_name`, `source_type`, `page_row_num`, `chapter_num`, `article_num`, and `annex_num`.
- Hybrid lexical + vector retrieval.
- Query profiling for complexity, expertise, specificity, and explicit legal references.
- Step-back query generation and query decomposition for complex questions.
- Reciprocal-rank fusion, legal-aware heuristic reranking, and MMR-style context diversification.
- Grounded answer generation with citations and trace IDs.
- Teams-compatible Bot Framework endpoint.
- RAG evaluation script with answer metrics and retrieval metrics.
- NotebookLM answer evaluation script using the same answer-level judge rubric.

## Repository Structure

```text
azure_rag/
  app.py                         # FastAPI API + Bot Framework endpoint for Teams
  chat_terminal.py               # Local terminal chat with trace logging
  chunk_preview.py               # Chunk documents locally without uploading to Azure
  ingest_local.py                # Direct local ingestion into Azure AI Search
  ingest_indexer.py              # Optional Azure Blob + Search indexer ingestion path
  evaluate_rag.py                # RAG benchmark evaluation
  evaluate_notebooklm.py         # NotebookLM answer evaluation
  Dockerfile
  requirements.txt
  .env.example

  src/
    config.py                    # Environment loading and Settings dataclass
    chat.py                      # RAG query engine
    local_chunker.py             # Legal chunking logic
    local_ingester.py            # Direct-push ingestion path
    ingester.py                  # Azure Blob + indexer path
    utils.py                     # Azure REST/template helpers

  input_data/
    jsons/                       # Azure AI Search templates
    local_storage/               # Source documents to index
    evaluation/                  # Benchmark workbooks and NotebookLM dataset

  output_data/                   # Generated traces, payloads, previews, eval runs
  scripts/
    deploy-aca.ps1               # Azure Container Apps deployment via ARM API
    analyze_eval_results.py      # Optional evaluation analysis/figure generation

  teams/
    manifest.json
    color.png
    outline.png
    eiopa-rag-teams-app.zip
```

Ignored local/generated folders include `output_data/`, `output/`, `tmp/`, `latex/`, `presentation/`, `.env`, and `.venv/`.

## Azure Services

The prototype uses these Azure services:

- **Azure AI Search** for lexical search, vector search, semantic configuration, and metadata storage.
- **Azure OpenAI / Azure AI Foundry** for embeddings, query profiling, step-back rewriting, document summaries, answer generation, and evaluation judging.
- **Azure Storage / Blob Storage** for the optional Azure-native ingestion path.
- **Azure Container Registry** for container images.
- **Azure Container Apps** for hosting the API.
- **Azure Bot Service** and **Microsoft Teams** for chat access inside an organization.
- **Microsoft Entra ID** for deployment identity and bot identity.
- **Log Analytics / Application Insights** for production observability.
- **Azure Key Vault** is recommended for production secret management, although the prototype uses environment variables.

## Installation

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy the example environment file and fill in local values:

```powershell
Copy-Item .env.example .env
```

Do not commit `.env`.

## Environment Variables

The main runtime variables are defined in `.env.example`.

### Local

- `LOCAL_STORAGE`: local folder containing source documents to index. Default: `input_data/local_storage`.

### Azure AI Search

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_ADMIN_KEY`
- `AZURE_SEARCH_INDEX_NAME`
- `AZURE_SEARCH_DATASOURCE_NAME`
- `AZURE_SEARCH_SKILLSET_NAME`
- `AZURE_SEARCH_INDEXER_NAME`
- `AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME`
- `AZURE_SEARCH_KNOWLEDGE_BASE_NAME`

### Azure OpenAI / Foundry

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `AZURE_OPENAI_JUDGE_DEPLOYMENT`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_EMBEDDING_MODEL`
- `AZURE_OPENAI_EMBEDDING_DIMENSIONS`

The endpoint format expected by this code is:

```text
https://<resource>.openai.azure.com/openai/v1
```

### Azure Storage

- `AZURE_STORAGE_ACCOUNT_NAME`
- `AZURE_CONTAINER_NAME`
- `AZURE_CONTAINER_SAS_TOKEN`
- `AZURE_CONTAINER_SAS_URL`

### Deployment

- `AZURE_SUBSCRIPTION_ID`
- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `AZURE_RESOURCE_GROUP`
- `AZURE_LOCATION`
- `AZURE_ACR_NAME`
- `AZURE_IMAGE_TAG`
- `ACA_ENV_NAME`
- `ACA_APP_NAME`
- `ACA_LOG_ANALYTICS_WORKSPACE`

## Running The Main Workflows

### 1. Preview Chunking Without Uploading

Use this before ingestion when you want to inspect chunk quality and metadata:

```powershell
python chunk_preview.py
```

Output is written to:

```text
output_data/chunk_previews/<run_id>/
```

Each JSON preview is readable and does not include embeddings.

### 2. Ingest Local Documents Into Azure AI Search

This is the current trusted ingestion path:

```powershell
python ingest_local.py
```

It:

- reads files from `LOCAL_STORAGE`;
- chunks supported files;
- generates document summaries where appropriate;
- embeds summary + chunk corpus;
- uploads chunks directly to Azure AI Search;
- writes readable ingestion payloads under `output_data/ingestion_payloads/<run_id>/`.

### 3. Run Optional Azure Indexer Ingestion

This path is kept for future Azure-native ingestion:

```powershell
python ingest_indexer.py
```

It synchronizes local files to Azure Blob Storage, updates Search templates from `input_data/jsons/`, and runs the Azure AI Search indexer.

### 4. Chat In The Terminal

```powershell
python chat_terminal.py
```

Terminal chat always enables trace logging. Traces are written to:

```text
output_data/chat_traces/
```

### 5. Run The API Locally

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

Health check:

```text
GET /healthz
```

RAG endpoint:

```text
POST /rag/answer
```

Example request:

```json
{
  "question": "What is EIOPA's role under DORA?",
  "user_id": "user-123",
  "conversation_id": "conv-456"
}
```

Example response shape:

```json
{
  "answer": "...",
  "citations": [
    {
      "source_id": 1,
      "doc_name": "L_2022333EN.01000101.xml.html",
      "source_type": "html",
      "page_row_num": 1,
      "chapter_num": 5,
      "article_num": "31",
      "annex_num": "NA"
    }
  ],
  "trace_id": "turn-1",
  "profile": {
    "complexity": "high",
    "expertise": "expert",
    "specificity": "explicit",
    "references": []
  },
  "active_query": "...",
  "retrieval_skipped": false
}
```

### 6. Run As A Teams Bot

`app.py` also exposes:

```text
POST /api/messages
```

This is the Bot Framework endpoint used by Azure Bot Service / Microsoft Teams. The Teams app package lives in `teams/`.

For local API testing, use `/rag/answer`. For Teams deployment, configure the bot messaging endpoint to:

```text
https://<your-host>/api/messages
```

## Retrieval And Metadata

The Azure AI Search schema is defined in `input_data/jsons/index.json`.

Searchable fields include:

- `id`
- `source_type`
- `doc_name`
- `article_num`
- `annex_num`
- `doc_summary`
- `corpus`
- `embedding` for vector search

Filterable/sortable metadata includes:

- `source_type`
- `doc_name`
- `page_row_num`
- `chapter_num`
- `article_num`
- `annex_num`

Important detail: `page_row_num` and `chapter_num` are numeric fields. They are filterable, sortable, and facetable, but they are not lexical-searchable. The current chat engine does not automatically apply metadata filters for prompts such as “page 54”; it retrieves through hybrid search and reranking.

## RAG Evaluation

The main benchmark is:

```text
input_data/evaluation/dora_qas_eiopa_eba_benchmark.xlsx
```

Expected sheet:

```text
benchmark_qas
```

The script:

```powershell
python evaluate_rag.py
```

does the following:

- asks each benchmark question to the current RAG;
- stores the RAG answer, citations, trace ID, query profile, and retrieval diagnostics;
- calls the judge deployment from `AZURE_OPENAI_JUDGE_DEPLOYMENT`;
- scores answer quality with answer correctness, faithfulness, answer relevance, completeness, citation precision, citation recall, and unsupported-claims score;
- evaluates retrieval metrics when Article or Template references are available;
- writes a fresh workbook under `output_data/eval_runs/<run_id>/`.

Useful options:

```powershell
python evaluate_rag.py --limit 1
python evaluate_rag.py --limit 5 --no-judge
```

The evaluation intentionally assumes that the official EIOPA/EBA Q&A files are not part of the indexed knowledge base, so the RAG cannot simply retrieve benchmark answers.

## NotebookLM Evaluation

The NotebookLM comparison dataset is:

```text
input_data/evaluation/notebook_lm.xlsx
```

Expected sheet:

```text
notebook_lm_answers
```

Run:

```powershell
python evaluate_notebooklm.py
```

This script evaluates existing NotebookLM answers with the same answer-level judge rubric used for the RAG evaluation. It does not automate NotebookLM through a browser; it only judges answers already collected in the workbook.

Useful options:

```powershell
python evaluate_notebooklm.py --limit 1
python evaluate_notebooklm.py --dry-run
python evaluate_notebooklm.py --question-id "DORA 253 - 3393"
```

Output is written to:

```text
output_data/eval_runs/<run_id>/notebooklm_answer_evaluated_<run_id>.xlsx
```

## Analyze Evaluation Results

After a RAG evaluation run, optional aggregate tables and figures can be generated with:

```powershell
python scripts/analyze_eval_results.py --input output_data/eval_runs/<run_id>/<evaluated_workbook>.xlsx
```

By default, the script writes analysis outputs to the evaluation run folder and figure files under `latex/images/evaluation/`.

## Docker

Build locally:

```powershell
docker build -t legal-rag-api:local .
```

Run locally:

```powershell
docker run --rm -p 8000:8000 --env-file .env legal-rag-api:local
```

The container starts:

```text
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Azure Deployment

The deployment script is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-aca.ps1
```

The script uses Azure Resource Manager APIs and environment variables. It does not require the Azure CLI for provisioning.

It can:

- create or update the resource group;
- create or use Azure Container Registry;
- build and push the Docker image;
- create a Log Analytics workspace;
- create a Container Apps environment;
- deploy the FastAPI container app with external ingress.

Before running deployment, verify:

- `.env` is filled locally and is not committed;
- `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_SUBSCRIPTION_ID` are GUIDs;
- `AZURE_CLIENT_SECRET` is the secret value, not the secret ID;
- the deployment app registration has Contributor on the target resource group;
- the container app receives all runtime variables required by `src/config.py`.

## Development Checks

Compile the Python entry points:

```powershell
python -m py_compile app.py chat_terminal.py chunk_preview.py ingest_local.py ingest_indexer.py evaluate_rag.py evaluate_notebooklm.py src/config.py src/chat.py src/local_chunker.py src/local_ingester.py src/ingester.py src/utils.py
```

Smoke-test the API:

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/healthz
```

## Git Hygiene

Do not commit:

- `.env`
- `.venv/`
- `output_data/`
- `output/`
- `tmp/`
- generated logs
- local LaTeX build outputs
- local presentation render outputs

Commit intentionally:

- source code;
- Azure Search templates in `input_data/jsons/`;
- benchmark datasets in `input_data/evaluation/`;
- Teams manifest assets in `teams/`;
- `README.md` and `.env.example`.

## Notes

- The terminal chat and API do not run ingestion implicitly.
- Ingestion is always an explicit script.
- Terminal chat always writes trace logs.
- The direct local ingester is the trusted ingestion path for the current prototype.
- The Azure indexer path is preserved for future production-style ingestion.
- The evaluation scripts write new workbooks and do not overwrite the benchmark source files unless explicitly requested by script options.
