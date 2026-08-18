# LangChain 和 LangGraph

把 OpenViking 接入你的 LangChain 或 LangGraph Agent 作为上下文后端。独立集成包提供
retriever、chat history、context wrapper、agent tools、LangGraph store 和 middleware，
统一连接 OpenViking HTTP 服务。

## 安装

```bash
pip install langchain-openviking                 # LangChain 适配器
pip install "langchain-openviking[langgraph]"    # LangGraph middleware
```

该集成独立于 OpenViking server 发布。为兼容现有应用，完整包仍会把旧的
`openviking.integrations.langchain` 导入路径转发到 `langchain-openviking`。

## 连接

```python
from langchain_openviking import create_openviking_tools

tools = create_openviking_tools(
    url="http://localhost:1933",
    api_key="...",
    profile="agent",
)
```

省略 `url` 时，适配器会使用 OpenViking CLI 配置中的 HTTP 连接信息。Embedding 和 VLM
在 OpenViking 侧配置，不在你的应用中。

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

异步适配器支持两种 client 模式：

| 配置 | 异步接口 | 所有权 |
|------|----------|--------|
| `client=` 或 `async_client=` | 原样返回注入的 client | 调用方 |
| `url=`，或省略 | 每个 event loop 一个支持恢复的 HTTP handle | Adapter |

长期运行的应用可以初始化一个由调用方管理的异步 client，并在同一 event loop
内的多个适配器之间复用：

```python
from openviking_sdk import AsyncHTTPClient
from langchain_openviking import OpenVikingRetriever

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

`OpenVikingChatMessageHistory` 提供 `aget_messages()`、`aadd_messages()` 和
`aclear()`；`OpenVikingSessionRecorder` 提供 `arecord()`、`aflush()` 和
`aclose()`。异步 LangGraph 运行会自动选择 `awrap_model_call()` 和
`aafter_agent()`。同一 adapter 首次被并发调用时，每个 event loop 只会创建一个内部
HTTP client。通过 `with_openviking_context()` 执行的每次 runnable 调用都独立持有
本次写入所需的 history 快照、peer 身份和召回上下文引用。因此，同一 session 的调用
可以并发执行，不会因为
退出时再次读取实时 history 而丢失消息；未消费完的 stream 也不会占用 session 级
lifecycle lock。只有最终的 append-and-commit 步骤会被串行化：async 写入在每个
event loop 内按 session 串行执行，sync 写入则会跨线程按 session 串行执行。

如果 recorder 已确认部分写入后任务被取消，`arecord()` 会重新抛出原始
`asyncio.CancelledError`。可将该异常或外层的 `asyncio.TimeoutError` 传给
`get_openviking_cancellation_progress()`，在重试前读取已确认写入的消息前缀或待
commit 状态，避免重复写入。保留原始取消异常也会保留 `asyncio.wait_for()` 和
`asyncio.timeout()` 的标准超时行为。

Adapter 不会关闭调用方注入的 client。对于 adapter 自行创建的 client，应按实际使用的组件调用
`await retriever.aclose()`、`await assembler.aclose()`、
`await middleware.aclose()`、`await history.aclose()` 或
`await recorder.aclose()`。如果 async 操作完成后误调用同步
`recorder.close()`，该方法会抛出异常并保持 recorder 可用，以便后续
`await recorder.aclose()` 仍能释放全部资源。
如果条件允许，应在关闭 event loop 前关闭 HTTP-backed adapter；原始 loop 已结束后的
清理属于 best-effort。

`with_openviking_context()` 返回 `OpenVikingContextRunnable`。它兼容 LangChain 的
`RunnableWithMessageHistory`，并负责管理其创建的 context 和 recording adapter。
它会在多次调用之间复用按 event loop 隔离的 client，同时继续隔离每次调用的 history、
peer 身份和召回引用。推荐使用托管生命周期：

```python
async with with_openviking_context(runnable, url="http://localhost:1933") as chain:
    result = await chain.ainvoke(
        {"messages": [...]},
        config={"configurable": {"session_id": "support-thread-1"}},
    )
```

同步调用使用 `with ...`，也可以显式调用 `close()` 或 `await aclose()`。不要在正在运行的
event loop 中调用 `close()`，此时应使用 `aclose()`。注入的 client 仍由调用方管理。

LCEL 组合会返回普通的 `RunnableSequence`，不会暴露 OpenViking 的 close 方法。应保留
托管 wrapper，并在其生命周期内完成组合：

```python
async with with_openviking_context(runnable, url="http://localhost:1933") as managed:
    chain = managed | another_step
    result = await chain.ainvoke(...)
```

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

### 并发 Agent 的运行时 Actor Peer

`OpenVikingContextMiddleware` 可以在复用绑定凭证的 HTTP client 时，从每次
LangGraph 运行中解析当前 actor peer：

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

解析出的 actor peer 会作用于召回和捕获期间发出的 OpenViking HTTP 请求。并发运行
互相隔离，middleware 的捕获进度也会按 actor peer、session 和 message peer 共同
隔离。OpenViking 的 Session 接口仍然以 user 为作用域，不会使用 actor-peer header
标记消息归属；如果捕获的消息也需要归属于同一个逻辑 peer，应同时设置
`peer_id_resolver`。拥有独立历史的不同 peer 也应解析为不同的 session ID。未传入
`actor_peer_resolver` 时，现有固定 client 行为保持不变。

该 resolver 不能改变 OpenViking account 或 user；这些身份继续由 API Key 或 OAuth
凭证决定。因此，多用户应用必须先选择绑定对应用户凭证的 client，再调用 middleware。
Actor peer 只能从已经认证、由服务端控制的 runtime 字段中解析；不要信任 model state
或客户端可控的 configurable 值。运行时 actor-peer 解析仅支持 HTTP-backed
middleware。注入的自定义 client 必须设置
`supports_request_actor_peer = True`，并遵循 `openviking_sdk` 的 actor-peer
作用域。在已有环境中启用该能力前，应同时升级 `openviking-sdk` 和 `openviking`。

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
from langchain_openviking import OpenVikingRetriever

retriever = OpenVikingRetriever(url="http://localhost:1933", api_key="...")
docs = retriever.invoke("用户之前对部署方案做了什么决定？")
```

### Context backend

```python
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_openviking import with_openviking_context

with with_openviking_context(
    RunnableLambda(lambda msgs: AIMessage(content="...")),
    url="http://localhost:1933",
    api_key="...",
) as chain:
    result = chain.invoke(...)
```

### Agent tools

```python
from langchain_openviking import create_openviking_tools

tools = create_openviking_tools(url="http://localhost:1933", profile="agent")
# 包括：viking_find, viking_search, viking_browse, viking_read,
#       viking_grep, viking_store, viking_add_resource 等
```

### LangGraph store

```python
from langchain_openviking import OpenVikingStore

store = OpenVikingStore(url="http://localhost:1933", api_key="...")
store.put(("users", "ada"), "preferences", {"color": "azure"})
items = store.search(("users",), query="azure", limit=3)
```

### LangGraph middleware

```python
from langchain_openviking import OpenVikingContextMiddleware

middleware = OpenVikingContextMiddleware(
    url="http://localhost:1933",
    api_key="...",
    capture_on_after_agent=True,
)
```

### Session recorder

当应用已经自行管理会话生命周期，只需要复用 OpenViking 持久化能力时，可使用 recorder：

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
uv run --project integrations/langchain --extra langgraph python examples/langchain-langgraph/langchain/rag/quick_app.py
uv run --project integrations/langchain --extra langgraph python examples/langchain-langgraph/langchain/context-backend/quick_app.py
uv run --project integrations/langchain --extra langgraph python examples/langchain-langgraph/langchain/message-history/quick_app.py
uv run --project integrations/langchain --extra langgraph python examples/langchain-langgraph/langgraph/agent/quick_app.py
uv run --project integrations/langchain --extra langgraph python examples/langchain-langgraph/langgraph/middleware/quick_app.py
```

连接真实 OpenViking 服务和 OpenAI 兼容模型的示例见 [live LangGraph app](https://github.com/volcengine/OpenViking/blob/main/examples/langchain-langgraph/langgraph/agent/live_app.py)。

## 参见

- [examples/langchain-langgraph/](https://github.com/volcengine/OpenViking/tree/main/examples/langchain-langgraph) — 上面所有示例的完整源码
- [MCP 客户端](./06-mcp-clients.md) — 非 SDK 方式的 MCP 集成
