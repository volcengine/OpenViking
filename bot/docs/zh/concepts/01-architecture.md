# VikingBot 架构

VikingBot 是 OpenViking 仓库内的多渠道 AI Agent 运行时。它将命令行、聊天平台和 HTTP API 的输入转换为统一消息，由 Agent 完成上下文构建、模型推理和工具调用，再把结果交付回原渠道。

## 系统概览

```text
┌──────────────────────────────────────────────────────────────────┐
│ CLI │ Feishu │ Slack │ Telegram │ Discord │ Email │ HTTP API   │
└─────────────────────────────┬────────────────────────────────────┘
                              │ InboundMessage
                    ┌─────────▼─────────┐
                    │    MessageBus     │
                    │   入站/出站队列     │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │     AgentLoop     │
                    │ 上下文 → 模型 → 工具 │
                    └───┬─────┬─────┬───┘
                        │     │     │
                ┌───────▼┐ ┌──▼───┐ ┌▼───────────────┐
                │Session │ │Tools │ │  OpenViking    │
                │对话历史 │ │Skills│ │资源/记忆/经验   │
                └────────┘ └──┬───┘ └────────────────┘
                              │
                        ┌─────▼─────┐
                        │  Sandbox  │
                        │ 文件与命令 │
                        └───────────┘
```

## 核心模块

| 模块 | 职责 | 主要实现 |
|------|------|----------|
| **Channels** | 适配平台事件、媒体、权限和回复格式 | `vikingbot/channels/` |
| **MessageBus** | 解耦消息接收、Agent 执行和结果交付 | `vikingbot/bus/` |
| **AgentLoop** | 驱动模型与工具多轮迭代 | `vikingbot/agent/loop.py` |
| **Context** | 组装身份、工作区、Skill、记忆与历史 | `vikingbot/agent/context.py` |
| **Providers** | 统一模型、流式输出和工具调用协议 | `vikingbot/providers/` |
| **Tools & Sandbox** | 提供可执行能力及其运行边界 | `vikingbot/agent/tools/`、`vikingbot/sandbox/` |
| **Session** | 缓存并持久化 Bot 对话状态 | `vikingbot/session/` |
| **OpenViking** | 提供资源、长期记忆、经验和会话沉淀 | `vikingbot/openviking_mount/`、`vikingbot/hooks/` |
| **Gateway** | 运行多渠道服务并提供 HTTP API | `vikingbot/channels/openapi.py` |

## 一条消息的主链路

```text
平台事件
  → Channel 鉴权并提取文字/媒体
  → 生成 SessionKey 和 InboundMessage
  → MessageBus 入站队列
  → AgentLoop 加载 Session
  → ContextBuilder 构建系统提示和历史
  → Provider 调用模型
      ├─ 返回文本：形成最终回复
      └─ 返回工具调用：ToolRegistry 执行后继续调用模型
  → 保存本地 Session，并按策略同步 OpenViking
  → MessageBus 出站队列
  → Channel 交付回复
```

Agent 处理过程中还会产生 reasoning、content delta、tool call、tool result 和 iteration 等中间事件。OpenAPI 可以把它们作为 SSE 流返回，普通渠道可以只交付最终回复。

## 消息与会话标识

所有渠道共享两个消息结构：

| 结构 | 主要内容 |
|------|----------|
| `InboundMessage` | 发送者、文本、媒体、SessionKey、渠道 metadata、OpenViking 身份 |
| `OutboundMessage` | 文本、事件类型、回复目标、媒体、token usage、response ID |

`SessionKey` 由 `type + channel_id + chat_id` 组成，既是会话隔离键，也是出站路由和工作区选择依据。持久化时编码为 `type__channel_id__chat_id`。

## Agent 执行循环

每次模型调用可能返回普通文本或一个/多个工具调用。AgentLoop 会：

1. 根据当前渠道和请求计算可见工具；
2. 调用 Provider，并发布流式事件；
3. 将工具名和参数交给 ToolRegistry 校验；
4. 在 ToolContext 中注入 SessionKey、发送者、沙箱和 OpenViking 连接；
5. 把工具结果追加到当前消息上下文；
6. 再次调用模型，直到生成最终回答或达到 `max_tool_iterations`。

队列模式由单个 AgentLoop 消费者按入站顺序处理消息。CLI、Cron 和 Heartbeat 也可以通过 `process_direct()` 直接触发相同的执行逻辑。

## 模型适配

Provider 层向 AgentLoop 提供统一的 `chat()` 和 `chat_stream()` 接口，并归一化：

- 最终文本和 reasoning；
- 流式文本、推理和工具参数增量；
- Provider 特有的 system message 和 thinking 参数；
- `prompt_tokens`、`completion_tokens` 和 `total_tokens`。

通用模型由 LiteLLMProvider 适配；OpenViking VLM 通过 VLMProviderAdapter 接入相同接口。模型默认读取根级 `vlm` 配置，`bot.agents` 可以覆盖 Bot 专用参数。

## 本地 Session

本地 Session 保存 user、assistant、tool 和分析事件，使用 JSONL 持久化到 Bot 数据目录的 `sessions/`。SessionManager 维护内存缓存，并使用 SessionKey 级异步锁保护写入。

本地 Session 与 OpenViking Session 职责不同：前者保证 Bot 对话可以继续运行，后者用于归档、摘要、长期记忆和经验提取。详见 [与 OpenViking 集成](./04-openviking-integration.md)。

## 运行入口

| 入口 | 用途 | 启动内容 |
|------|------|----------|
| `vikingbot chat` / `ov chat` | 单次或交互式对话 | CLI Channel、AgentLoop、Session、Sandbox |
| `vikingbot gateway` | 长期运行服务 | Gateway、配置渠道、AgentLoop、Cron、Heartbeat、OpenAPI |

VikingBot 与 OpenViking 共用 `ov.conf`。Bot 配置位于 `bot` 字段，`OPENVIKING_CONFIG_FILE` 可以指定其他配置文件。

## 运行模式

| 模式 | 行为 |
|------|------|
| `normal` | 正常执行模型、工具和记忆流程 |
| `readonly` | 不注册 OpenViking 资源写入工具，不执行主动记忆固化 |
| `debug` | 只记录收到的用户消息，不执行模型推理和回复 |

## 相关文档

- [Agent 能力体系](./02-agent-capabilities.md)
- [渠道、Gateway 与运行管理](./03-channels-and-gateway.md)
- [与 OpenViking 集成](./04-openviking-integration.md)
