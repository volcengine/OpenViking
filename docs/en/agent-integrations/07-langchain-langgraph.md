# LangChain / LangGraph

Wire OpenViking into your LangChain or LangGraph agent as the context backend. The
standalone integration package provides a retriever, chat history, context wrapper,
agent tools, LangGraph store, and middleware for OpenViking HTTP deployments.

## Install

```bash
pip install langchain-openviking                 # LangChain adapters
pip install "langchain-openviking[langgraph]"    # LangGraph middleware support
```

The integration is released independently from the OpenViking server. Starting
with the next server release after v0.5.0, the legacy
`openviking.integrations.langchain` namespace is removed without a compatibility
shim. v0.5.0 still provides it. Install the standalone package and import from
`langchain_openviking` instead; see the [Unreleased migration notes](../about/02-changelog.md#unreleased).

## Connection

```python
from langchain_openviking import create_openviking_tools

tools = create_openviking_tools(
    url="http://localhost:1933",
    api_key="...",
    profile="agent",
)
```

When `url` is omitted, adapters use the HTTP connection settings from the OpenViking CLI config. Embedding and VLM providers are configured in OpenViking, not in your app.

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

Async adapters support two client modes:

| Configuration | Async interface | Ownership |
|---------------|-----------------|-----------|
| `client=` or `async_client=` | The injected client is returned unchanged | Caller |
| `url=`, or omitted | One recovery-capable HTTP handle per event loop | Adapter |

Long-lived applications can initialize one caller-owned async client and reuse
it across adapters running on the same event loop:

```python
from openviking_sdk import AsyncHTTPClient
from langchain_openviking import OpenVikingRetriever

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

`OpenVikingChatMessageHistory` provides `aget_messages()`, `aadd_messages()`,
and `aclear()`. `OpenVikingSessionRecorder` provides `arecord()`, `aflush()`,
and `aclose()`. Async LangGraph runs select `awrap_model_call()` and
`aafter_agent()` automatically. Concurrent first use creates one internal HTTP
client per adapter and event loop. Each runnable invocation through
`with_openviking_context()` owns the history snapshot, peer identity, and
recalled-context references used to record its turn. Concurrent calls may
therefore share a session without a
second live history read losing messages, and an abandoned stream does not hold
a session-wide lifecycle lock. Only the final append-and-commit step is
serialized: async writes are serialized per session within each event loop,
while synchronous writes are serialized per session across threads.

If cancellation occurs after a recorder has confirmed part of a write,
`arecord()` re-raises the original `asyncio.CancelledError`. Pass that exception,
or a wrapping `asyncio.TimeoutError`, to
`get_openviking_cancellation_progress()` to inspect the confirmed message
prefix or pending-commit state before retrying. Preserving the original
cancellation object also preserves the standard `asyncio.wait_for()` and
`asyncio.timeout()` timeout behavior.

Adapters never close an injected client. When an adapter creates its own client,
release it with `await retriever.aclose()`, `await assembler.aclose()`,
`await middleware.aclose()`, `await history.aclose()`, or
`await recorder.aclose()` as appropriate. Calling synchronous
`recorder.close()` after an async operation raises and intentionally leaves the
recorder open so `await recorder.aclose()` can still release every resource.
When possible, close HTTP-backed adapters before shutting down their event
loops; cleanup after an originating loop has already ended is best-effort.

`with_openviking_context()` returns an `OpenVikingContextRunnable`, a compatible
subclass of LangChain's `RunnableWithMessageHistory` that owns the context and
recording adapters it creates. It reuses their loop-scoped clients across
invocations while keeping invocation history, peer identity, and recalled
references isolated. Prefer a managed lifecycle:

```python
async with with_openviking_context(runnable, url="http://localhost:1933") as chain:
    result = await chain.ainvoke(
        {"messages": [...]},
        config={"configurable": {"session_id": "support-thread-1"}},
    )
```

Use `with ...` for synchronous calls, or call `close()` / `await aclose()`
explicitly. `close()` cannot run inside an active event loop; use `aclose()`
there. Injected clients remain caller-owned.

LCEL composition returns a plain `RunnableSequence`, which does not expose the
OpenViking close methods. Keep the managed wrapper and compose inside its
lifecycle:

```python
async with with_openviking_context(runnable, url="http://localhost:1933") as managed:
    chain = managed | another_step
    result = await chain.ainvoke(...)
```

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

### Runtime actor peers for concurrent agents

`OpenVikingContextMiddleware` can resolve the active actor peer from each
LangGraph run while reusing its credential-bound HTTP clients:

```python
from langchain_openviking import OpenVikingContextMiddleware


def resolve_actor_peer(_state, runtime):
    context = runtime.context or {}
    return context.get("actor_peer_id")


middleware = OpenVikingContextMiddleware(
    url="http://localhost:1933",
    api_key="user-api-key",
    actor_peer_resolver=resolve_actor_peer,
)
```

The resolved actor peer scopes OpenViking HTTP calls made during recall and
capture. Concurrent runs are isolated, and middleware capture progress is keyed
by actor peer as well as session and message peer. OpenViking session endpoints
remain user-scoped and do not use the actor-peer header for message attribution;
set `peer_id_resolver` as well when captured messages must be attributed to the
same logical peer. Distinct peers with independent histories should also resolve
distinct session IDs. When `actor_peer_resolver` is omitted, the existing fixed
client behavior is unchanged.

The resolver cannot change the OpenViking account or user. Those identities
remain bound to the API key or OAuth credential, so multi-user applications
must select a credential-bound client before invoking the middleware. Resolve
the actor peer only from authenticated, server-owned runtime fields; do not
trust model state or client-controlled configurable values. Runtime actor-peer
resolution is available only for HTTP-backed middleware. An injected custom client must set
`supports_request_actor_peer = True` and honor the `openviking_sdk` actor-peer
scope. Upgrade `openviking-sdk` together with `openviking` before enabling this
feature in an existing environment.

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
from langchain_openviking import OpenVikingRetriever

retriever = OpenVikingRetriever(url="http://localhost:1933", api_key="...")
docs = retriever.invoke("What did the user decide about deployment?")
```

### Context backend

```python
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_openviking import with_openviking_context

with with_openviking_context(
    RunnableLambda(lambda msgs: AIMessage(content="...")),
    url="http://localhost:1933",
    api_key="...",
) as chain:
    result = chain.invoke(
        [HumanMessage(content="Remember that I prefer short answers")],
        config={"configurable": {"session_id": "support-thread-1"}},
    )
```

### Agent tools

```python
from langchain_openviking import create_openviking_tools

tools = create_openviking_tools(url="http://localhost:1933", profile="agent")
# Includes: viking_find, viking_search, viking_browse, viking_read,
#           viking_grep, viking_store, viking_add_resource, and more
```

### LangGraph store

```python
from langchain_openviking import OpenVikingStore

store = OpenVikingStore(url="http://localhost:1933", api_key="...")
store.put(("users", "ada"), "preferences", {"color": "azure"})
items = store.search(("users",), query="azure", limit=3)
store.put(("users", "ada"), "preferences", None)  # a None value deletes the item
```

Following LangGraph's `BaseStore` contract, passing `None` as the value deletes the item and is
equivalent to `delete()`. Deleting a key that does not exist is a no-op.

`put()` validates the namespace before changing records. Other removal errors propagate to
the caller. Record and search-projection removal are separate operations: if only one
succeeds, resolve the reported error and retry the operation to finish cleanup.

Record reads return `None` only for a missing record; permission, backend, and invalid-JSON
errors propagate, and `put()` does not overwrite a record after a failed read.
`list_namespaces(prefix=("users", "*"))` matches one namespace label per `*`, including
when `root_uri` uses a peer home alias such as `viking://~/peers/support/memories/langgraph_store`.

`search()` accepts nested JSON filters such as `filter={"metadata": {"color": "blue"}}`.
Filter keys `eq`, `ne`, `gt`, `gte`, `lt`, `lte` and `in` are reserved operator aliases at every nesting level.
Use `index=["items[*].text"]` to index selected array fields while retaining the complete
stored value. Existing index projections are updated when the item is written again.

### LangGraph middleware

```python
from langchain_openviking import OpenVikingContextMiddleware

middleware = OpenVikingContextMiddleware(
    url="http://localhost:1933",
    api_key="...",
    capture_on_after_agent=True,
)
```

The middleware prepends recalled context to a copy of the current user message
for the model request. System instructions and stored conversation messages
remain unchanged. Recall is reused within tool loops and refreshed for each new
user message.

With a LangGraph checkpointer, the middleware saves exact recall blocks in
internal graph state and replays them with their original messages after later
turns or a restart. This preserves unchanged prompt prefixes for provider caching;
actual cache hits depend on the provider. The ledger retains up to 500 entries
per session/peer scope and follows the application's checkpoint retention policy.
Pending structured context references are also checkpointed until their assistant
message is captured, so resuming an interrupted tool loop preserves provenance.

### Session recorder

Use the recorder when your application already owns the conversation lifecycle
and only needs reusable OpenViking persistence:

```python
from langchain_openviking import (
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

Pass `commit_policy=` to `record()` or `arecord()` to override the recorder's
policy for that call without changing its default. `None` uses the default;
`OpenVikingCommitPolicy(mode="never")` suppresses a new post-write commit, but
still retries any previously failed commit. `history.clear()` and `aclear()`
raise if deletion or recreation fails; a failed recreation leaves the old
session deleted and does not acknowledge pending context references.

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
uv run --project integrations/frameworks/langchain --extra langgraph python integrations/frameworks/langchain/examples/langchain/rag/quick_app.py
uv run --project integrations/frameworks/langchain --extra langgraph python integrations/frameworks/langchain/examples/langchain/context-backend/quick_app.py
uv run --project integrations/frameworks/langchain --extra langgraph python integrations/frameworks/langchain/examples/langchain/message-history/quick_app.py
uv run --project integrations/frameworks/langchain --extra langgraph python integrations/frameworks/langchain/examples/langgraph/agent/quick_app.py
uv run --project integrations/frameworks/langchain --extra langgraph python integrations/frameworks/langchain/examples/langgraph/middleware/quick_app.py
```

For a real OpenViking server and OpenAI-compatible model flow, see the [live LangGraph app](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/langchain/examples/langgraph/agent/live_app.py).

## See also

- [Capability Reference](./16-capability-reference.md)
- [integrations/frameworks/langchain/examples/](https://github.com/volcengine/OpenViking/tree/main/integrations/frameworks/langchain/examples) — full source for all examples above
- [MCP Clients](./06-mcp-clients.md) — for non-SDK MCP integration
