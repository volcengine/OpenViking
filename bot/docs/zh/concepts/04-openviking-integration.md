# VikingBot 与 OpenViking 集成

OpenViking 是 VikingBot 的长期上下文层。VikingBot 自己负责实时对话、模型推理和工具执行；OpenViking 负责统一保存和检索 Resource、Memory、Skill，以及从会话中沉淀可跨任务复用的记忆与经验。

## 集成目标

```text
OpenViking → VikingBot
  Resource：为任务提供知识与文件上下文
  Memory：提供当前用户/Peer 的 Profile、偏好、实体和事件
  Experience：提供 Agent 过去完成类似任务的方法
  Session：提供压缩历史和会话归档

VikingBot → OpenViking
  添加 Resource
  记录会话消息和使用过的上下文
  提交 Session，触发摘要、记忆和经验提取
  显式提交用户要求长期记住的信息
```

两者共同形成“召回 → 执行 → 反馈 → 沉淀 → 再召回”的上下文闭环。

## 连接模式

VikingBot 从同一个 `ov.conf` 解析 OpenViking 连接，支持三种拓扑：

| 模式 | 配置来源 | 行为 |
|------|----------|------|
| **Inherited** | 继承根级 `server` | Bot 与当前 OpenViking Server 配套运行 |
| **Explicit** | `bot.ov_server.server_url` | Bot 连接另一个 OpenViking Server |
| **Standalone** | 没有可用 Server URL | 基础对话可运行，OpenViking 能力降级 |

`openviking-server --with-bot` 对应 **Inherited** 模式：Server 启动受管的 VikingBot Gateway，并把当前 Server 的连接信息传给 Bot。下面的配置示例同样属于 Inherited 模式，根级 `server` 定义当前 OpenViking Server，`bot.ov_server` 只提供 Bot 访问该 Server 的凭证，没有配置 `server_url`。如果要使用 **Explicit** 模式连接另一套 OpenViking Server，应在 `bot.ov_server` 中同时配置目标 URL 和对应凭证。

示例：

```json
{
  "server": {
    "auth_mode": "api_key",
    "host": "127.0.0.1",
    "port": 1933
  },
  "bot": {
    "ov_server": {
      "api_key": "<openviking-user-api-key>",
      "account_id": "default"
    }
  }
}
```

## 认证与身份模型

OpenViking 连接支持 User key 和 Root key：

| `api_key_type` | 典型场景 | 含义 |
|----------------|----------|------|
| `user` | `api_key` / `dev` auth mode | 以 OpenViking User 身份访问 |
| `root` | `trusted` auth mode | Gateway 使用 Root key，并转发可信身份头 |

没有显式配置 `api_key_type` 时，VikingBot 根据同一 `ov.conf` 中 OpenViking Server 的有效 auth mode 推导默认值。

在当前 User/Peer 模型中：

- Bot 的 API key 所属主体是 User；
- 当前消息发送者表示为该 User 下的 Peer；
- `actor_peer_id` 是当前发送者的可信 Peer 标识；
- Peer Profile 和长期记忆围绕 `actor_peer_id` 召回。

Gateway 请求中可能携带 request-scoped `openviking_connection`，其中包含 account、user、agent、actor peer、role 和 namespace policy。该字段只接受可信 Server 代理传入，不能由普通客户端请求体自证。

## 客户端选择

OpenViking 访问主要通过 `VikingClient` 完成：

```text
有 request-scoped openviking_connection
  → 为当前请求创建临时 VikingClient
  → 使用该请求已认证的身份
  → 调用完成后关闭

没有 request-scoped connection
  → 使用 bot.ov_server 全局配置
  → 按 workspace 和 event loop 复用客户端
```

请求级连接优先，避免多用户 Gateway 错用 Bot 的全局身份。全局客户端还会按 asyncio event loop 隔离，避免在训练或多线程运行中复用绑定到其他 loop 的连接对象。

## Workspace 映射

VikingBot 使用 SandboxManager 计算 workspace ID：

| Sandbox mode | OpenViking workspace ID |
|--------------|-------------------------|
| `shared` | `shared` |
| `per-session` | SessionKey 的安全名称 |
| `per-channel` | `type__channel_id` |

该 ID 用于区分 Bot 工作区相关的 OpenViking 客户端、Session 和经验上下文。身份隔离仍由 OpenViking account/user/agent/peer 规则负责，workspace ID 不能替代认证。

## 自动上下文召回

ContextBuilder 在处理每条用户消息、首次调用模型前构建 OpenViking 上下文。本轮后续工具迭代复用这份基础上下文，并可在写工具或 Skill Hook 触发时追加 Experience。

### Peer Profile

首先读取当前 `actor_peer_id` 的 Profile，并作为“当前发送者信息”注入系统提示。渠道配置的 `memory_peer` 或请求 metadata 可以增加需要召回的其他 Peer。

旧字段 `memory_user` 只保留 owner-user 查询兼容用途，新配置应使用 `memory_peer`。

### 用户与 Peer 记忆

默认按类型配额检索：

| 类型 | 默认条数 | 内容 |
|------|----------|------|
| `events` | 10 | 与当前任务相关的历史事件和决策 |
| `entities` | 10 | 人、项目、组织等实体信息 |
| `preferences` | 3 | 用户偏好和约束 |

Profile 使用独立读取路径，不占用搜索候选。`memory_recall_max_chars` 控制注入的总字符预算。结果会去重、排序，并按完整内容、摘要或 URI 逐级降级，避免因预算不足完全丢弃相关记忆。

### Experience

Experience 保存 Agent 过去完成任务时形成的可复用方法。VikingBot 支持两个召回时机：

1. 根据当前任务直接检索 Experience；
2. Agent 读取某个 Skill 后，`tool.post_call` Hook 使用 Skill 名称或描述检索相关 Experience，并追加到 Skill 内容。

`exp_recall_limit` 控制召回条数，`exp_recall_max_chars` 控制注入预算。`recall_exp_first_round_only=true` 时只在会话第一轮注入，适合一次性任务或评测，不适合长对话。

### 写操作前的经验提醒

`exp_write_tools` 指定哪些工具调用前需要补充检索经验，默认是 `write_file` 和 `edit_file`。AgentLoop 会基于最近几条用户消息检索 Experience，并在真正写入前把结果加入当前上下文。

该配置只控制 Bot 侧的召回时机；OpenViking 是否生成 Experience 由 Session 的 memory policy 决定。

## OpenViking 工具

当渠道启用 `ov_tools_enable` 时，Agent 可以使用：

| 工具 | 能力 |
|------|------|
| `openviking_list` | 浏览 Viking URI 目录 |
| `openviking_search` | 对资源、记忆和 Skill 做语义检索 |
| `openviking_grep` | 在 OpenViking 内容中做正则搜索 |
| `openviking_glob` | 按 URI 路径模式搜索 |
| `openviking_multi_read` | 并发读取多个 URI 的完整内容 |
| `openviking_add_resource` | 添加 URL、本地文件或代码资源 |
| `openviking_memory_commit` | 显式提交当前会话中的长期记忆 |

OpenViking 工具通过 ToolContext 获得当前 actor peer 和 request-scoped connection。检索默认覆盖当前身份允许访问的资源、Peer 记忆和 Skill 路径。

`openviking_add_resource` 是异步资源处理操作；`readonly` 模式不注册该工具。`openviking_memory_commit` 适用于用户明确要求“记住”某项信息的场景。

## 本地 Session 与 OpenViking Session

两类 Session 不应混淆：

| Session | 存储 | 职责 |
|---------|------|------|
| VikingBot Session | 本地 JSONL | 运行历史、渠道状态、工具事件、回复与反馈 |
| OpenViking Session | OpenViking Server | 消息归档、压缩摘要、记忆和经验提取 |

VikingBot Session metadata 记录 OpenViking 同步状态：

- OpenViking session ID；
- 最后同步的本地消息下标；
- 最后 commit 的消息下标；
- 当前 pending token 数；
- 最近同步状态和错误。

## 增量同步和自动提交

```text
读取本地 Session 中未同步的消息
  → append_messages 到 OpenViking Session
  → 更新 last_synced_local_index
  → 查询 pending_tokens
  → 达到 token/消息阈值或强制提交
  → commit_session
  → 更新 last_commit_local_index
```

`message.compact` Hook 执行上述同步。主要配置包括：

| 配置 | 作用 |
|------|------|
| `agents.commit_token_threshold` | pending token 达到该值后 commit |
| `agents.commit_keep_recent_turn_count` | commit 后最多保留的最近逻辑 Turn 数；默认 `3` |
| `agents.commit_retained_message_token_budget` | commit 后 retained messages 与 checkpoint 的 token 预算；默认 `6000` |
| `agents.commit_min_raw_tail_steps` | 最新 Turn 超出预算时，至少原样保留的末尾 assistant Step 数；默认 `1` |
| `agents.commit_keep_recent_count` | 已废弃的物理消息数配置，仅为兼容旧配置文件而保留 |
| `agents.memory_window` | 本地历史窗口，也可触发消息数阈值提交 |

一个逻辑 Turn 从真实 user query 开始，包含下一条真实 user query 之前的全部 assistant Step；每个 Step 会将 assistant 文本、工具调用和对应工具结果作为不可拆分的整体处理。系统先按 `commit_keep_recent_turn_count` 选择最近 Turn，再用 `commit_retained_message_token_budget` 约束 retained 内容。如果最新 Turn 本身超出预算，则保留 user query 和至少 `commit_min_raw_tail_steps` 个最新 Step，较早 Step 进入同一次归档生成的 checkpoint。

迁移旧配置时，`commit_keep_recent_count` 不会自动换算为 Turn 数，当前 VikingBot 的 Turn-aware commit 也不再读取它。该字段仍被配置模型接受，因此已有 `ov.conf` 不会因未知字段而加载失败。如果只保留旧字段，系统会使用三个新字段的默认值；需要保持自定义保留策略时，应显式配置新字段，例如：

```yaml
agents:
  commit_keep_recent_turn_count: 3
  commit_retained_message_token_budget: 6000
  commit_min_raw_tail_steps: 1
```

消息使用本地索引增量同步，避免每轮重复 append。同步失败会写入 metadata 并记录日志，但不会让可选记忆能力阻断基础对话。

## 压缩会话上下文

默认模型历史来自本地 Session 最近 `memory_window` 条消息。设置 `agents.session_context_enabled=true` 后，VikingBot 可以从 OpenViking Session 获取压缩后的历史，并使用 `session_context_token_budget` 控制预算。

在新一轮开始前，如果历史达到阈值，AgentLoop 会先同步和 commit OpenViking Session，再构建新的提示上下文，从而避免超长对话持续膨胀。

## 显式记忆提交

用户明确要求长期记住信息时，Agent 调用 `openviking_memory_commit`：

```text
当前 Bot Session 消息
  → 追加到 OpenViking Session
  → commit
  → 等待或查询后台任务
  → 返回新增/更新/删除的 Memory URI
```

在 `readonly` 模式或渠道关闭 OpenViking 工具时，不会执行主动记忆固化。

## 经验闭环

完整闭环如下：

```text
当前任务
  → 检索 Resource / Peer Memory / Experience
  → Agent 使用 Skill 和工具执行任务
  → 本地 Session 记录消息、工具和结果
  → 增量同步并 commit OpenViking Session
  → OpenViking 提取记忆和经验
  → 后续任务再次召回
```

资源提供外部知识，Peer Memory 提供“关于当前用户的信息”，Experience 提供“Agent 过去如何做成类似任务”。三类上下文职责不同，但通过 Viking URI 和 OpenViking 检索接口统一访问。

## Gateway 代理

配置 OpenViking Server 后，VikingBot Gateway 将 `/api/v1/{path}` 代理到 upstream。代理会：

1. 验证 Gateway token 或本地请求边界；
2. 调用 upstream `/health` 确认实际 auth mode；
3. 解析 User key 或 trusted identity；
4. 过滤 hop-by-hop headers；
5. 转发认证头并保持响应状态。

Bot Chat 与 OpenViking API 因而可以通过同一个 Gateway 地址访问，但身份仍由 OpenViking Server 最终验证。

## 降级与错误边界

| 情况 | 行为 |
|------|------|
| 未配置 OpenViking Server | Bot 基础聊天继续运行，OpenViking 召回和工具不可用或跳过 |
| 自动记忆召回失败 | 记录日志，继续模型调用 |
| Session 同步失败 | 记录同步错误，保留本地 Session |
| request-scoped 身份不可信 | Gateway 拒绝请求 |
| upstream auth mode 与配置不一致 | Gateway 拒绝代理或聊天请求 |
| `ov_tools_enable=false` | 不注入 OpenViking 记忆，也不暴露 OpenViking 工具 |

## 可选 FUSE 挂载

`openviking_mount` 还提供可选的 FUSE 挂载能力，可将 OpenViking 内容映射为本地目录，并按 Session 创建或回收挂载点。它不在默认 AgentLoop 主链路中；默认 Bot 通过 VikingClient 和 `openviking_*` 工具访问 OpenViking。

## 实现位置

| 内容 | 路径 |
|------|------|
| 连接配置与合并 | `vikingbot/config/loader.py`、`schema.py` |
| VikingClient 适配 | `vikingbot/openviking_mount/ov_server.py` |
| 自动召回 | `vikingbot/agent/memory.py`、`context.py` |
| OpenViking 工具 | `vikingbot/agent/tools/ov_file.py` |
| Session 同步状态 | `vikingbot/openviking_mount/session_state.py` |
| Compact 与 Experience Hook | `vikingbot/hooks/builtins/openviking_hooks.py` |
| Gateway 代理和身份解析 | `vikingbot/channels/openapi.py` |
| 可选挂载 | `vikingbot/openviking_mount/manager.py`、`session_integration.py` |

## 相关文档

- [VikingBot 架构](./01-architecture.md)
- [Agent 能力体系](./02-agent-capabilities.md)
- [渠道、Gateway 与运行管理](./03-channels-and-gateway.md)
- [OpenViking 架构](../../../../docs/zh/concepts/01-architecture.md)
- [OpenViking 上下文类型](../../../../docs/zh/concepts/02-context-types.md)
- [OpenViking 会话管理](../../../../docs/zh/concepts/08-session.md)
