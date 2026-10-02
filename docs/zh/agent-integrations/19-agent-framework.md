# Microsoft Agent Framework

使用 `OpenVikingContextProvider` 为 Python Microsoft Agent Framework（MAF）Agent
接入记忆。它在推理前检索上下文，并在完整轮次结束后记录对话。
应用决定何时提交对话，以提取长期记忆。

## 安装与连接

在 OpenViking 仓库目录中执行：

```bash
pip install ./examples/agent-framework
```

需要 Python 3.10+、MAF core 1.20+、OpenViking SDK 0.1.13+，以及运行中的
OpenViking 0.4.23+ 服务。依赖范围为 MAF core 1.x 和 SDK 0.1.x。
请另行安装应用使用的 MAF 模型客户端。

```python
from agent_framework import Agent
from openviking_sdk import AsyncHTTPClient
from openviking_agent_framework import OpenVikingContextProvider

# 放在应用的异步入口中：
client = AsyncHTTPClient(url=server_url, api_key=user_key, timeout=60)
await client.initialize()
try:
    memory = OpenVikingContextProvider(client)
    agent = Agent(chat_client, context_providers=[memory])
    session = agent.create_session()
    response = await agent.run("请记住，我偏好的输出格式是 JSON。", session=session)
    job = await memory.commit(session)
finally:
    await client.close()
```

`chat_client` 是应用现有的 MAF 模型客户端。`server_url` 和 `user_key` 由应用配置提供。
请使用普通用户密钥。[包 README](https://github.com/volcengine/OpenViking/tree/main/examples/agent-framework)
包含完整可运行示例、测试命令和恢复步骤。

## 职责与限制

- 自动召回：将服务端生成的上下文作为带来源标记的参考数据传入模型。
  服务端权限规则决定当前用户可检索的数据。
- 对话记录包含文本、函数调用及结果，不包含召回上下文、系统指令、隐藏推理或非文本媒体。
- 流式响应必须完整消费。中断的流不会被记录。
- `commit()` 表示任务已接受。使用 `get_task()` 轮询返回的 `task_id`，直到任务完成。
  服务端必须配置真实模型，才能提取记忆。
- 保存 `session.to_dict()`，并用 `AgentSession.from_dict()` 恢复。
  每个用户和 actor 使用独立的客户端及 provider。同一会话中的操作必须串行执行。
- 网络和服务端错误会向应用抛出。未确认的记录会阻止后续运行和提交。
  处理状态不明的写入后，才能用 `flush(session)` 重试。追加接口不具备幂等性；
  响应丢失时，应先检查当前记录及归档，避免重复写入。

该 provider 使用 MAF 的公开 `ContextProvider` API 和轻量 OpenViking HTTP SDK。
它不安装 MCP 工具，也不替代 MAF 的聊天历史存储。MAF 与 OpenViking 仍是独立的包。
AutoGen 需要单独的适配器。共享 CLI 插件的环境开关不配置此 provider。
