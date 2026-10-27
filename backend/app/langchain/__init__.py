"""ComplyAgent's LangChain layer: every language-model feature is built from LangChain parts.

One module per LangChain component, each used by the app (nothing here is a demo):

    models.py    MODELS    The chat model (AzureChatOpenAI or ChatAnthropic) and one error mapping
    prompts.py   PROMPTS   Every system prompt and ChatPromptTemplate in the app
    chains.py    CHAINS    LCEL chains: prompt | model.with_structured_output(schema)
    memory.py    MEMORY    Chat history for the policy assistant, conversation memory for the agent
    indexes.py   INDEXES   A LangChain retriever over the policy knowledge base
    agents.py    AGENTS    The AI Assistant: a tool-calling agent over policies (and customers)
    llm.py                 LangChainGenerator, built from models + prompts + chains + memory

How the rest of the backend uses it:

    app/rag/knowledge_base.py    answers policy questions with LangChainGenerator.generate
    app/decisions/decision.py    the Decision Agent: prompt from prompts.py, call through chains.py
    app/decisions/messages.py    the email writer: prompt from prompts.py, call through chains.py
    app/main.py                  POST /api/assistant/chat runs agents.AssistantAgent

The folder is named after LangChain on purpose; the installed packages are imported by their own
names (langchain_core, langchain_openai, langchain.agents), so there is no clash.
"""
