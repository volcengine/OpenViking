# 渠道、Gateway 与运行管理

Channels 负责把不同聊天平台适配为统一消息，Gateway 则把 Channels、AgentLoop、HTTP API、定时任务和观测能力组装为长期运行服务。

## 支持的渠道

| 类型 | 连接方式 | 主要能力 |
|------|----------|----------|
| `feishu` | 飞书事件/长连接 | 私聊、群聊、话题、@ 规则、媒体 |
| `slack` | Socket Mode | 私聊和群聊策略 |
| `telegram` | Bot API | 文本、媒体和音频转写 |
| `discord` | Gateway | 文本与媒体 |
| `whatsapp` | Node.js WebSocket bridge | WhatsApp 消息转发 |
| `dingtalk` | Stream SDK | 消息接收与回复 |
| `qq` | QQ Bot API | 消息接收与回复 |
| `email` | IMAP + SMTP | 轮询收件和自动回复 |
| `mochat` | Socket.IO / Watch API | 会话监听、@ 和延迟回复 |
| `openapi` / `bot_api` | FastAPI | HTTP Chat API 与 SSE |

CLI 交互使用 ChatChannel，单条命令使用 SingleTurnChannel，它们也沿用统一消息模型。

## Channel 的职责

BaseChannel 和具体平台实现共同负责：

1. 启停连接和报告运行状态；
2. 校验 `allow_from` 等发送者策略；
3. 提取文本、图片、附件和回复 metadata；
4. 生成 SessionKey 和 InboundMessage；
5. 将 OutboundMessage 转换为平台原生回复；
6. 按平台能力展示处理中状态或 reaction。

平台差异留在具体 Channel 内，AgentLoop 不依赖飞书、Slack 等 SDK。

ChannelManager 从 `bot.channels` 创建所有启用实例，并以 `type__channel_id` 区分同类型的多个 Bot。它消费 MessageBus 出站队列，根据 SessionKey 将回复路由到原渠道。

## Gateway 运行时

`vikingbot gateway` 在一个 asyncio 进程中启动：

```text
FastAPI / Uvicorn
  + OpenAPIChannel
  + 配置的聊天 Channels
  + MessageBus
  + AgentLoop
  + CronService
  + HeartbeatService
```

默认监听 `127.0.0.1:18790`。当 `gateway.host` 不是 localhost 时，必须设置 `bot.gateway.token`，否则 Gateway 拒绝启动。

## Bot HTTP API

Bot API 位于 `/bot/v1`：

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/bot/v1/health` | Bot 健康状态 |
| POST | `/bot/v1/chat` | 同步聊天 |
| POST | `/bot/v1/chat/stream` | SSE 流式聊天 |
| POST | `/bot/v1/chat/channel` | 调用指定 Bot Channel |
| POST | `/bot/v1/chat/channel/stream` | 流式调用指定 Bot Channel |
| POST | `/bot/v1/feedback` | 提交用户反馈 |
| GET/POST | `/bot/v1/sessions` | 列出或创建 API Session |
| GET/DELETE | `/bot/v1/sessions/{id}` | 查询或删除 API Session |

ChatRequest 支持 session ID、额外上下文、是否回复、请求级禁用工具和渠道 ID。ChatResponse 返回 response ID、最终文本、中间事件、相关记忆和 token usage。

SSE 会发送 reasoning、content delta、tool call、tool result、iteration 和最终 response 等事件。

## OpenViking API 代理

当配置 OpenViking Server 时，Gateway 还提供：

| 路径 | 作用 |
|------|------|
| `/health` | 汇总 Gateway 和 OpenViking upstream 状态 |
| `/api/v1/{path}` | 代理 OpenViking API |

代理会过滤 hop-by-hop headers，转发经过校验的身份头，并保持上游响应状态。详细连接与身份流程见 [与 OpenViking 集成](./04-openviking-integration.md)。

## 访问控制

Gateway 使用多层安全边界：

1. 非本地监听要求 `X-Gateway-Token`；
2. loopback 请求可以使用本地开发边界；
3. OpenViking API key 通过 upstream `/health` 验证身份和实际 auth mode；
4. 只有可信 OpenViking Server 代理才能传入 `openviking_connection`；
5. API Session 使用认证主体 scope 与外部 session ID 组合隔离。

普通请求字段中的 `user_id`、account ID 或 connection 信息不能自行证明 OpenViking 身份。

## 反馈与结果评估

每条最终回复都生成 `response_id`。客户端可以提交 thumb up、thumb down 或数值 rating，并附带原因与文本。Gateway 会保存反馈、计算反馈延迟并发布 `feedback_submitted` 事件。

当用户继续对话时，Outcome Evaluator 还可以根据后续行为评估上一条回复，形成 `response_outcome_evaluated` 事件。`vikingbot feedback-stats` 聚合本地 Session，统计反馈覆盖率、评分、结果状态、工具使用和延迟。

## Langfuse 与日志

设置 `bot.langfuse.enabled=true` 后，模型调用、token usage、耗时、工具事件和结果 metadata 会写入 Langfuse。Langfuse 初始化失败不会阻断 Bot 主链路。

运行日志使用 Loguru；Gateway 的 `--verbose` 可以开启更详细日志。分析专用事件与普通回复分离，不会被误发到聊天平台。

## 实现位置

| 内容 | 路径 |
|------|------|
| 渠道基类与管理 | `vikingbot/channels/base.py`、`manager.py` |
| 平台适配 | `vikingbot/channels/*.py` |
| Gateway/OpenAPI | `vikingbot/channels/openapi.py` |
| API 数据模型 | `vikingbot/channels/openapi_models.py` |
| 运行时组装 | `vikingbot/cli/commands.py` |
| 反馈与结果 | `vikingbot/observability/` |
| Langfuse | `vikingbot/integrations/langfuse.py` |

## 相关文档

- [VikingBot 架构](./01-architecture.md)
- [Agent 能力体系](./02-agent-capabilities.md)
- [与 OpenViking 集成](./04-openviking-integration.md)
- [渠道配置](../../CHANNEL.md)
