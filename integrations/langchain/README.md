# langchain-openviking

`langchain-openviking` is the official OpenViking integration package for
LangChain and LangGraph applications. It keeps framework-specific adapters
separate from the OpenViking server and communicates with remote OpenViking
instances through the lightweight `openviking-sdk` package.

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
    target_uri="viking://user/memories",
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

The previous `openviking.integrations.langchain` import path remains available
from the full `openviking` distribution as a compatibility shim.

See the [OpenViking documentation](https://openviking.ai) and the repository's
`examples/langchain-langgraph` directory for complete examples.
