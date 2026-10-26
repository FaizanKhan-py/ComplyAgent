"""ComplyAgent's LangChain layer: every language-model feature is built from LangChain parts.

One module per LangChain component, each used by the app (nothing here is a demo):

    models.py    MODELS    The chat model (AzureChatOpenAI or ChatAnthropic) and one error mapping
    prompts.py   PROMPTS   Every system prompt and ChatPromptTemplate in the app
    chains.py    CHAINS    LCEL chains: prompt | model.with_structured_output(schema)
    memory.py    MEMORY    Chat history for the policy assistant, conversation memory for the agent
    indexes.py   INDEXES   A LangChain retriever over the policy knowledge base
    agents.py    AGENTS    The Collections Agent: a tool-calling agent over customers and policies

How the rest of the backend uses it:

    app/llm.py           LangChainGenerator: the one object the API, the knowledge base, the
                         Decision Agent and the message writer call
                         (models + prompts + chains + memory)
    app/decision.py      the Decision Agent's prompt comes from prompts.py, its call from chains.py
    app/messages.py      the message writer's prompt comes from prompts.py, its call from chains.py
    app/main.py          POST /api/admin/agent/chat runs agents.CollectionsAgent

The folder is named after LangChain on purpose; the installed packages are imported by their own
names (langchain_core, langchain_openai, langchain.agents), so there is no clash.
"""
