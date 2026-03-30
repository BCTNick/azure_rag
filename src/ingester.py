def upload_local_files_to_blob(settings: Settings) -> None:
    print("Uploading files from local folder to Azure Blob Storage...")
    if not settings.local_blob_folder.exists():
        raise FileNotFoundError(f"Local folder not found: {settings.local_blob_folder}")

    blob_service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    container_client = blob_service.get_container_client(settings.storage_container_name)
    if not container_client.exists():
        container_client.create_container()

    files = [p for p in settings.local_blob_folder.rglob("*") if p.is_file()]
    if not files:
        raise ValueError(f"No files found in {settings.local_blob_folder}")

    for path in files:
        blob_name = str(path.relative_to(settings.local_blob_folder)).replace("\\", "/")
        with path.open("rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        print(f"Uploaded: {blob_name}")


def create_search_index(settings: Settings) -> None:
    print("Creating or updating Azure AI Search index...")
    credential = AzureKeyCredential(settings.search_admin_key)
    index_client = SearchIndexClient(endpoint=settings.search_endpoint, credential=credential)

    index = SearchIndex(
        name=settings.index_name,
        fields=[
            SearchField(name="id", type="Edm.String", key=True, filterable=True, sortable=True, facetable=False),
            SearchField(name="parent_id", type="Edm.String", filterable=True, sortable=False, facetable=False),
            SearchField(name="doc_name", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="page_num", type="Edm.Int32", filterable=True, sortable=True, facetable=True),
            SearchField(name="chapter_num", type="Edm.Int32", filterable=True, sortable=True, facetable=True),
            SearchField(name="article_num", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="annex_num", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="question_id", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="submitted_on", type="Edm.DateTimeOffset", filterable=True, sortable=True, facetable=True),
            SearchField(name="answered_on", type="Edm.DateTimeOffset", filterable=True, sortable=True, facetable=True),
            SearchField(name="regulation_reference", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="qa_topic", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="article_template", type="Edm.String", searchable=True, filterable=False, sortable=False, facetable=False),
            SearchField(name="background_question", type="Edm.String", searchable=True, filterable=False, sortable=False, facetable=False),
            SearchField(name="corpus", type="Edm.String", searchable=True, filterable=False, sortable=False, facetable=False),
            SearchField(
                name="embedding",
                type="Collection(Edm.Single)",
                stored=False,
                vector_search_dimensions=3072,
                vector_search_profile_name="hnsw_text_3_large",
            ),
        ],
        vector_search=VectorSearch(
            profiles=[
                VectorSearchProfile(
                    name="hnsw_text_3_large",
                    algorithm_configuration_name="alg",
                    vectorizer_name="azure_openai_text_3_large",
                )
            ],
            algorithms=[HnswAlgorithmConfiguration(name="alg")],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="azure_openai_text_3_large",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=settings.azure_openai_endpoint,
                        deployment_name=settings.azure_openai_embedding_deployment,
                        model_name=settings.azure_openai_embedding_model,
                    ),
                )
            ],
        ),
        semantic_search=SemanticSearch(
            default_configuration_name="semantic_config",
            configurations=[
                SemanticConfiguration(
                    name="semantic_config",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="qa_topic"),
                        content_fields=[SemanticField(field_name="corpus")],
                        keywords_fields=[
                            SemanticField(field_name="regulation_reference"),
                            SemanticField(field_name="article_num"),
                        ],
                    ),
                )
            ],
        ),
    )

    index_client.create_or_update_index(index)


def _search_rest_put(settings: Settings, path: str, payload: dict, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"Content-Type": "application/json", "api-key": settings.search_admin_key}
    response = requests.put(url, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    return response.json() if response.text else {}


def _search_rest_post(settings: Settings, path: str, payload: dict | None = None, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"Content-Type": "application/json", "api-key": settings.search_admin_key}
    response = requests.post(url, headers=headers, json=payload or {}, timeout=120)
    response.raise_for_status()
    return response.json() if response.text else {}


def _search_rest_get(settings: Settings, path: str, api_version: str = "2025-09-01") -> dict:
    url = f"{settings.search_endpoint}/{path}?api-version={api_version}"
    headers = {"api-key": settings.search_admin_key}
    response = requests.get(url, headers=headers, timeout=120)
    response.raise_for_status()
    return response.json() if response.text else {}


def create_data_source_skillset_and_indexer(settings: Settings) -> None:
    print("Creating or updating data source...")
    data_source_payload = {
        "name": settings.data_source_name,
        "type": "azureblob",
        "credentials": {"connectionString": settings.storage_connection_string},
        "container": {"name": settings.storage_container_name},
    }
    _search_rest_put(settings, f"datasources/{settings.data_source_name}", data_source_payload)

    print("Creating or updating skillset (split + embedding + projection)...")
    embedding_skill: dict = {
        "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
        "name": "eiopa_embedding_skill",
        "description": "Generate embeddings for chunked text.",
        "context": "/document/pages/*",
        "inputs": [{"name": "text", "source": "/document/pages/*"}],
        "outputs": [{"name": "embedding", "targetName": "embedding"}],
        "resourceUri": settings.azure_openai_endpoint,
        "deploymentId": settings.azure_openai_embedding_deployment,
        "modelName": settings.azure_openai_embedding_model,
    }
    if settings.azure_openai_api_key:
        embedding_skill["apiKey"] = settings.azure_openai_api_key

    skillset_payload = {
        "name": settings.skillset_name,
        "description": "Chunk documents and generate embeddings.",
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "name": "eiopa_split_skill",
                "description": "Split extracted document text into chunks.",
                "context": "/document",
                "textSplitMode": "pages",
                "maximumPageLength": 2000,
                "pageOverlapLength": 300,
                "unit": "characters",
                "inputs": [{"name": "text", "source": "/document/content"}],
                "outputs": [{"name": "textItems", "targetName": "pages"}],
            },
            embedding_skill,
        ],
        "indexProjections": {
            "selectors": [
                {
                    "targetIndexName": settings.index_name,
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/pages/*",
                    "mappings": [
                        {"name": "corpus", "source": "/document/pages/*"},
                        {"name": "embedding", "source": "/document/pages/*/embedding"},
                        {"name": "doc_name", "source": "/document/metadata_storage_name"},
                        {"name": "regulation_reference", "source": "/document/metadata_storage_path"},
                    ],
                }
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }
    _search_rest_put(settings, f"skillsets/{settings.skillset_name}", skillset_payload)

    print("Creating or updating indexer...")
    indexer_payload = {
        "name": settings.indexer_name,
        "dataSourceName": settings.data_source_name,
        "targetIndexName": settings.index_name,
        "skillsetName": settings.skillset_name,
        "parameters": {
            "batchSize": 1,
            "configuration": {
                "dataToExtract": "contentAndMetadata",
                "parsingMode": "default",
                "allowSkillsetToReadFileData": True,
            },
        },
    }
    _search_rest_put(settings, f"indexers/{settings.indexer_name}", indexer_payload)


def run_indexer_and_wait(settings: Settings, timeout_seconds: int = 1800) -> None:
    print("Running indexer...")
    _search_rest_post(settings, f"indexers/{settings.indexer_name}/run")

    start = time.time()
    while True:
        status = _search_rest_get(settings, f"indexers/{settings.indexer_name}/status")
        last_result = status.get("lastResult", {})
        state = (last_result.get("status") or "").lower()

        if state == "success":
            print("Indexer completed successfully.")
            return
        if state in {"transientfailure", "error", "failed"}:
            raise RuntimeError(f"Indexer failed: {last_result}")

        if time.time() - start > timeout_seconds:
            raise TimeoutError("Timed out waiting for indexer completion.")

        print("Indexer still running... waiting 15s")
        time.sleep(15)


def create_knowledge_source_and_base(settings: Settings) -> str:
    print("Creating or updating knowledge source and knowledge base...")
    index_client = SearchIndexClient(
        endpoint=settings.search_endpoint,
        credential=AzureKeyCredential(settings.search_admin_key),
    )

    ks = SearchIndexKnowledgeSource(
        name=settings.knowledge_source_name,
        description="Knowledge source for EIOPA documents.",
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=settings.index_name,
            source_data_fields=[
                SearchIndexFieldReference(name="id"),
                SearchIndexFieldReference(name="doc_name"),
                SearchIndexFieldReference(name="page_num"),
                SearchIndexFieldReference(name="corpus"),
            ],
        ),
    )
    index_client.create_or_update_knowledge_source(knowledge_source=ks)

    kb = KnowledgeBase(
        name=settings.knowledge_base_name,
        knowledge_sources=[KnowledgeSourceReference(name=settings.knowledge_source_name)],
        output_mode=KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA,
        retrieval_reasoning_effort=KnowledgeRetrievalMinimalReasoningEffort(),
    )
    index_client.create_or_update_knowledge_base(knowledge_base=kb)

    return f"{settings.search_endpoint}/knowledgebases/{settings.knowledge_base_name}/mcp?api-version=2025-11-01-Preview"
