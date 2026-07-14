# Agent 能力体系

VikingBot 的 Agent 能力由上下文、Skill、工具、沙箱和自动化共同组成。上下文告诉模型“当前是谁、知道什么、应该怎么做”，工具和沙箱决定它“实际能做什么”。

## 上下文构建

ContextBuilder 按以下顺序组织模型输入：

```text
Bot 身份
  + 沙箱环境说明
  + 工作区启动文件
  + Always Skill 完整内容
  + 可用 Skill 摘要
  + OpenViking Profile、记忆和经验
  + 本地或压缩后的会话历史
  + 本轮文本与媒体
```

工作区启动文件提供稳定身份和运行规则。图片等媒体会转换为 Provider 支持的多模态内容块。

## Skill 与工具

| 概念 | 作用 | 形式 |
|------|------|------|
| **Skill** | 告诉 Agent 如何完成一类任务 | `SKILL.md` 指令和资源 |
| **Tool** | 让 Agent执行具体操作 | 注册给模型的 JSON Schema 函数 |

Skill 采用渐进式加载：Always Skill 每轮注入完整内容，其他 Skill 只注入名称、描述和路径，Agent 需要时再用 `read_file` 读取。SkillsLoader 会检查命令和环境变量等依赖，避免暴露尚不可用的能力。

Skill 可以编排多个工具，但不会自动获得额外权限。工具是否可见仍由运行模式、渠道设置、请求参数和沙箱决定。

## 默认工具

| 类别 | 工具 | 作用 |
|------|------|------|
| 文件 | `read_file`、`write_file`、`edit_file`、`list_dir` | 操作工作区文件 |
| 命令 | `exec` | 在沙箱后端执行 shell 命令 |
| 网络 | `web_search`、`web_fetch` | 搜索和读取网页 |
| OpenViking | `openviking_list/search/grep/glob/multi_read` | 浏览、检索和读取上下文 |
| OpenViking | `openviking_add_resource`、`openviking_memory_commit` | 添加资源和提交记忆 |
| 对外操作 | `message`、`generate_image` | 主动发送消息或生成图片 |
| 自动化 | `cron` | 管理定时 Agent 任务 |
| 并行任务 | `spawn` | 启动后台子 Agent |

ToolRegistry 负责注册、参数校验、执行和 Hook。ToolContext 为每次调用提供当前 SessionKey、发送者身份、渠道 metadata、沙箱和已认证的 OpenViking 连接。

OpenAPI 的 `disabled_tools` 可以按请求隐藏工具；渠道的 `ov_tools_enable=false` 会隐藏 OpenViking 工具并关闭自动记忆上下文；`readonly` 模式不注册资源写入工具。

## MCP 扩展

`bot.tools.mcp_servers` 可以连接外部 MCP Server，支持 `stdio`、`sse` 和 `streamableHttp`。远端工具会包装为普通 VikingBot Tool，并以 `mcp_<server>_<tool>` 名称注册。

每个 MCP Server 可以配置：

- 启动命令或远端 URL；
- 环境变量和请求头；
- `enabled_tools` 工具白名单；
- `tool_timeout` 单次调用超时。

MCP 参数 Schema 会先做兼容转换，再交给模型和 ToolRegistry。

## 子 Agent

主 Agent 使用 `spawn` 把独立任务交给 SubagentManager。子 Agent 共享模型和对应工作区，但使用受限工具集：

- 保留文件、命令和 Web 工具；
- 不提供 `message`，避免直接对外发送；
- 不提供 `spawn`，避免递归创建子 Agent；
- 不提供 Cron、图片生成和 OpenViking 工具。

子 Agent 完成后把结果通知主会话，由主 Agent 负责身份相关操作和最终交付。

## Workspace 与 Agent 定制

Workspace 同时承担两个职责：一是保存构成 Agent 系统提示的启动文件和 Skill，二是作为文件与命令工具的本地工作目录。它与通过 `openviking_*` 工具访问的 OpenViking Workspace 相互独立。

### 路径与隔离范围

Workspace 根目录是 `<storage.workspace>/bot/workspace`；未配置 `storage.workspace` 时，默认为 `~/.openviking/data/bot/workspace`。`vikingbot status` 会显示解析后的根目录。

ContextBuilder 实际读取按 `sandbox.mode` 选择的活动 Workspace：

| 模式 | 活动目录 |
|------|----------|
| `shared` | `<workspace>/shared` |
| `per-session` | `<workspace>/<session-key>` |
| `per-channel` | `<workspace>/<channel-key>` |

因此，默认 `shared` 模式下，应定制 `<workspace>/shared` 中的文件，而不是直接修改 Workspace 根目录。

### 启动文件

ContextBuilder 在每轮构建系统提示时，按 `AGENTS.md`、`SOUL.md`、`TOOLS.md`、`IDENTITY.md` 的顺序读取存在且非空的文件：

| 文件 | 适合定义的内容 |
|------|----------------|
| `AGENTS.md` | 全局工作方式、任务流程、输出约束和必须遵守的项目规则 |
| `SOUL.md` | 人格、价值观、语气、回答风格和默认行为偏好 |
| `TOOLS.md` | 工具选择原则、调用顺序、副作用确认和安全边界 |
| `IDENTITY.md` | Agent 名称、角色、职责范围和身份背景 |

这些文件补充 VikingBot 内置身份和运行环境提示，不会改变真实工具 Schema、Channel 鉴权或 Sandbox 权限。例如，在 `SOUL.md` 中要求“始终执行 Shell”并不能让不可见的 `exec` 工具出现，也不能绕过沙箱策略。

初始模板中还包含 `USER.md`，但当前 ContextBuilder 不会把它自动加入系统提示。长期用户资料应优先保存在 OpenViking Peer Profile 和 Memory 中；需要静态行为规则时，应写入 `AGENTS.md` 或 `SOUL.md`。

### Skill、Heartbeat 与本地记忆

- `skills/<name>/SKILL.md` 定义某类任务的流程。Workspace Skill 优先于同名内置 Skill，并采用摘要注入、按需读取全文的渐进加载方式。
- `HEARTBEAT.md` 不属于普通系统提示，只由 HeartbeatService 周期读取。
- `memory/MEMORY.md` 和 `memory/HISTORY.md` 是本地记忆文件；只有启用 `bot.use_local_memory` 时，旧会话整理结果才会写回本地文件。默认长期上下文由 OpenViking 管理。

### 初始化与生效时机

首次使用活动 Workspace 时，VikingBot 从安装包中的 `bot/workspace` 复制启动文件、内置 Skill 模板和辅助目录。模板初始化不会覆盖已有的启动文件定制。

应直接修改活动 Workspace。ContextBuilder 每轮重新读取启动文件，因此保存 `SOUL.md`、`AGENTS.md`、`TOOLS.md` 或 `IDENTITY.md` 后，通常下一轮对话即可生效，无需重启 Gateway。修改安装包或仓库中的 `bot/workspace` 只影响以后创建的新 Workspace。

启动文件等同于系统级提示的一部分，应限制写权限，并且不要存放 API Key、Token 或其他秘密。

## 沙箱与 Workspace 隔离

SandboxManager 根据 SessionKey 和 `sandbox.mode` 选择工作区：

| 模式 | 工作区粒度 |
|------|------------|
| `shared` | 所有会话共享 `workspace/shared` |
| `per-session` | 每个会话独立目录 |
| `per-channel` | 同一渠道实例共享目录 |

当前实现提供以下执行后端：

| 后端 | 特点 |
|------|------|
| `direct` | 直接在 Bot 宿主机执行，默认不是强隔离环境 |
| `srt` | 支持文件和网络允许/拒绝策略 |
| `opensandbox` | 通过 OpenSandbox Server 创建隔离环境 |
| `aiosandbox` | 通过 AIO Sandbox 服务执行命令和文件操作 |

Direct 模式的 `restrict_to_workspace=false` 时，文件和命令可能访问工作区外内容。面向不可信用户开放服务时，应选择隔离后端并显式设置网络与文件策略。

## 多模态

VikingBot 支持三类多模态能力：

- 渠道图片输入转换为模型视觉内容块；
- `generate_image` 使用 `agents.gen_image_model` 完成文生图或支持模型上的图生图；
- Telegram 音频可以通过 GroqTranscriptionProvider 转换为文本。

生成图片可以通过消息回调直接交付到原渠道。模型是否理解图片取决于所选 Provider 和模型能力。

## Cron 与 Heartbeat

两类主动执行能力最终都调用 AgentLoop：

| 能力 | 触发方式 | 适用场景 |
|------|----------|----------|
| Cron | `at`、`every` 或 cron 表达式 | 指定时间提醒、固定周期任务 |
| Heartbeat | 周期读取工作区 `HEARTBEAT.md` | 持续检查一组可能变化的事项 |

Cron 任务持久化在 `cron/jobs.json`，保存原 SessionKey 和渠道 metadata；`deliver=true` 时将执行结果发回原渠道。

Heartbeat 跳过空文件、明确禁用心跳的 Session 和长期不活跃 Session。Agent 无需处理任务时返回 `HEARTBEAT_OK`。

## Hook

HookManager 提供运行时扩展点。当前内置 Hook 主要用于：

- `message.compact`：增量同步并按阈值提交 OpenViking Session；
- `tool.post_call`：读取 Skill 后检索并追加相关 Experience。

自定义 Hook 可以通过 `bot.hooks` 配置加载。

## 实现位置

| 内容 | 路径 |
|------|------|
| Workspace 模板 | `bot/workspace/` |
| 上下文与 Skill | `vikingbot/agent/context.py`、`skills.py` |
| 工具系统 | `vikingbot/agent/tools/` |
| 子 Agent | `vikingbot/agent/subagent.py` |
| 沙箱 | `vikingbot/sandbox/` |
| 自动化 | `vikingbot/cron/`、`vikingbot/heartbeat/` |
| Hook | `vikingbot/hooks/` |

## 相关文档

- [VikingBot 架构](./01-architecture.md)
- [渠道、Gateway 与运行管理](./03-channels-and-gateway.md)
- [与 OpenViking 集成](./04-openviking-integration.md)
