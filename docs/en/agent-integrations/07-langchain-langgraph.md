# LangChain and LangGraph

Wire OpenViking into your LangChain or LangGraph agent as the context backend. The SDK provides a retriever, chat history, context wrapper, agent tools, LangGraph store, and middleware — all connecting to a running OpenViking server over HTTP.

## Install

```bash
pip install "openviking[langchain]"       # retriever + chat history
pip install "openviking[langgraph]"       # full LangGraph support (includes langchain)
```

## Connection

```python
from openviking.integrations.langchain import create_openviking_tools

tools = create_openviking_tools(
    url="http://localhost:1933",
    api_key="...",
    profile="agent",
)
```

When `url` is omitted, the adapters load connection settings from the OpenViking CLI config. Embedding and VLM providers are configured in OpenViking, not in your app.

## Peer identity

All adapters that read or write session memory accept `peer_id`. Use it when one OpenViking user interacts with multiple stable external peers, such as different agents, bots, or people in shared sessions.

```python
retriever = OpenVikingRetriever(
    url="http://localhost:1933",
    peer_id="assistant-a",
)

chain = with_openviking_context(
    runnable,
    session_id="support-thread-1",
    peer_id="assistant-a",
)
```

For dynamic runs, `with_openviking_context()` also reads `config["configurable"]["peer_id"]` by default:

```python
chain.invoke(
    {"messages": [...]},
    config={"configurable": {"session_id": "support-thread-1", "peer_id": "assistant-a"}},
)
```

## Which adapter should I use?

| I want to… | Use this |
|------------|----------|
| Retrieve relevant context for RAG | `OpenVikingRetriever` |
| Wrap a runnable with full session lifecycle (recall + capture + commit) | `with_openviking_context()` |
| Give the agent explicit memory tools | `create_openviking_tools()` |
| Store durable cross-thread state | `OpenVikingStore` |
| Inject context into LangGraph as middleware | `OpenVikingContextMiddleware` |
| Back LangChain chat history with OpenViking | `OpenVikingChatMessageHistory` |

## Quick examples

### Retriever

```python
from openviking.integrations.langchain import OpenVikingRetriever

retriever = OpenVikingRetriever(url="http://localhost:1933", api_key="...")
docs = retriever.invoke("What did the user decide about deployment?")
```

### Context backend

```python
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from openviking.integrations.langchain import with_openviking_context

chain = with_openviking_context(
    RunnableLambda(lambda msgs: AIMessage(content="...")),
    url="http://localhost:1933",
    api_key="...",
)
```

### Agent tools

```python
from openviking.integrations.langchain import create_openviking_tools

tools = create_openviking_tools(url="http://localhost:1933", profile="agent")
# Includes: viking_find, viking_search, viking_browse, viking_read,
#           viking_grep, viking_store, viking_add_resource, and more
```

### LangGraph store

```python
from openviking.integrations.langchain import OpenVikingStore

store = OpenVikingStore(url="http://localhost:1933", api_key="...")
store.put(("users", "ada"), "preferences", {"color": "azure"})
items = store.search(("users",), query="azure", limit=3)
```

### LangGraph middleware

```python
from openviking.integrations.langchain import OpenVikingContextMiddleware

middleware = OpenVikingContextMiddleware(
    url="http://localhost:1933",
    api_key="...",
    capture_on_after_agent=True,
)
```

## Try the examples

The repository includes runnable examples that work without model credentials using an in-memory test client:

```bash
uv run --extra langgraph python examples/langchain-langgraph/langchain/rag/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langchain/context-backend/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langchain/message-history/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langgraph/agent/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langgraph/middleware/quick_app.py
```

For a real OpenViking server and OpenAI-compatible model flow, see the [live LangGraph app](https://github.com/volcengine/OpenViking/blob/main/examples/langchain-langgraph/langgraph/agent/live_app.py).

## See also

- [examples/langchain-langgraph/](https://github.com/volcengine/OpenViking/tree/main/examples/langchain-langgraph) — full source for all examples above
- [MCP Clients](./06-mcp-clients.md) — for non-SDK MCP integration
