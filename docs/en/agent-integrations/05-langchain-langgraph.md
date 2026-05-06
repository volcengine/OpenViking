# LangChain and LangGraph

OpenViking can be configured as the context backend for LangChain and LangGraph agents through optional Python adapters:

- `OpenVikingRetriever` returns LangChain `Document` objects from OpenViking `find` or `search`.
- `create_openviking_tools()` exposes common `viking_*` tools for agents.
- `OpenVikingStore` implements LangGraph's `BaseStore` for durable user or agent state.
- `OpenVikingContextMiddleware` injects OpenViking recall before LangGraph model calls and can capture completed turns into OpenViking sessions.

Install the optional dependencies:

```bash
pip install "openviking[langgraph]"
```

For retriever-only LangChain usage:

```bash
pip install "openviking[langchain]"
```

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
- `viking_read`: read one or more Viking URIs.
- `viking_grep`: grep-style content search.
- `viking_store`: write conversation turns to an OpenViking session.
- `viking_add_resource`: import files, directories, URLs, or repositories.
- `viking_add_skill`: register reusable skills.
- `viking_health`: check OpenViking status.

`viking_forget` is intentionally not exposed by the default profile. Use `profile="admin"` or `allow_forget=True` only for trusted agents.

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

## LangGraph Middleware

```python
from langchain.agents import create_agent
from openviking.integrations.langchain import OpenVikingContextMiddleware

middleware = OpenVikingContextMiddleware(
    url="http://localhost:1933",
    api_key="...",
    target_uri=["viking://user/memories", "viking://resources"],
    limit=5,
    capture_on_after_agent=True,
    commit_on_after_agent=False,
)

agent = create_agent(
    model="...",
    tools=[],
    middleware=[middleware],
)
```

The middleware adds a marked OpenViking context block to the model system message. After the agent finishes, it can append new user/assistant messages to an OpenViking session. Provide `session_id_resolver` if your graph uses a custom thread/session identifier.

## Smoke Tests

The repository includes deterministic smoke apps that exercise real LangChain and LangGraph workflows without requiring credentials:

```bash
uv run --no-project \
  --with "langchain>=1.0.0,<2.0.0" \
  --with "langchain-core>=1.0.0,<2.0.0" \
  --with "langgraph>=1.0.0,<2.0.0" \
  --with pytest \
  --with pytest-asyncio \
  python -m pytest \
    -o addopts='' \
    --confcutdir=tests/unit \
    tests/unit/test_langchain_integration.py

uv run --no-project \
  --with "langchain>=1.0.0,<2.0.0" \
  --with "langchain-core>=1.0.0,<2.0.0" \
  --with "langgraph>=1.0.0,<2.0.0" \
  --with pytest \
  --with pytest-asyncio \
  python -m pytest \
    -o addopts='' \
    --confcutdir=tests/integration/langchain_langgraph \
    tests/integration/langchain_langgraph/test_smoke.py
```

There is also an env-gated live LLM lane for manual CI runs:

```bash
export OPENVIKING_LANGGRAPH_LIVE=1
export ARK_API_KEY=...
export ARK_BASE_URL=https://ark-cn-beijing.bytedance.net/api/v3
export ARK_MODEL=doubao-seed-2-0-code-preview-260215

uv run --no-project \
  --with "langchain>=1.0.0,<2.0.0" \
  --with "langchain-core>=1.0.0,<2.0.0" \
  --with "langgraph>=1.0.0,<2.0.0" \
  --with openai \
  --with pytest \
  --with pytest-asyncio \
  python -m pytest \
    -o addopts='' \
    --confcutdir=tests/integration/langchain_langgraph \
    tests/integration/langchain_langgraph/test_live.py
```

By default the live lane uses an in-memory OpenViking-compatible backend so it only verifies the live LLM path plus LangGraph wiring. Set `OPENVIKING_LIVE_BACKEND=http` with `OPENVIKING_URL` and `OPENVIKING_API_KEY`, or `OPENVIKING_LIVE_BACKEND=local` with `OPENVIKING_PATH`, to run the same lane against a real OpenViking backend.
