# langchain-openviking

> **Working memory is now opt-in.** Update installed plugins separately from the OV server, then restart the host. Existing explicit settings still take precedence. See the [default-off upgrade guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/working-memory-default-off.md) for native history, re-enabling WM, and old-conversation handoffs.

`langchain-openviking` is the official OpenViking integration package for
LangChain and LangGraph applications. It keeps framework-specific adapters
separate from the OpenViking server and communicates with remote OpenViking
instances through the lightweight `openviking-sdk` package.

> **OpenViking Server requirement**: examples here use the `viking://~` home alias
> (for example `viking://~/memories`), which the server expands to the authenticated
> caller's own user space, so they require a server with `viking://~` support. The
> uid-less `viking://user/memories` shorthand is rejected by newer servers; pass an
> explicit `viking://user/<uid>/...` URI when targeting another user.

## Installation

For LangChain retrievers, tools, message history, and context wrappers:

```bash
pip install langchain-openviking
```

For the LangGraph store and middleware:

```bash
pip install "langchain-openviking[langgraph]"
```

## Quick start

```python
from langchain_openviking import OpenVikingRetriever
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-api-key",
)
client.initialize()
retriever = OpenVikingRetriever(
    client=client,
    target_uri="viking://~/memories",
)

try:
    documents = retriever.invoke("What deployment preferences should I remember?")
finally:
    client.close()
```

The package also provides `OpenVikingSessionRecorder`,
`OpenVikingContextMiddleware`, `OpenVikingStore`,
`OpenVikingChatMessageHistory`, and `create_openviking_tools`.

## Client ownership

- A client supplied through `client=` or `async_client=` remains caller-owned.
- Clients created from `url=` are managed by the adapter and can be closed with
  `close()` or `aclose()` as documented by each adapter.

## Import migration

Starting with the next server release after v0.5.0, the legacy
`openviking.integrations.langchain` namespace is removed without a compatibility
shim. v0.5.0 still provides it. Install the standalone package and import from
`langchain_openviking` instead; see the [Unreleased migration notes](https://github.com/volcengine/OpenViking/blob/main/docs/en/about/02-changelog.md#unreleased).

```python
from langchain_openviking import OpenVikingRetriever
```

See the [OpenViking documentation](https://openviking.ai), [examples](https://github.com/volcengine/OpenViking/tree/main/integrations/frameworks/langchain/examples),
and the [framework adapter guide](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/README.md).

## Validate

From the repository root:

```bash
pip install -e 'integrations/frameworks/langchain[langgraph,test,dev]'
python -m pytest -q --noconftest -o addopts='' \
  integrations/frameworks/langchain/tests tests/unit/test_langchain_package_boundary.py
```

Package metadata and source headers declare AGPL-3.0; see [LICENSE](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/langchain/LICENSE).
The runnable [examples](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/langchain/examples/LICENSE) retain Apache-2.0.
