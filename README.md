# EIOPA RAG Pipeline

This project runs an end-to-end RAG ingestion and chat workflow from `main.py`.

## What `main.py` does

When you run `main.py`, it executes the pipeline in this order:

1. Loads configuration from environment variables (optionally from `.env` via `python-dotenv`) and validates required settings.
2. Uploads local files from `data/blob_container` to the configured Azure Blob Storage container.
3. Creates or updates the Azure AI Search index, including:
	- Metadata fields (for filtering/faceting/sorting)
	- `corpus` text field for semantic ranking
	- `embedding` vector field for vector search
4. Creates or updates a Search data source pointing to Blob Storage.
5. Creates or updates a Search skillset that:
	- Splits extracted content into chunks (`SplitSkill`)
	- Generates embeddings for each chunk (`AzureOpenAIEmbeddingSkill`)
	- Projects chunk outputs into the target index (`indexProjections`)
6. Creates or updates and runs a Search indexer to execute ingestion + enrichment.
7. Polls indexer status until completion (or raises an error/timeout).
8. Creates or updates a Search knowledge source that references the index.
9. Creates or updates a Search knowledge base for extractive retrieval.
10. Creates or updates the Microsoft Foundry project connection for the MCP knowledge base endpoint.
11. Creates or updates a Foundry agent configured to use the knowledge base tool.
12. Starts an interactive terminal chat loop so you can ask questions grounded on indexed data.

## Output

After setup completes, you can chat in the terminal. The agent uses the Azure AI Search knowledge base as its retrieval source.

## Required setup

- Python virtual environment with dependencies in `requirements.txt`
- Azure AI Search service and admin key
- Azure Storage account connection string and container
- Azure OpenAI embedding deployment
- Microsoft Foundry project endpoint and resource ID

Use `.env.example` as the template for required environment variables.
