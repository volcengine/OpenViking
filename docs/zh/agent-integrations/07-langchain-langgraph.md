# LangChain 和 LangGraph

把 OpenViking 接入你的 LangChain 或 LangGraph Agent 作为上下文后端。SDK 提供 retriever、chat history、context wrapper、agent tools、LangGraph store 和 middleware——全部通过 HTTP 连接运行中的 OpenViking 服务。

## 安装

```bash
pip install "openviking[langchain]"       # retriever + chat history
pip install "openviking[langgraph]"       # 完整 LangGraph 支持（包含 langchain）
```

## 连接

```python
from openviking.integrations.langchain import create_openviking_tools

tools = create_openviking_tools(
    url="http://localhost:1933",
    api_key="...",
    profile="agent",
)
```

省略 `url` 时，适配器自动从 OpenViking CLI 配置加载连接信息。Embedding 和 VLM 在 OpenViking 侧配置，不在你的应用中。

## Peer 身份

所有会读写 session memory 的适配器都支持 `peer_id`。当同一个 OpenViking user 需要和多个稳定外部对象交互时使用它，例如不同 agent、机器人，或共享会话中的不同人。

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

动态运行时，`with_openviking_context()` 默认也会读取 `config["configurable"]["peer_id"]`：

```python
chain.invoke(
    {"messages": [...]},
    config={"configurable": {"session_id": "support-thread-1", "peer_id": "assistant-a"}},
)
```

## 选哪个适配器？

| 我想… | 用这个 |
|-------|--------|
| 为 RAG 检索相关上下文 | `OpenVikingRetriever` |
| 包装 runnable，自动召回 + 捕获 + 按策略 commit | `with_openviking_context()` |
| 给 agent 暴露显式记忆工具 | `create_openviking_tools()` |
| 存储跨线程的持久化状态 | `OpenVikingStore` |
| 在 LangGraph 中以 middleware 注入上下文 | `OpenVikingContextMiddleware` |
| 用 OpenViking 存储 LangChain 聊天记录 | `OpenVikingChatMessageHistory` |

## 快速示例

### Retriever

```python
from openviking.integrations.langchain import OpenVikingRetriever

retriever = OpenVikingRetriever(url="http://localhost:1933", api_key="...")
docs = retriever.invoke("用户之前对部署方案做了什么决定？")
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
# 包括：viking_find, viking_search, viking_browse, viking_read,
#       viking_grep, viking_store, viking_add_resource 等
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

## 运行示例

仓库内提供了可直接运行的最小示例，使用内存测试客户端，无需模型凭证：

```bash
uv run --extra langgraph python examples/langchain-langgraph/langchain/rag/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langchain/context-backend/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langchain/message-history/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langgraph/agent/quick_app.py
uv run --extra langgraph python examples/langchain-langgraph/langgraph/middleware/quick_app.py
```

连接真实 OpenViking 服务和 OpenAI 兼容模型的示例见 [live LangGraph app](https://github.com/volcengine/OpenViking/blob/main/examples/langchain-langgraph/langgraph/agent/live_app.py)。

## 参见

- [examples/langchain-langgraph/](https://github.com/volcengine/OpenViking/tree/main/examples/langchain-langgraph) — 上面所有示例的完整源码
- [MCP 客户端](./06-mcp-clients.md) — 非 SDK 方式的 MCP 集成
