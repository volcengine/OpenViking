# LangChain 和 LangGraph

OpenViking 可以作为 LangChain 与 LangGraph Agent 的上下文后端使用。该集成提供框架原生的 retriever、chat history、runnable wrapper、LangGraph middleware、LangGraph store，以及 `viking_*` tools。

## 安装

```bash
pip install "openviking[langchain]"
pip install "openviking[langgraph]"
```

如果同时使用 LangChain 和 LangGraph，可以一起安装：

```bash
pip install "openviking[langchain,langgraph]"
```

## 连接 OpenViking

本集成默认连接 `http://localhost:1933`。远程服务需要传入 `api_key`、`account`、`user_id`、`agent_id` 等连接参数，或使用环境变量和应用自己的配置层传入这些值。

```python
from openviking.integrations.langchain import create_openviking_tools

tools = create_openviking_tools(
    url="http://localhost:1933",
    profile="agent",
)
```

## 常见用法

- 使用 `OpenVikingRetriever` 把 OpenViking 检索结果接入 LangChain RAG。
- 使用 `create_openviking_tools()` 暴露 `viking_find`、`viking_search`、`viking_browse`、`viking_read`、`viking_grep`、`viking_store` 等工具。
- 使用 `with_openviking_context(...)` 在 LangChain runnable 前自动装配 OpenViking session context，并在模型调用后捕获对话。
- 使用 `OpenVikingContextMiddleware` 在 LangGraph agent 中注入上下文、捕获消息，并按 commit policy 触发 session commit。该 middleware 需要明确的 `thread_id`、`session_id` 或自定义 `session_id_resolver`，不会回退到共享默认 session。
- 使用 `OpenVikingStore` 作为 LangGraph `BaseStore`，把跨线程记忆存入 OpenViking。

## 示例

仓库内提供了可直接运行的最小示例：

- `examples/langchain-langgraph/langchain/rag/quick_app.py`
- `examples/langchain-langgraph/langchain/context-backend/quick_app.py`
- `examples/langchain-langgraph/langchain/message-history/quick_app.py`
- `examples/langchain-langgraph/langgraph/agent/quick_app.py`
- `examples/langchain-langgraph/langgraph/agent/live_app.py`
- `examples/langchain-langgraph/langgraph/middleware/quick_app.py`

更完整的英文说明见 [LangChain and LangGraph](../../en/agent-integrations/05-langchain-langgraph.md)。
