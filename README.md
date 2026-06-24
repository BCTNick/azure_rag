# Azure Legal RAG Platform

This repository provides a complete legal and regulatory RAG platform on Azure, with:

- robust ingestion pipelines,
- hybrid retrieval and advanced query orchestration,
- grounded answer generation with citations,
- API and Teams-ready service interfaces,
- trace logging for auditability.

The system is designed for legal corpora such as regulations, directives, delegated acts, RTS/ITS material, and supervisory Q&A sets.

## 1. Platform Capabilities

### Ingestion and indexing

Implemented:

- Local direct-push ingestion through src/local_ingester.py.
- Legacy Azure-native ingestion experiment retained in src/ingester.py and JSON templates in input_data/jsons for possible future work.
- Source-aware parsing for PDF, Excel, HTML, and text-like files.
- Legal structure extraction (article, chapter, annex, page/row metadata).
- Chunk enrichment with document summary and metadata.
- Embeddings generated from summary + chunk content.
- Azure AI Search indexing with vector-enabled schema.

### Retrieval and reasoning

Implemented:

- Query classification by expertise and complexity.
- Complexity mode none to skip retrieval for non-retrieval conversational turns.
- Step-back query rewriting and query decomposition.
- Multi-variant retrieval orchestration.
- Hybrid lexical + vector retrieval in Azure AI Search.
- Weighted fusion of retrieval signals.
- Query-aware heuristic reranking based on fused retrieval score, lexical overlap, and explicit reference matches.
- MMR-style evidence diversification.
- Citation extraction from selected evidence.

### Generation and interfaces

Implemented:

- Grounded response generation from retrieved evidence.
- Terminal chat experience through `chat_terminal.py`.
- Reusable one-turn answer API method: answer_once.
- FastAPI service with:
  - GET /healthz
  - POST /rag/answer
- Teams-ready architecture with traceable request handling.

### Observability

Implemented:

- Terminal chat traces in `output_data/chat_traces/`.
- Turn-level logs include:
  - query classification,
  - step-back transformation,
  - retrieved context,
  - prompt sent to the LLM,
  - final response.

## 2. Repository Structure

```text
azure_rag/
  app.py
  chat_terminal.py
  ingest_local.py
  ingest_indexer.py
  PLAN.md
  README.md
  requirements.txt
  .env.example
  src/
    config.py
    chat.py
    local_ingester.py
    local_chunker.py
    ingester.py
    utils.py
  input_data/
    jsons/
      index.json
      datasource.json
      skillset.json
      indexer.json
      knowledge_source.json
    local_storage/
  output_data/
    chat_traces/
    ingestion_payloads/
    eval_runs/
  latex/
```

## 3. Runtime Entry Points

### Terminal chat

Run:

```bash
python chat_terminal.py
```

Behavior:

- Loads environment settings.
- Starts interactive legal chat.
- Always writes trace logs under `output_data/chat_traces/`.

### Local direct-push ingestion

Run:

```bash
python ingest_local.py
```

This is the current trusted ingestion path. It chunks local files, creates summaries and embeddings, uploads documents directly to Azure AI Search, and writes payload audits under `output_data/ingestion_payloads/`.

### Azure indexer ingestion

Run:

```bash
python ingest_indexer.py
```

This path uploads files to Azure Blob Storage, syncs the Azure AI Search datasource/skillset/indexer resources, and runs the indexer.

### API service

Run:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Endpoints:

- GET /healthz
- POST /rag/answer

Example request:

```json
{
  "question": "What are the oversight obligations and does EIOPA apply?",
  "user_id": "user-123",
  "conversation_id": "conv-456"
}
```

Example response:

```json
{
  "answer": "...",
  "citations": [
    {
      "source_id": 1,
      "doc_name": "CELEX_32022R2554_EN_TXT.pdf",
      "source_type": "pdf",
      "page_row_num": "42",
      "chapter_num": "III",
      "article_num": "46",
      "annex_num": "NA"
    }
  ],
  "trace_id": "turn-7",
  "profile": {
    "complexity": "high",
    "expertise": "expert",
    "specificity": "explicit",
    "references": ["Article 46"]
  },
  "active_query": "...",
  "retrieval_skipped": false
}
```

## 4. Environment Variables

All variables are required unless explicitly documented as optional.

### Core runtime

- LOCAL_STORAGE

### Azure AI Search

- AZURE_SEARCH_ENDPOINT
- AZURE_SEARCH_ADMIN_KEY
- AZURE_SEARCH_INDEX_NAME
- AZURE_SEARCH_DATASOURCE_NAME
- AZURE_SEARCH_SKILLSET_NAME
- AZURE_SEARCH_INDEXER_NAME
- AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME
- AZURE_SEARCH_KNOWLEDGE_BASE_NAME

### Azure OpenAI / Foundry

- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_API_KEY
- AZURE_OPENAI_CHAT_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_MODEL

### Azure Storage

- AZURE_STORAGE_ACCOUNT_NAME
- AZURE_CONTAINER_NAME
- AZURE_CONTAINER_SAS_TOKEN
- AZURE_CONTAINER_SAS_URL

### Where To Get Each .env Variable

Use this section to populate .env safely.

Deployment and container variables:

- AZURE_SUBSCRIPTION_ID
  - Azure Portal: Subscriptions > your subscription > Overview > Subscription ID
- AZURE_TENANT_ID
  - Azure Portal: Microsoft Entra ID > Overview > Tenant ID
- AZURE_CLIENT_ID
  - Azure Portal: Microsoft Entra ID > App registrations > your app > Application (client) ID
- AZURE_CLIENT_SECRET
  - Azure Portal: Microsoft Entra ID > App registrations > your app > Certificates & secrets > Client secrets
  - Store secret value only in local .env
- AZURE_RESOURCE_GROUP
  - Azure Portal: Resource groups > choose/create group name
- AZURE_LOCATION
  - Use the region where your core services run, for example westeurope
- AZURE_ACR_NAME
  - Azure Portal: Container registries > registry name
  - Must be globally unique, lowercase letters/numbers
- AZURE_IMAGE_TAG
  - Your release label, for example v1, v2, 2026-06-16
- ACA_ENV_NAME / ACA_APP_NAME
  - Names you choose for Azure Container Apps environment and app
- ACA_LOG_ANALYTICS_WORKSPACE
  - Name for Log Analytics workspace used by Container Apps environment
- APPSERVICE_PLAN_NAME / APPSERVICE_WEBAPP_NAME
  - Names you choose for App Service plan and web app

Service principal and permissions setup (required for API-based deployment scripts):

1. Create an app registration
  - Azure Portal > Microsoft Entra ID > App registrations > New registration
  - Name it, for example legal-rag-deployer
2. Collect identity values
  - AZURE_TENANT_ID from Microsoft Entra ID > Overview > Tenant ID
  - AZURE_CLIENT_ID from the app registration > Overview > Application (client) ID
3. Create a client secret
  - App registration > Certificates & secrets > New client secret
  - Copy the secret Value immediately and store it in AZURE_CLIENT_SECRET
4. Grant deployment permissions to this app
  - Recommended scope: Resource groups > your group > Access control (IAM)
  - Select Add role assignment
  - Required role: Contributor (built-in)
  - Depending on portal UX/version, Contributor can appear under Job function roles or under Privileged administrator roles.
  - In the role picker, choose the role with exact name Contributor and description:
    Grants full access to manage all resources, but does not allow you to assign roles in Azure RBAC.
  - Do not choose similarly named roles such as:
    - Contributor DataActions
    - User Access Administrator
    - Service-specific contributor roles (for example Search Service Contributor, Storage Blob Data Contributor)
  - Assign access to: User, group, or service principal
  - Select members: search for your app registration name and select it
  - Save
5. Wait a few minutes for role propagation, then run the deployment script

Pre-deployment identity checklist (exactly what must be in .env):

- AZURE_TENANT_ID
  - Must be the Tenant ID GUID (not tenant name)
  - Source: Microsoft Entra ID > Overview > Tenant ID
- AZURE_CLIENT_ID
  - Must be the Application (client) ID GUID (not app display name)
  - Source: App registrations > your app > Overview > Application (client) ID
- AZURE_CLIENT_SECRET
  - Must be the client secret Value (not Secret ID)
  - Source: App registrations > your app > Certificates & secrets
- AZURE_SUBSCRIPTION_ID
  - Must be the target subscription GUID where resources are created

Quick sanity checks before running deployment:

- AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_SUBSCRIPTION_ID should all look like GUIDs in the form xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.
- AZURE_CLIENT_ID should never be a text label like my-app-id or eiopa-client-id.
- Role assignment must be on the same target scope where you deploy (recommended: target resource group).

If you hit AADSTS700016 (application not found):

1. Verify AZURE_CLIENT_ID is copied from Application (client) ID, not from app name.
2. Verify AZURE_TENANT_ID is the tenant that contains that app registration.
3. If the app was just created, wait 1-3 minutes and retry.
4. Recreate AZURE_CLIENT_SECRET if needed and update .env.

Notes:

- The deployment app registration is only for provisioning resources (ARM API calls).
- It is separate from your runtime app and separate from the Docker image itself.
- Do not commit AZURE_CLIENT_SECRET to git.

Azure AI Search variables:

- AZURE_SEARCH_ENDPOINT
  - Azure Portal: AI Search service > Overview > URL
- AZURE_SEARCH_ADMIN_KEY
  - Azure Portal: AI Search service > Keys > Primary admin key
- AZURE_SEARCH_INDEX_NAME
- AZURE_SEARCH_DATASOURCE_NAME
- AZURE_SEARCH_SKILLSET_NAME
- AZURE_SEARCH_INDEXER_NAME
- AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME
- AZURE_SEARCH_KNOWLEDGE_BASE_NAME
  - Use the resource names already created in your search service, or keep the defaults used by this repo (index, datasource, skillset, indexer, knowledge-source, knowledge-base)

Azure OpenAI variables:

- AZURE_OPENAI_ENDPOINT
  - Azure Portal: Azure OpenAI resource > Keys and Endpoint > Endpoint
  - Keep the format expected by this repo: https://<resource>.openai.azure.com/openai/v1
- AZURE_OPENAI_API_KEY
  - Azure Portal: Azure OpenAI resource > Keys and Endpoint > Key 1 or Key 2
- AZURE_OPENAI_CHAT_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_MODEL
  - Azure AI Foundry / Azure OpenAI Studio > Deployments > deployment names and model names

Azure Storage variables:

- AZURE_STORAGE_ACCOUNT_NAME
  - Azure Portal: Storage account > Overview > Name
- AZURE_CONTAINER_NAME
  - Azure Portal: Storage account > Data storage > Containers
- AZURE_CONTAINER_SAS_TOKEN
- AZURE_CONTAINER_SAS_URL
  - Azure Portal: Storage account > Containers > select container > Generate SAS
  - SAS token is the query string value
  - SAS URL is the full URL including SAS query

Runtime variables:

- LOCAL_STORAGE
  - Local path used by ingestion; inside the container, use `/app/input_data/local_storage`.

## 5. How The Query Pipeline Works

1. Classify user query into profile fields:
   - complexity,
   - expertise,
   - specificity,
   - explicit legal references.
2. If complexity is none, skip retrieval and answer in non-citation mode.
3. Build one or more retrieval variants (base query, references, step-back, decomposed parts).
4. Run lexical and vector retrieval for each variant.
5. Fuse lexical and vector rankings.
6. Apply legal-aware reranking using metadata and text overlap.
7. Select diverse final evidence through MMR.
8. Build grounded prompt and generate final answer with citations.

## 6. Ingestion Quality Profile

The ingestion layer preserves legal explainability by keeping retrieval metadata attached to each chunk:

- doc_name
- source_type
- page_row_num
- chapter_num
- article_num
- annex_num
- doc_summary
- corpus
- embedding

Excel content is row-preserving; PDF and HTML favor legal-structure-aware chunking with fallback chunking for robustness.

## 7. Trace Logging

Terminal chat always writes per-session logs in `output_data/chat_traces/`:

- chat_YYYYMMDD_HHMMSS.txt

Each turn captures:

- user query,
- classification,
- step-back query,
- retrieval output,
- final prompt payload,
- model answer.

This supports legal auditability and retrieval diagnostics.

## 8. Teams Deployment Readiness

The platform is structured for direct Teams channel integration through an API-facing bot service:

- deterministic request entrypoint,
- stable answer contract,
- citation and trace ID support,
- Azure-hostable FastAPI backend.

Recommended Azure hosting stack:

- Azure Container Apps,
- Azure Bot Service,
- Microsoft Teams app package,
- Key Vault + Application Insights.

## 9. Install

```bash
pip install -r requirements.txt
```

## 10. Docker Image

Build locally:

```bash
docker build -t legal-rag-api:local .
```

Run locally:

```bash
docker run --rm -p 8000:8000 --env-file .env legal-rag-api:local
```

## 11. Azure Deployment Scripts (ARM API, No Azure CLI)

Ready-to-run scripts are included:

- scripts/deploy-aca.ps1

Both scripts:

- create a resource group,
- create Azure Container Registry through ARM API,
- build and push the Docker image,
- deploy the container resource to Azure through ARM API,
- set all required runtime environment variables.

Authentication model:

- The scripts request a Microsoft Entra OAuth token using:
  - AZURE_TENANT_ID
  - AZURE_CLIENT_ID
  - AZURE_CLIENT_SECRET
- They call Azure management REST APIs directly (management.azure.com).
- They do not call az CLI commands.

### Azure Container Apps

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-aca.ps1
```

Important:

- Fill all required variables in local .env before running scripts.
- For auth, verify AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET are from the same app registration and tenant.
- Do not commit .env or client secret values.
- Grant exactly one role to the deployment app registration: Contributor on the target resource group.
- The app still requires all runtime environment variables because settings are validated at startup.

## 12. Notes

- The system is designed for evidence-grounded legal assistance, not legal advice automation.
- Final decisions should be validated against the cited primary legal texts.
- The strongest output quality comes from high-quality source documents and metadata-rich chunking.
