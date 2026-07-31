# VikingBot：基于 OpenViking 的多渠道 AI Agent

VikingBot 是 OpenViking 提供的多渠道 AI Agent。OpenViking 负责统一管理 Resource、Memory 和 Skill 等长期上下文；VikingBot 负责接收用户消息、组织上下文、调用模型和工具，并把任务结果交付回命令行、聊天平台或 HTTP 客户端。

两者组合后，Agent 不仅能完成当前任务，还能持续积累用户记忆、会话摘要和任务经验，在后续任务中再次使用。

## VikingBot 与 OpenViking 的分工

| 组件 | 主要职责 | 典型能力 |
|------|----------|----------|
| **OpenViking** | 上下文存储、组织和检索 | Resource、Memory、Skill、Session、语义检索、记忆与经验提取 |
| **VikingBot** | Agent 运行和交互 | 多渠道消息、模型推理、工具调用、Skill 执行、沙箱、自动化、结果交付 |

## 系统概览

```text
CLI / Feishu / Slack / Telegram / Discord / Email / HTTP API
                              │
                              ▼
                    Channel + MessageBus
                              │
                              ▼
                         AgentLoop
                 上下文 → 模型 → 工具 → 模型
                    │                   │
          ┌─────────┴─────────┐         ▼
          ▼                   ▼       回复与事件
  OpenViking Context     Tools / Skills
  Resource / Memory      Files / Shell / Web
  Experience / Session   MCP / Cron / Subagent
          │                   │
          └─────────┬─────────┘
                    ▼
             Session 同步与经验沉淀
```

所有入口最终使用同一套 AgentLoop。渠道差异被转换为统一消息，模型和工具无需感知消息来自命令行、飞书还是 HTTP API。

## 核心能力

### 多入口与多渠道

VikingBot 支持三类入口：

- `vikingbot chat` 和 `ov chat`：单次调用或交互式命令行对话；
- Feishu、Slack、Telegram、Discord、WhatsApp、DingTalk、QQ、Email 和 MoChat：长期运行的聊天机器人；
- `/bot/v1` HTTP API：同步 Chat、SSE 流式事件、Session 和反馈接口。

每个渠道负责平台鉴权、发送者白名单、媒体解析、回复格式和会话路由。VikingBot 使用 `type + channel_id + chat_id` 隔离不同渠道实例和会话。

### Agent 执行循环

AgentLoop 是 VikingBot 的执行核心。每轮消息会经过：

1. 加载身份、工作区规则、Skill、会话历史和 OpenViking 上下文；
2. 调用配置的模型；
3. 如果模型返回工具调用，由 ToolRegistry 校验参数并执行；
4. 将工具结果加入上下文，再次调用模型；
5. 生成最终回复，保存 Session，并投递回原渠道。

模型 Provider 层统一处理文本、reasoning、流式增量、工具调用和 token usage。Bot 默认继承 OpenViking 根级 `vlm`，也可以通过 `bot.agents` 使用独立模型。

### 工具、Skill 与子 Agent

VikingBot 内置文件、Shell、Web、图片、定时任务和 OpenViking 工具，也可以连接外部 MCP Server。

| 能力 | 作用 |
|------|------|
| **Tool** | 执行文件读写、命令、搜索、消息发送等具体操作 |
| **Skill** | 向 Agent 提供完成一类任务的流程、约束和配套资源 |
| **MCP** | 将外部服务能力注册为普通 Agent 工具 |
| **Subagent** | 在后台执行可独立完成的复杂任务，并将结果返回主 Agent |

Skill 采用渐进式加载，只有需要时才读取完整指令。工具是否可见由运行模式、渠道配置、请求参数和沙箱共同决定。

### 沙箱与工作区

文件和 Shell 工具通过 SandboxManager 执行。工作区可以由所有会话共享，也可以按 Session 或 Channel 隔离。

VikingBot 支持 Direct、SRT、OpenSandbox 和 AIO Sandbox 等后端。`direct` 直接使用 Bot 进程权限，不是强隔离环境；面向不可信用户时，应选择隔离后端并配置文件和网络策略。

### 自动化与主动任务

VikingBot 提供两种主动执行机制：

- **Cron**：按一次性时间、固定间隔或 cron 表达式触发 Agent；
- **Heartbeat**：周期读取工作区中的 `HEARTBEAT.md`，检查持续性任务。

两者最终都调用同一个 AgentLoop，并可以把结果交付回原 Session 和渠道。

### Gateway 与服务化运行

`vikingbot gateway` 将以下能力组合为长期运行服务：

- 已配置的聊天 Channels；
- Bot HTTP API 和 SSE 流式事件；
- AgentLoop、Session、Cron 和 Heartbeat；
- OpenViking API 代理；
- 用户反馈、结果评估、日志和可选 Langfuse 观测。

配置 OpenViking upstream 后，Bot Chat 和 `/api/v1/*` 可以使用同一个 Gateway 地址，但 Gateway Token 与 OpenViking 用户身份仍是两个独立安全边界。

## OpenViking 如何增强 VikingBot

### Resource：任务知识

Resource 为 Agent 提供文档、代码、网页和其他外部知识。VikingBot 可以语义检索、按路径浏览、进行 grep/glob 搜索，并只读取当前任务真正需要的完整内容。

### Memory：用户与 Peer 上下文

VikingBot 根据当前可信 `actor_peer_id` 读取 Peer Profile，并按类型召回：

- `events`：历史事件和决策；
- `entities`：人、项目和组织等实体信息；
- `preferences`：用户偏好、习惯和约束。

这使不同用户共享同一个 Gateway 时，仍能使用各自隔离的上下文。

### Experience：可复用任务经验

Experience 保存 Agent 过去完成类似任务的方法。VikingBot 可以在任务开始、读取 Skill 后或执行写操作前召回相关经验，减少重复试错。

### Session：从对话到长期上下文

VikingBot 本地 Session 保存运行历史和渠道状态；OpenViking Session 负责消息归档、压缩摘要、记忆和经验提取。

```text
当前任务
  → 召回 Resource / Memory / Experience
  → Agent 使用 Skill 和工具执行
  → 保存本地 Session
  → 增量同步并提交 OpenViking Session
  → 提取新的 Memory 和 Experience
  → 后续任务再次召回
```

普通会话会按策略自动同步。只有用户明确要求长期记住某项信息时，Agent 才主动调用记忆提交工具。

## 三种运行入口

| 入口 | 适用场景 | OpenViking 连接 |
|------|----------|----------------|
| `openviking-server --with-bot` | 本地完整体验 | 使用当前启动的 OpenViking Server |
| `vikingbot chat` | 快速试用和 Agent 开发 | 可选；不可用时 standalone 运行 |
| `vikingbot gateway` | 长期服务、远程访问和聊天平台 | 可连接指定或同配置中的 Server，也可 standalone 运行 |

安装、配置和每种入口的启动步骤见 [VikingBot 安装与配置](../guides/17-vikingbot.md)。

## 身份与安全边界

VikingBot 的访问控制分为多层：

- Channel 使用 `allow_from` 等策略限制消息发送者；
- 非 localhost Gateway 必须配置 Gateway Token；
- OpenViking Server 验证 User/Admin API Key 或 trusted 身份；
- request-scoped OpenViking 连接只接受可信 Server 代理注入；
- Sandbox 控制文件、命令和网络访问边界。

Gateway Token 只保护 Gateway 入口，不能代替 OpenViking 用户身份。对于公网或多用户部署，不应使用 `direct` 后端处理不可信请求。

## 适用场景

- 带长期记忆的个人或团队助手；
- 接入企业聊天平台的知识与任务 Bot；
- 需要文件、Shell、Web、MCP 和 Skill 的通用 Agent；
- 通过统一 Gateway 暴露 Chat 与 OpenViking API；
- 需要记录反馈、结果和任务经验的持续学习型 Agent。

## 相关文档

- [VikingBot 安装与配置](../guides/17-vikingbot.md)
- [VikingBot 完整使用说明](https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md)
- [VikingBot 架构详解](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/01-architecture.md)
- [Agent 能力体系](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/02-agent-capabilities.md)
- [渠道、Gateway 与运行管理](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/03-channels-and-gateway.md)
- [VikingBot 与 OpenViking 集成](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/04-openviking-integration.md)
- [OpenViking 上下文类型](./02-context-types.md)
- [OpenViking 会话管理](./08-session.md)
