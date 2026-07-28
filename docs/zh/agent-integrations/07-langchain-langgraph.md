# LangChain 和 LangGraph

把 OpenViking 接入你的 LangChain 或 LangGraph Agent 作为上下文后端。SDK 提供 retriever、chat history、context wrapper、agent tools、LangGraph store 和 middleware，可连接 HTTP 服务或嵌入式 OpenViking。

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

同时省略 `url` 和 `path` 时，适配器会使用 OpenViking CLI 配置中的 HTTP 连接信息。传入 `path` 时，通过 OpenViking 同步 client 使用嵌入式 workspace。Embedding 和 VLM 在 OpenViking 侧配置，不在你的应用中。

### 异步应用

Retriever、context wrapper、chat history、session recorder 和 LangGraph
middleware 都支持原生异步路径。通过 URL 配置时，适配器会自动创建异步 OpenViking HTTP
client：

```python
docs = await retriever.ainvoke("用户之前做了什么决定？")
result = await chain.ainvoke(
    {"messages": [...]},
    config={"configurable": {"session_id": "support-thread-1"}},
)
```

异步适配器支持三种 client 模式：

| 配置 | 异步接口 | 所有权 |
|------|----------|--------|
| `client=` 或 `async_client=` | 原样返回注入的 client | 调用方 |
| `url=`，或同时省略 `url` 和 `path` | 每个 event loop 一个支持恢复的 HTTP handle | Adapter |
| `path=` | 在 worker thread 中调用同步嵌入式 client | Adapter |

长期运行的应用可以初始化一个由调用方管理的异步 client，并在同一 event loop
内的多个适配器之间复用：

```python
from openviking.client import AsyncHTTPClient
from openviking.integrations.langchain import OpenVikingRetriever

client = AsyncHTTPClient(url="http://localhost:1933", api_key="...")
await client.initialize()
try:
    retriever = OpenVikingRetriever(async_client=client)
    docs = await retriever.ainvoke("部署决定")
finally:
    await client.close()
```

注入的异步 client 会绑定到初始化它的 event loop。不要跨 event loop 共享同一个
注入异步 client；应为每个 loop 分别创建并管理 client。注入的同步 client 仍可安全地
用于异步 adapter 方法，因为调用会在 worker thread 中执行。

`path=` 嵌入式 adapter 使用同步 fallback 是有意设计：`SyncOpenViking` 会让有状态的
嵌入式引擎保持在 OpenViking 的共享后台 loop 上，同时不阻塞应用 event loop。若要使用
原生嵌入式异步方法，请自行创建并初始化 `AsyncOpenViking`，通过 `async_client=` 注入，
在同一个 event loop 中使用，并由调用方自行关闭。每个进程同时只能运行一个嵌入式
workspace；切换 workspace 前应先关闭或 reset 当前 client。

`OpenVikingChatMessageHistory` 提供 `aget_messages()`、`aadd_messages()` 和
`aclear()`；`OpenVikingSessionRecorder` 提供 `arecord()`、`aflush()` 和
`aclose()`。异步 LangGraph 运行会自动选择 `awrap_model_call()` 和
`aafter_agent()`。同一 adapter 首次被并发调用时，每个 event loop 只会创建一个内部
HTTP client。

Adapter 不会关闭调用方注入的 client。对于 adapter 自行创建的 client，应按实际使用的组件调用
`await retriever.aclose()`、`await assembler.aclose()`、
`await middleware.aclose()`、`await history.aclose()` 或
`await recorder.aclose()`。如果 async 操作完成后误调用同步
`recorder.close()`，该方法会抛出异常并保持 recorder 可用，以便后续
`await recorder.aclose()` 仍能释放全部资源。
如果条件允许，应在关闭 event loop 前关闭 HTTP-backed adapter；原始 loop 已结束后的
清理属于 best-effort。
`with_openviking_context()` 返回 LangChain 标准的
`RunnableWithMessageHistory`，而该类型没有 close hook。因此长期运行的异步应用使用此
helper 时，应按上例注入并关闭由调用方管理的异步 client。

## Peer 身份

传入 `actor_peer_id` 可以在文件系统和检索操作中过滤当前用户的 peer 集合。session message capture 仍可使用 `peer_id` 表达每条消息的说话人归属。

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

动态运行时，`with_openviking_context()` 默认仍会读取 `config["configurable"]["peer_id"]`，用于 captured message 的归属：

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
| 在自定义生命周期中记录调用方选定的 LangChain 消息 | `OpenVikingSessionRecorder` |

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

### Session recorder

当应用已经自行管理会话生命周期，只需要复用 OpenViking 持久化能力时，可使用 recorder：

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

`record()` 只写入调用方传入的消息；它会过滤框架控制消息、按服务端限制分批写入，并应用已配置的
commit 策略。如果后续批次或写入后的 commit 失败，`OpenVikingPartialWriteError` 会报告已经
确认写入的输入前缀，调用方可仅重试尚未写入的后缀；空后缀会安全地重试待完成的 commit。
传入 `context_parts` 时，仅在 `exc.context_attached` 为 false 时重传。`flush()` 仅在 session
存在待提交内容时强制 commit。`close()` 后 recorder 不可复用；由调用方注入的 client
仍归调用方管理。

异步生命周期使用对应的 `await recorder.arecord(...)`、
`await recorder.aflush(...)` 和 `await recorder.aclose()`。不要用
`recorder.close()` 结束异步生命周期。

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
