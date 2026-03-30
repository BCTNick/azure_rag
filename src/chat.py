def create_foundry_connection(settings: Settings, credential: DefaultAzureCredential, mcp_endpoint: str) -> None:
    print("Creating or updating Foundry project connection for MCP knowledge base...")
    token_provider = get_bearer_token_provider(credential, "https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {token_provider()}"}

    response = requests.put(
        (
            f"https://management.azure.com{settings.project_resource_id}/connections/"
            f"{settings.project_connection_name}?api-version=2025-10-01-preview"
        ),
        headers=headers,
        json={
            "name": settings.project_connection_name,
            "type": "Microsoft.MachineLearningServices/workspaces/connections",
            "properties": {
                "authType": "ProjectManagedIdentity",
                "category": "RemoteTool",
                "target": mcp_endpoint,
                "isSharedToAll": True,
                "audience": "https://search.azure.com/",
                "metadata": {"ApiType": "Azure"},
            },
        },
        timeout=120,
    )
    response.raise_for_status()


def create_or_update_agent(
    settings: Settings,
    project_client: AIProjectClient,
    mcp_endpoint: str,
) -> object:
    print("Creating or updating Foundry agent...")
    instructions = (
        "You are a helpful assistant that must use the knowledge base for every answer. "
        "Never answer from your own knowledge. "
        "If the knowledge base has no answer, respond with 'I don't know'. "
        "Always include citations from retrieved sources."
    )

    mcp_tool = MCPTool(
        server_label="knowledge-base",
        server_url=mcp_endpoint,
        require_approval="never",
        allowed_tools=["knowledge_base_retrieve"],
        project_connection_id=settings.project_connection_name,
    )

    return project_client.agents.create_version(
        agent_name=settings.agent_name,
        definition=PromptAgentDefinition(
            model=settings.agent_model,
            instructions=instructions,
            tools=[mcp_tool],
        ),
    )


def chat_in_terminal(project_client: AIProjectClient, agent: object) -> None:
    print("Starting terminal chat. Type 'exit' to stop.")
    openai_client = project_client.get_openai_client()
    conversation = openai_client.conversations.create()

    while True:
        user_text = input("You: ").strip()
        if user_text.lower() in {"exit", "quit", "q"}:
            print("Bye.")
            return
        if not user_text:
            continue

        response = openai_client.responses.create(
            conversation=conversation.id,
            tool_choice="required",
            input=user_text,
            extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
        )
        print(f"Assistant: {response.output_text}\n")
