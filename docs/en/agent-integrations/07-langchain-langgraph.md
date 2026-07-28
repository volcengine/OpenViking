# LangChain and LangGraph

Wire OpenViking into your LangChain or LangGraph agent as the context backend. The SDK provides a retriever, chat history, context wrapper, agent tools, LangGraph store, and middleware for HTTP-backed or embedded OpenViking deployments.

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

When both `url` and `path` are omitted, adapters use the HTTP connection settings from the OpenViking CLI config. Pass `path` to use an embedded workspace through OpenViking's synchronous client. Embedding and VLM providers are configured in OpenViking, not in your app.

### Async applications

The retriever, context wrapper, chat history, session recorder, and LangGraph
middleware all have native async paths. URL-based adapters create an async
OpenViking HTTP client automatically:

```python
docs = await retriever.ainvoke("What did the user decide?")
result = await chain.ainvoke(
    {"messages": [...]},
    config={"configurable": {"session_id": "support-thread-1"}},
)
```

Async adapters support three client modes:

| Configuration | Async interface | Ownership |
|---------------|-----------------|-----------|
| `client=` or `async_client=` | The injected client is returned unchanged | Caller |
| `url=`, or neither `url` nor `path` | One recovery-capable HTTP handle per event loop | Adapter |
| `path=` | A synchronous embedded client invoked in a worker thread | Adapter |

Long-lived applications can initialize one caller-owned async client and reuse
it across adapters running on the same event loop:

```python
from openviking.client import AsyncHTTPClient
from openviking.integrations.langchain import OpenVikingRetriever

client = AsyncHTTPClient(url="http://localhost:1933", api_key="...")
await client.initialize()
try:
    retriever = OpenVikingRetriever(async_client=client)
    docs = await retriever.ainvoke("deployment decision")
finally:
    await client.close()
```

Injected async clients are bound to the event loop that initializes them. Do
not share one injected async client across event loops; create and manage one
client per loop instead. An injected synchronous client remains safe to use
from async adapter methods because its calls run in a worker thread.

For embedded `path=` adapters, the synchronous fallback is intentional:
`SyncOpenViking` keeps the stateful embedded engine on OpenViking's shared
background loop while the application event loop remains non-blocking. To use
native embedded async methods, construct and initialize `AsyncOpenViking`
yourself, inject it with `async_client=`, use it from that same event loop, and
close it yourself. Only one embedded workspace can be live per process; close
or reset it before selecting another workspace.

`OpenVikingChatMessageHistory` provides `aget_messages()`, `aadd_messages()`,
and `aclear()`. `OpenVikingSessionRecorder` provides `arecord()`, `aflush()`,
and `aclose()`. Async LangGraph runs select `awrap_model_call()` and
`aafter_agent()` automatically. Concurrent first use creates one internal HTTP
client per adapter and event loop.

Adapters never close an injected client. When an adapter creates its own client,
release it with `await retriever.aclose()`, `await assembler.aclose()`,
`await middleware.aclose()`, `await history.aclose()`, or
`await recorder.aclose()` as appropriate. Calling synchronous
`recorder.close()` after an async operation raises and intentionally leaves the
recorder open so `await recorder.aclose()` can still release every resource.
When possible, close HTTP-backed adapters before shutting down their event
loops; cleanup after an originating loop has already ended is best-effort.
`with_openviking_context()` returns LangChain's standard
`RunnableWithMessageHistory`, which has no close hook. Long-lived async
applications using that helper should therefore inject and close a
caller-owned async client as shown above.

## Peer Identity

Pass `actor_peer_id` to filter the current user's peer collection for filesystem and retrieval operations. Session message capture can still use `peer_id` for per-message speaker attribution.

```python
retriever = OpenVikingRetriever(
    url="http://localhost:1933",
    actor_peer_id="assistant-a",
)

chain = with_openviking_context(
    runnable,
    session_id="support-thread-1",
    actor_peer_id="assistant-a",
)
```

For dynamic runs, `with_openviking_context()` still reads `config["configurable"]["peer_id"]` by default for captured message attribution:

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
| Record caller-selected LangChain messages from a custom lifecycle | `OpenVikingSessionRecorder` |

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

### Session recorder

Use the recorder when your application already owns the conversation lifecycle
and only needs reusable OpenViking persistence:

```python
from openviking.integrations.langchain import (
    OpenVikingPartialWriteError,
    OpenVikingSessionRecorder,
)

recorder = OpenVikingSessionRecorder(url="http://localhost:1933", api_key="...")
try:
    recorder.record("support-thread-1", messages, peer_id="assistant-a")
except OpenVikingPartialWriteError as exc:
    recorder.record(
        "support-thread-1",
        messages[exc.input_messages_consumed :],
        peer_id="assistant-a",
    )
recorder.flush("support-thread-1")
recorder.close()
```

`record()` writes only the messages supplied by the caller. It filters framework
control messages, writes in server-safe batches, and applies the configured
commit policy. If a later batch or the post-write commit fails,
`OpenVikingPartialWriteError` reports the confirmed input prefix so callers can
retry only the unwritten suffix; an empty suffix safely retries a pending
commit. When supplying `context_parts`, resend them only if
`exc.context_attached` is false. `flush()` forces a commit only when the session
has pending content. After `close()`, the recorder cannot be reused; injected
clients remain owned by the caller.

For async lifecycles, use the equivalent `await recorder.arecord(...)`,
`await recorder.aflush(...)`, and `await recorder.aclose()` methods. Do not
finish an async lifecycle with `recorder.close()`.

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
