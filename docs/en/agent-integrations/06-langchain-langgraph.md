# LangChain and LangGraph

OpenViking can be configured as the context backend for LangChain and LangGraph agents through optional Python SDK adapters. The recommended deployment is HTTP mode: the agent app connects to a running OpenViking server, and OpenViking handles storage, indexing, embeddings, VLM parsing, session compression, and memory extraction.

- `with_openviking_context()` wraps LangChain runnables with session assembly, recall, capture, and optional commit policy.
- `OpenVikingContextMiddleware` gives LangGraph agents the same session lifecycle through middleware.
- `OpenVikingChatMessageHistory` stores LangChain history in OpenViking sessions.
- `create_openviking_tools()` exposes common `viking_*` tools for agents.
- `OpenVikingRetriever` returns LangChain `Document` objects from OpenViking `find` or `search`.
- `OpenVikingStore` implements LangGraph's `BaseStore` for durable user or agent state.

Install the optional dependencies:

```bash
pip install "openviking[langgraph]"
```

For retriever-only LangChain usage:

```bash
pip install "openviking[langchain]"
```

## Connection Setup

In production, configure OpenViking once, start `openviking-server`, and connect from the LangChain/LangGraph app:

```python
connection = {
    "url": "http://localhost:1933",
    "api_key": "...",
    "user_id": "user-123",
    "agent_id": "support-agent",
}
```

The application still owns its LLM calls. OpenViking owns context storage and processing. Embedding and VLM providers are configured in OpenViking, not in the LangChain or LangGraph app.

If `client`, `url`, and `path` are all omitted, the adapters create `SyncHTTPClient()` and load connection settings from the OpenViking CLI config.

Embedded local clients can be passed explicitly with `client=...` or `path=...` for tests, notebooks, and single-process scripts. The primary examples below use HTTP mode.

## LangChain Retriever

```python
from openviking.integrations.langchain import OpenVikingRetriever

retriever = OpenVikingRetriever(
    url="http://localhost:1933",
    api_key="...",
    target_uri=["viking://user/memories", "viking://resources"],
    search_mode="find",
    limit=6,
)

docs = retriever.invoke("What did the user decide about deployment color?")
```

Use `search_mode="search"` with `session_id=...` when you want OpenViking's session-aware retrieval. Use `content_mode="read"` to force full L2 reads, or keep the default `auto` mode to read L2 hits and use abstracts/overviews for higher-level hits.

The retriever is a LangChain compatibility surface. For OpenViking-managed context lifecycle, prefer `with_openviking_context()` or `OpenVikingContextMiddleware`.

## LangChain Context Backend

Use `with_openviking_context()` when OpenViking should own the agent context lifecycle, not just retrieval:

```python
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from openviking.integrations.langchain import (
    OpenVikingCommitPolicy,
    with_openviking_context,
)


def answer(messages):
    return AIMessage(content="Answer using the injected OpenViking context.")


chain = with_openviking_context(
    RunnableLambda(answer),
    url="http://localhost:1933",
    api_key="...",
    target_uri=["viking://user/memories", "viking://resources"],
    commit_policy=OpenVikingCommitPolicy(
        mode="pending_tokens",
        pending_token_threshold=8_000,
    ),
)

result = chain.invoke(
    [HumanMessage(content="What did we decide about deploys?")],
    config={"configurable": {"session_id": "agent-thread-123"}},
)
```

When `session_id` is omitted from `with_openviking_context()`, each invoke must pass the configured session ID. For one-off scripts or single-session apps, pass `session_id="..."` to `with_openviking_context()` and then call `invoke()` without config.

The wrapper calls `get_session_context()`, injects archive/recall context, stores the completed turn through `OpenVikingChatMessageHistory`, and commits when the configured policy is met. Use the same session ID across invocations when the conversation should share OpenViking working memory and archives.

For direct LangChain history usage:

```python
from langchain_core.runnables.history import RunnableWithMessageHistory
from openviking.integrations.langchain import OpenVikingChatMessageHistory

with_history = RunnableWithMessageHistory(
    runnable,
    lambda session_id: OpenVikingChatMessageHistory(
        session_id=session_id,
        url="http://localhost:1933",
        api_key="...",
    ),
)
```

`OpenVikingChatMessageHistory` stores text, tool calls/results, and context references as OpenViking `TextPart`, `ToolPart`, and `ContextPart` payloads where those details are available.

## Agent Tools

```python
from openviking.integrations.langchain import create_openviking_tools

tools = create_openviking_tools(
    url="http://localhost:1933",
    api_key="...",
    profile="agent",
)
```

The default agent profile includes:

- `viking_find`: quick semantic recall without session context.
- `viking_search`: session-aware hierarchical retrieval.
- `viking_browse`: list or glob OpenViking namespaces.
- `viking_read`: read one or more Viking URIs as L0 `abstract`, L1 `overview`, or full L2 `read` content.
- `viking_grep`: grep-style content search.
- `viking_archive_search`: search assembled session/archive context.
- `viking_archive_expand`: expand a specific committed session archive.
- `viking_store`: write conversation turns to an OpenViking session.
- `viking_add_resource`: import files, directories, URLs, or repositories.
- `viking_add_skill`: register reusable skills.
- `viking_health`: check OpenViking status.

The default `agent` profile is intended for trusted, app-controlled agents. Use `profile="retrieval"` or explicit `tool_names=[...]` when a model should only retrieve/read context.

`viking_forget` is intentionally not exposed by the default profile. Use `profile="admin"` or `allow_forget=True` only for trusted agents.

`viking_add_resource` exposes OpenViking resource ingestion to agents. OpenViking parses and indexes documents, repositories, URLs, and supported media according to the server's configured parsers, embedding model, and VLM model. Chat message capture focuses on text, tool, and context parts.

## LangGraph Store

```python
from openviking.integrations.langchain import OpenVikingStore

store = OpenVikingStore(
    url="http://localhost:1933",
    api_key="...",
    root_uri="viking://user/memories/langgraph_store",
)

store.put(("users", "ada"), "preferences", {"color": "azure"})
items = store.search(("users",), query="azure", limit=3)
```

The store writes JSON records under `<root_uri>/data` and a compact markdown index under `<root_uri>/index`. Query-based `search()` uses OpenViking `find()` over that index, then resolves the original JSON values.

Store writes wait for indexing by default so immediate query-based `search()` can see just-written values. Pass `wait=False` when asynchronous indexing is acceptable.

`OpenVikingStore` is for durable LangGraph store data. It is separate from LangGraph checkpointing.

## LangGraph Middleware

```python
from langchain.agents import create_agent
from openviking.integrations.langchain import (
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
)

middleware = OpenVikingContextMiddleware(
    url="http://localhost:1933",
    api_key="...",
    target_uri=["viking://user/memories", "viking://resources"],
    limit=5,
    capture_on_after_agent=True,
    commit_policy=OpenVikingCommitPolicy(
        mode="pending_tokens",
        pending_token_threshold=8_000,
    ),
)

agent = create_agent(
    model="...",
    tools=[],
    middleware=[middleware],
)
```

The middleware adds a marked OpenViking context block to the model system message. The block includes session archive overview and recall results; the active request messages are left as normal LangGraph messages to avoid duplication. After the agent finishes, the middleware captures new user, assistant, tool, and context parts into the OpenViking session.

If your graph passes only the latest request and relies on OpenViking for the session window, set `include_active_messages=True` on the middleware.

LangGraph `thread_id` is the natural OpenViking `session_id`. Reuse the same ID for both systems when you also configure a LangGraph checkpointer. OpenViking handles semantic context and memory; the checkpointer handles exact graph execution resume. Provide `session_id_resolver` if your graph uses a custom thread/session identifier.

The middleware requires a session identifier. If no `thread_id`, `session_id`, or custom resolver is available, it raises `ValueError` instead of writing to a shared default session.

## Try The Examples

The repository includes small deterministic examples that exercise real LangChain and LangGraph application flows without requiring model credentials or a running OpenViking server. They use an OpenViking-compatible in-memory test client so you can see how the adapters fit into an agent app before connecting to a real backend.

From the repository root:

```bash
uv run --extra langgraph python examples/langchain-langgraph/langchain/rag/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langchain/context-backend/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langchain/message-history/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langgraph/agent/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langgraph/middleware/quick_app.py
```

The examples cover:

- [LangChain RAG quick app](../../../examples/langchain-langgraph/langchain/rag/quick_app.py): LangChain RAG with `OpenVikingRetriever`.
- [LangChain context backend quick app](../../../examples/langchain-langgraph/langchain/context-backend/quick_app.py): LangChain session context injection with `with_openviking_context()`.
- [LangChain message history quick app](../../../examples/langchain-langgraph/langchain/message-history/quick_app.py): LangChain chat history backed by `OpenVikingChatMessageHistory`.
- [LangGraph agent quick app](../../../examples/langchain-langgraph/langgraph/agent/quick_app.py): LangGraph workflow using OpenViking tools and `OpenVikingStore`.
- [LangGraph middleware quick app](../../../examples/langchain-langgraph/langgraph/middleware/quick_app.py): LangGraph context injection and capture with `OpenVikingContextMiddleware`.

For a real OpenViking server and OpenAI-compatible model flow, see the [live LangGraph app](../../../examples/langchain-langgraph/langgraph/agent/live_app.py).
