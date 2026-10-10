<a id="agent-集成能力对照"></a>

# Agent 集成能力

本页对比各 OpenViking Agent 集成安装后的实际行为。依赖某个集成之前，可以先用本页回答三个问题：

- Agent 能自己调用哪些 OpenViking 工具？
- 哪些事情不需要 Agent 请求就会发生：每次提问前的召回、会话开场注入的上下文、对话捕获，以及把对话变成长期记忆的提交？
- Agent 退出、崩溃、压缩上下文或连不上服务端时，哪些内容会保留，哪些会丢失？

安装请从[选择 Agent 接入方式](./01-overview.md)开始。下文的对比基于[源码依据](#源码依据)中列出的实现；一个集成能用哪些 MCP 工具，由所连的服务端决定。

## 两类能力

**Agent 可调用的工具**由模型决定是否调用，例如 `search`、`read`、`remember`。模型不调用，就不会发生任何事。只接 MCP 时，只有这一类能力。

**自动生命周期行为**由宿主事件触发，不需要模型请求，包括：提问到达模型前的召回、会话开场注入的 profile 和 skill 上下文、把每轮对话捕获到 OpenViking 会话，以及提交。hook、插件和原生扩展提供这类能力。

长期记忆只在**提交**（commit）之后才会从会话中抽取。已捕获但未提交的消息留在服务端会话里，要等之后的某次提交才会变成记忆。[捕获与提交](#捕获与提交)说明每个集成何时提交，以及退出时可能留下什么。

<a id="导读"></a><a id="_1-能力总览"></a><a id="_1-2-自动-hook-面-通过-harness-自动实现"></a><a id="_1-3-形态分组"></a>

## 一览

### 可调用工具

“服务端 MCP 工具”指服务端 `tools/list` 返回的工具：当前服务端有 20 个，列在[服务端 MCP 工具](#服务端-mcp-工具)。

| 集成 | 接入方式 | Agent 可调用的工具 |
|---|---|---|
| [Claude Code](#claude-code) | 插件：hook + MCP 代理 | 服务端 MCP 工具 |
| [Codex、TraeCode CLI 2.0](#codex-与-traecode-cli-2-0) | Codex 插件：hook + MCP 代理 | 服务端 MCP 工具 |
| [Cursor](#cursor) | hook 与 MCP 配置，外加一条 rule | 服务端 MCP 工具 |
| [TRAE、TRAE CN](#trae-与-trae-cn) | hook 与 MCP 配置 | 服务端 MCP 工具 |
| [ZCode](#zcode) | hook 与 MCP 配置 | 服务端 MCP 工具 |
| [Kimi Code](#kimi-code) | Kimi Code 插件：hook + MCP 代理 | 服务端 MCP 工具 |
| [OpenCode](#opencode) | npm 插件，自动添加 MCP 条目 | 服务端 MCP 工具，名为 `openviking_<tool>` |
| [DSH](#dsh) | 同进程 Cordis 插件 + MCP 代理 | 服务端 MCP 工具，名为 `mcp__openviking__<tool>` |
| [pi](#pi) | 原生扩展，内置 MCP 客户端 | 服务端 MCP 工具；MCP 握手成功后注册为 `openviking_<tool>` |
| [OpenClaw](#openclaw) | context-engine 插件 | 15 个原生工具，默认启用 14 个 |
| [Hermes](#hermes) | Hermes memory provider | 6 个原生 `viking_*` 工具 |
| [MCP 客户端、Agent Plugins](#其他集成) | 仅 MCP | 服务端 MCP 工具 |
| [ov CLI](#ov-cli) | 命令行客户端 | 每个操作都是显式命令，不经过 Agent |

### 自动行为

| 集成 | 每次提问前召回 | 会话开场上下文 | 会话中的提交 | 正常退出时提交 | 宿主压缩 |
|---|---|---|---|---|---|
| Claude Code | 是，带会话 | profile、记忆索引、skill | 待提交 token 达到 20,000 | 是 | 先提交，再由宿主摘要 |
| Codex | 是，带会话 | profile、记忆索引、skill | 待提交 token 达到 20,000 | Codex 0.145+；否则下次启动时 | 先提交，再由宿主摘要 |
| TraeCode CLI 2.0 | 是，带会话 | profile、记忆索引、skill | 待提交 token 达到 20,000 | 仅当版本带 `SessionEnd`；否则下次启动时 | 同 Codex |
| Cursor | 是，带会话 | profile、记忆索引、skill | 每捕获 8 条消息 | 否 | 先提交，再由宿主摘要 |
| TRAE、TRAE CN | 是，带会话 | profile、记忆索引、skill | 每轮 | 没有退出事件；每轮已提交 | 没有压缩前事件 |
| ZCode | 是，带会话 | profile、记忆索引、skill | 每轮 | 没有退出事件；每轮已提交 | 没有压缩前事件 |
| Kimi Code | 是，带会话 | profile、记忆索引、skill，在首次提问时 | 每捕获 8 条消息 | 仅当 `SessionEnd` 捕获到新消息 | 提交新捕获的消息 |
| OpenCode | 是，带会话 | profile、记忆索引、skill、已索引的仓库 | 空闲时，待提交 token 达到 20,000 | 是，在宿主的清理时限内 | 在压缩前后提交 |
| DSH | 是，带会话 | profile、记忆索引、skill，每个会话一次 | 待提交 token 达到 20,000 | 是，预算 3 秒 | 未观察到 |
| pi | 是，带会话 | profile、记忆索引、skill，每轮 | takeover 开：约 30,000 token；关：20,000 | takeover 开：否；关：是 | takeover 替换 pi 的摘要 |
| OpenClaw | 是，带会话 | profile，每轮 | token 预算的一半（默认 64,000） | 否 | 插件接管压缩 |
| Hermes（内置） | 是，带会话，有回退路径 | profile 与记忆清单 | 否；只在会话边界 | 是，前提是待上传内容在 10 秒内完成 | fork 型压缩时提交 |
| ov CLI | 否 | 否 | 只有 `ov session commit` | 不适用 | 不适用 |

标为 **Hermes（内置）** 的行描述旧版 Hermes 中固定提交的内置 provider。
使用目录插件的版本会加载外部插件，其不同的生命周期行为见 [Hermes](#hermes)。

表格说明：

- **带会话的召回**会发送会话 ID，服务端借助对话内容理解查询。共享插件的 context 模式还会扩写查询，并记录最近注入过的记忆，避免重复注入。Hermes 用 list 模式，没有注入台账。OpenClaw 用 context search 并发送会话 ID，但关闭去重，因为它注入的上下文每轮重建，不写入历史。请求路径和回退见[召回请求如何到达服务端](#召回请求如何到达服务端)。
- **待提交 token** 是服务端统计的、已捕获但未提交的消息量。多数阈值是客户端设置，见[各集成何时提交](#各集成何时提交)。
- **正常退出时提交**指集成会发出提交请求。网络错误、宿主的退出时限或进程被终止仍可能打断它。Ctrl+C、信号和崩溃见[退出时会发生什么](#退出时会发生什么)。

<a id="_1-1-主动工具面-agentic-调用能力"></a><a id="_2-公共能力核"></a>

## Agent 可调用的工具

<a id="_2-1-服务端-mcp-工具面"></a>

### 服务端 MCP 工具

所有基于 MCP 的集成都暴露同一组由服务端定义的工具，自己不维护工具目录。本地代理（pi 则是内置 MCP 客户端）读取 `~/.openviking/ovcli.conf`，连接服务端的 `/mcp`，原样转发 `tools/list` 的结果。服务端升级可以直接增加工具，不需要插件发版；客户端重新连接后即可看到。各工具的参数见 [MCP 工具与协议](../guides/06-mcp-integration.md#可用的-mcp-工具)。

| 分组 | 工具 |
|---|---|
| 检索与浏览 | `find`、`search`、`read`、`list`、`tree`、`grep`、`glob` |
| 写入 | `remember`、`write`、`edit`、`add_resource`、`add_skill` |
| 删除 | `forget` |
| 监听资源 | `list_watches`、`cancel_watch`（商业版暂不支持） |
| 账户与权限 | `list_users`、`list_groups`、`get_acl`、`set_acl` |
| 状态 | `health` |

使用前需要了解的行为：

- **`remember`** 会创建一个名为 `mcp-store-<id>` 的一次性会话，写入消息后立即提交。它是唯一会提交的 MCP 工具。提交被接受后它立刻返回，结果里带后台记忆提取任务的 `task_id`；提取过程再决定新建或更新哪些记忆。没有任何 MCP 工具会提交 Agent 当前的对话，那是自动 hook 的工作。
- **`find` 与 `search`**：`find` 是不带会话上下文的快速检索。`search` 可以传 `session_id`，并做意图分析（`retrieval.enable_intent`，默认开启）。`find` 传 `context_type="skill"` 时，每个 skill 包只返回一条命中，指向它的 `SKILL.md`，范围包括用户自己的和账户共享的 skill。
- **`write` 与 `edit`** 只能写 `viking://resources`、`viking://user` 和 `viking://agent`。新文件的扩展名必须是 `.md`、`.txt`、`.json`、`.yaml`、`.yml`、`.toml`、`.py`、`.js` 或 `.ts`。用户的 `skills/`、`peers/`、`privacy/` 和 `sessions/` 目录只读。写入 `viking://agent/skills` 不会被拒绝，但会跳过 skill 安装流程，skill 请用 `add_skill`。
- **`add_resource` 传本地路径**时返回一个签名上传 URL（默认 600 秒有效）。模型需要把文件上传到这个 URL，例如用一条 shell 命令；上传后自动开始入库。远程 URL 会直接入库。
- **`forget`** 删除传入的任意 URI，默认不递归。它不检查 URI 是记忆、资源还是 skill，见[写入与删除限制](#写入与删除限制)。
- **Schema** 在服务端启动时被展平，只接受 OpenAPI 3.0 子集的客户端（如 Gemini）也能加载。校验仍按原始签名进行，例如 `read` 也接受单个字符串。所有客户端拿到的 schema 相同。
- **身份**的解析方式与 REST 请求相同，见[认证](../guides/04-authentication.md)。

各宿主的差异：

- **OpenCode** 给工具加 `openviking_` 前缀；**DSH** 把工具命名为 `mcp__openviking__<tool>`。
- **DSH** 每个 DSH profile 只运行一个 MCP 代理，所以工具调用带的是进程级 actor peer，`remember` 也不绑定 DSH 会话。召回、捕获和提交仍按会话解析 peer。
- **pi** 只在以下条件都满足时注册工具：会话未被 bypass、健康检查通过、OpenViking 会话存在、`/mcp` 握手返回了工具列表。握手失败时，本次会话没有 OpenViking 工具，但召回、同步和 takeover 照常进行，后续轮次会重试握手。`mcpEnabled: false` 会关闭这条桥接。root API key 访问 `/mcp` 会得到 403，所以凭据链最终落到 `ov.conf` 的 `server.root_api_key` 时，会话里没有工具。
- **Codex** 只把 `.mcp.json` 的 `env_vars` 中列出的环境变量传给 MCP 服务。

### OpenClaw 工具

OpenClaw 注册自己的工具，不转发 MCP 工具。

| 分组 | 工具 | 说明 |
|---|---|---|
| 记忆 | `memory_recall`、`memory_store`、`memory_forget` | `memory_recall` 检索记忆；`memory_store` 通过临时会话提交 |
| 资源与 skill | `ov_search`、`ov_read`、`ov_multi_read`、`ov_list`、`add_skill`、`add_resource` | `ov_search` 默认检索资源和用户的 skill。`add_resource` 默认关闭，还需要开启 `enableAddResourceTool` |
| 会话归档 | `ov_archive_search`、`ov_archive_expand` | 检索并展开已归档的历史 |
| 大块工具输出 | `openviking_tool_result_read`、`openviking_tool_result_search`、`openviking_tool_result_list` | 读取服务端单独存放的工具输出 |
| 诊断 | `ov_recall_trace` | 查看自动召回是如何构建的 |

### Hermes 工具

Hermes 有 6 个工具：`viking_search`、`viking_read`、`viking_browse`、`viking_remember`、`viking_forget` 和 `viking_add_resource`，没有 skill 工具。`viking_remember` 通过独立会话提交一条事实，在抽取完成前就返回；抽取可能新增、合并或跳过这条事实。内置 provider 与外部插件的区别见 [Hermes](#hermes)。

<a id="_3-5-写入与删除的类型边界"></a><a id="_3-5-1-写入边界"></a><a id="_3-5-2-删除边界"></a>

### 写入与删除限制

无论删除来自哪个工具或命令，服务端都做同样的检查：

- 调用方需要对该 URI 有 manage 权限；正在被删除的用户会被拒绝。
- `viking://`、`viking://user` 和 `viking://agent` 不能删除。删除 `viking://resources` 或整个用户根（如 `viking://user/<id>`）需要 ROOT 角色。非 root 调用方不能写 `viking://temp` 根。
- 调用方的 actor-peer 视图中不可见的 URI 会被拒绝。

这些检查保护的是命名空间，不区分内容类型。按类型的限制来自客户端：

| 入口 | 能删除 | 客户端限制 |
|---|---|---|
| MCP `forget`（所有 MCP 集成，含 DSH 与 pi） | 传入的任意 URI | 除非 `recursive=true`，否则不递归；不检查类型或分数 |
| `ov rm` | 任意 URI | 不确认；`-r` 递归删除 |
| `ov tui` 的 `d` 键 | 根目录和 scope 目录以外的任意 URI | 需要确认 |
| OpenClaw `memory_forget` | 只能删记忆文件 | URI 必须匹配用户、peer 或 agent 的记忆路径。按搜索删除时，只有唯一候选且分数不低于 0.85 才执行，否则列出候选。始终不递归 |
| Hermes `viking_forget` | 一个用户记忆 `.md` 文件 | 拒绝目录、非 `.md` 文件、自动生成的 `.abstract.md` 和 `.overview.md`，以及带 query 或 fragment 的 URI |
| LangChain `viking_forget` | 任意 URI | 只在 `profile="admin"` 或 `allow_forget=True` 时提供；`recursive` 由模型决定 |
| Open WebUI | 无 | 没有删除工具 |

skill 走单独的路径。创建、安装或替换 skill 用 MCP `add_skill` 工具、OpenClaw 的 `add_skill`、`ov add-skill` 或 `POST /api/v1/skills`；`add_resource` 拒绝 skill URI。删除 skill 用 `ov skills remove` 或 `DELETE /api/v1/skills/{name}`。MCP `forget` 和 `ov rm` 也能删掉 skill 目录，但会留下该 skill 的 privacy 配置。见[技能](../api/04-skills.md)。

<a id="_3-维度详解"></a><a id="_3-2-自动召回与注入"></a>

## 自动召回与会话开场上下文

<a id="_3-2-1-机制底座-一条共享管线-两条服务端路径"></a>

### 召回请求如何到达服务端

除 OpenClaw 和 Hermes 外，所有集成都使用共享插件代码，最多依次尝试三种请求：

1. **context 检索**。`POST /api/v1/search/search`，带 `mode: "context"`、`purpose: "coding"` 和会话 ID。预算、配额、查询扩写和摘要选项只在你配置后才发送；否则使用服务端默认值，包括 1,600 token 的注入预算（`max_tokens`）。
2. **旧版 recall**。服务端版本较旧、拒绝 context 模式时，插件在 `~/.openviking/state/context-face.json` 中记录这一结果，有效 6 小时，并改调 `/api/v1/search/recall`。这个文件由本机所有集成共享，一个集成的判断对所有集成生效。
3. **普通 find**。最后一步，插件对 `viking://~/memories` 和 `viking://~/skills` 执行 `find`，在本地排序，并按自己的 token 预算装填。`recallTokenBudget`、`recallMaxContentChars` 和 `recallPreferAbstract` 只在这一步生效。

共享插件的召回不包含资源；资源文档由模型自己调用 `search` 检索。Hermes 开启资源召回选项后可以包含资源。

服务端对会话 ID 有两种处理方式：

- **context 模式**（context 检索和旧版 recall）根据对话扩写查询，并为每个会话维护一份已注入记忆的台账。扩写需要同时满足：`retrieval.enable_intent` 开启（默认开启）、会话中已有消息、存在归档概览或当前消息。原始查询始终排第一，后面最多追加三条规划出的查询。
- **list 模式**（不传 `mode` 时的默认值）由意图分析替换查询，原始查询不一定保留，也不使用台账。Codex 的第二级回退、Hermes 的 `viking_search(mode="deep")` 以及 Hermes 召回的首选路径都走这里。它们会发送会话 ID，但得不到扩写和去重。

跨轮去重（`dedup_turns`）：

- 服务端在 context 模式下的默认值是 **0**，即关闭。共享插件发送 5（`recallDedupTurns`）。直接调用 API 时必须自己发送 `dedup_turns`，只带会话 ID 不会开启去重。
- 窗口按消息条数计算，不按对话轮。同时捕获用户和助手消息时，5 大约覆盖两轮。
- 关闭自动捕获但开启召回时，消息数不会增长，已注入过的记忆会在整个会话里一直被抑制。设置 `OPENVIKING_RECALL_DEDUP_TURNS=0` 可以关闭去重。

<a id="_3-2-2-判定矩阵"></a>

### 各集成的召回方式

下表是默认路径。开启[召回摘要](#召回摘要)后，请求会有变化。

| 集成 | 触发时机 | 查询内容 | 上下文放在哪里 |
|---|---|---|---|
| Claude Code | 每次 `UserPromptSubmit` | 去掉首尾空白的提问 | `additionalContext` 中的 `<openviking-context>` |
| Codex、TraeCode CLI 2.0 | 每次 `UserPromptSubmit`；整个 hook 有 120 秒截止时间 | 提问 | `<openviking-context source="auto-recall">` |
| Cursor | `beforeSubmitPrompt` | 提问；500 ms 内的重复事件复用上次结果 | `additional_context` |
| TRAE、TRAE CN | `UserPromptSubmit` | 去掉之前注入块的提问 | `additionalContext` |
| ZCode | `UserPromptSubmit` | 去掉三类注入块（含 `<system-reminder>`）的提问 | `additionalContext`，严格 JSON |
| Kimi Code | `UserPromptSubmit` | 提问 | 纯文本上下文，不是 JSON |
| OpenCode | v1：每次 `chat.message`；v2：每次提问 | 消息的文本部分 | v1 在消息前插入一个合成 part。v2 把结果存进消息 metadata，每个模型 step 在该消息前注入，不发新请求 |
| DSH | `agent/pre-step` | 本批认领的所有消息，去掉自身注入的内容 | 追加为一条用户消息 |
| pi | 在 `before_agent_start` 排队，在 `context` 事件中执行 | 提问 | 前置到最后一条真实用户消息，本轮提问拿到本轮的记忆 |
| OpenClaw | 上下文组装时 | 传入的提问，清洗后截到 4,000 字符 | `<relevant-memories>`，放在 system prompt 中，每轮重建。宿主不传提问时前置到最后一条用户消息 |
| Hermes | 每次模型调用前 | 5 个字符及以上的用户输入，去掉 skill 脚手架 | `<memory-context>`，只追加到本次请求中的当前用户消息，不写入存储 |

<a id="_3-2-3-profile-开场注入"></a>

### 会话开场上下文

使用共享插件代码的集成会在会话开始时注入一个 `<openviking-context>` 块，包含用户 profile（`viking://user/<space>/memories/profile.md`）、`preferences/` 和 `entities/` 记忆的索引，以及 `<available-skills>` skill 清单：

```text
<openviking-context source="startup">
<user-profile uri="viking://user/default/memories/profile.md">...</user-profile>
<available-memories>...</available-memories>
<available-skills>
  OpenViking skills (stored in OpenViking, not local files). Before following one, read <dir>/<name>/SKILL.md with the OpenViking read tool.
  viking://user/default/skills/
    - pr-review — Review a pull request against the team checklist.
  viking://agent/skills/
    - deploy-runbook — Shared deployment runbook for the payments service.
</available-skills>
</openviking-context>
```

各集成的注入时机：

| 集成 | 时机 |
|---|---|
| Claude Code | `SessionStart`，所有 source |
| Codex | `SessionStart` 的 startup、clear 和 resume |
| Cursor、TRAE、TRAE CN、ZCode | `SessionStart` |
| Kimi Code | 首次提问时；失败后在后续提问中重试，直到成功 |
| OpenCode | 每个会话的第一条消息；失败后不重试，子代理会话跳过。已索引仓库的列表同时进入 system prompt |
| DSH | 每个会话一次；压缩后不再发送 |
| pi | 放在 system prompt 中，每轮重建 |
| OpenClaw | 放在 system prompt 中，每轮重建：`<user-profile>` 包含用户的 profile，设置了 `peer_role` 时还包含当前 actor peer 的 profile。不注入记忆索引和 skill 清单 |
| Hermes | 用自己的读取逻辑加载 profile 及 preferences、entities 清单，默认预算 6,000 token |

预算与限制：

- `profileTokenBudget`（默认 10,000 token）覆盖 profile 和记忆索引，profile 占一半。profile 超长时保留前 8 行和结尾；清单超长时以 `... +N more` 结尾。token 估算中，CJK 字符按 1.5 token 计，其他文本按每 4 个字符 1 token 计。
- `skillCatalogTokenBudget`（默认 1,200 token，`OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`）是 skill 清单的独立预算。`skillCatalog`（`OPENVIKING_SKILL_CATALOG`）或把预算设为 0 都会关闭清单。
- 清单来自一次 `GET /api/v1/skills` 请求。用户自己的 skill 排在前面，与用户 skill 同名的账户 skill 不再列出。每条描述截到约 40 token。描述放不下时只列名称；名称也放不下时以 `... +N more` 结尾，或缩成一行数量。服务端没有该接口时不注入清单。
- `sessionStartMaxBytes` 按 UTF-8 字节限制整个块：Claude Code 和 Codex 为 9,500，因为这两个宿主会把超过约 10,000 字符的 hook 输出存成文件，只显示预览；ZCode 为 20,000，因为它会丢弃超过 32 KB 的输出；其他集成不设上限。超出上限时，先去掉记忆索引，再去掉 skill 清单。

恢复会话时，部分集成还会注入上一次归档的摘要，预算 32,000 token：Claude Code 在 resume 和 compact 时，Codex 在本地会话已清空后的 resume 时，OpenCode 在会话开始时，pi 在 takeover 关闭时。Claude Code 和 Codex 遇到与本会话已注入内容相同的 profile 块时会跳过。

<a id="_3-2-4-超时与预算链"></a>

### 超时与预算

服务端的查询扩写在 5 秒后停止（`retrieval.recall_intent_timeout_s`），摘要改写在 30 秒后停止（`retrieval.recall_rewrite_timeout_s`）。共享插件在开启扩写时把 context 请求超时提高到至少 15 秒，请求摘要时至少 45 秒，让客户端能等完服务端的每个阶段。宿主的截止时间仍可能先结束 hook。修改这些值见[调整召回延迟](./19-recall-tuning.md#请求超时)。

| 集成 | 召回超时 |
|---|---|
| Claude Code | 15 秒，hook 上限 60 秒 |
| Codex | 整个 hook 120 秒截止，包括最长 110 秒的本地压缩器 |
| Cursor、TRAE、TRAE CN、ZCode | 15 秒，宿主上限 20 秒 |
| OpenCode | 30 秒 |
| DSH | 10 秒，开启查询扩写时至少 15 秒。召回会阻塞 pre-step |
| pi | 15 秒 |
| OpenClaw | context search 默认 15 秒（`autoRecallTimeoutMs`），之前有一次 500 ms 健康检查 |
| Hermes | 总计 4 秒，单请求 3 秒；可配置 |

OpenClaw 和 Hermes 把注入的召回内容限制在 4,000 字符，放不下的条目整条跳过，不截断。

<a id="_3-2-5-召回再摘要"></a>

### 召回摘要

召回摘要让模型先把召回结果改写成一份带引用的简短要点，再交给 Agent。

**服务端**。context 检索接受 `rewrite`（`false`、`true` 或 `"auto"`，默认 `false`）和 `rewrite_max_bullets`（默认 6，范围 1–20）。服务端使用 `query_planner` 模型；`rewrite=true` 且未配置 `query_planner` 时回退到主 `vlm`，`"auto"` 只在配置了 `query_planner` 时执行。摘要以 `OpenViking memory digest:` 开头，每个要点一行 `- `。每个要点最多 500 字符，必须引用结果中的一个 `viking://` URI；没有有效引用的要点会被丢弃。模型判断没有相关内容时，服务端返回 `stats.rewrite="no_relevant"` 和空块，这一轮不记入去重台账。超过 30 秒时，服务端返回未改写的块。

**插件**。`OPENVIKING_RECALL_COMPRESS` 或共享配置中的 `recallCompress` 选择模式，`recallRewrite` 仍可作为别名使用。

| 模式 | 行为 |
| --- | --- |
| `off` | 不生成摘要 |
| `server` | 发送 `rewrite: true`，优先使用服务端摘要 |
| `client` | 使用本地压缩器；仅 Claude Code 和 Codex |
| `auto` | Claude Code 和 Codex 有本地压缩器时使用它，否则发送 `rewrite: "auto"`。其他集成发送 `rewrite: "auto"` |

Claude Code 和 Codex 默认 `auto`，其他集成默认 `off`。支持服务端摘要的有 Claude Code、Codex、OpenCode、DSH、pi、Cursor、TRAE、TRAE CN、ZCode、OpenClaw 和 Hermes 外部插件，且需要服务端支持 context-search rewrite。Hermes 内置 provider 不支持召回摘要。旧值 `1` 和 `0` 分别等同于 `auto` 和 `off`。`no_relevant` 结果会取消本轮注入，插件不能回退到原始块。

本地压缩器：

- **Claude Code** 用 `claude --version` 检测 `claude`（结果缓存 7 天），执行 `claude -p --model sonnet --effort low --strict-mcp-config`。子进程超时默认 110 秒（`recallCompressTimeoutMs`），但 60 秒的提问 hook 可能更早结束它。少于 1,500 字符的输入不压缩。引用的 URI 按编辑距离匹配回真实的结果 URI，无法匹配的要点会被丢弃。失败时注入本地格式化的结果，或注入渲染好的块。
- **Codex** 从 `~/.codex/models_cache.json` 选择模型（优先 `gpt-5.3-codex-spark`，其次低 effort 的 `gpt-5.6-luna`；缓存 7 天），在只读的临时沙箱中执行 `codex exec`，默认超时 110 秒。运行失败一次后，本地压缩停用到下一次 `SessionStart`。输出上限 4,000 字符；失败时的回退方式与 Claude Code 相同。

这些设置只作用于自动召回。模型显式调用 MCP `search` 时，使用它自己传入的参数。旧服务端的回退和宿主时间预算见[共享插件说明](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression)和[调整召回延迟](./19-recall-tuning.md)。

<a id="_3-2-6-注入回流防护"></a>

### 避免注入内容被重复捕获

注入的上下文用固定标签包裹，例如 `<openviking-context>`。捕获时先去掉这些块再发送，召回的记忆就不会被当成新对话再次存储。共享捕获代码还会去掉摘要块、元数据围栏和时间戳前缀。TRAE 和 ZCode 使用各自的清理函数，ZCode 的清理函数会去掉三类注入块。OpenClaw 在捕获一轮对话和构造下一次召回查询时都会去掉 `<relevant-memories>`。Hermes 从捕获批次中丢弃三个只读工具的调用和结果，保留写入类工具的调用。

<a id="_3-3-会话与-commit-生命周期"></a>

## 捕获与提交

<a id="_2-3-服务端会话与-commit-语义"></a><a id="_3-3-1-机制底座"></a>

### 服务端会话与提交机制

- **会话隐式创建**。服务端收到某个会话的第一条消息时创建该会话；带该会话 ID 的第一次 context 模式召回也会创建。DSH 是唯一显式创建会话的集成。
- **提交分两个阶段**。`POST /api/v1/sessions/{id}/commit` 在第一阶段归档消息后返回。响应中带有第二阶段（记忆抽取）的 `task_id`，抽取在后台运行。提交请求成功不代表抽取已经完成。
- **`keep_recent_count`** 决定提交后会话中保留多少条最近的消息。服务端默认 0，即全部归档。Claude Code、Codex、OpenCode、DSH、Cursor、TRAE、TRAE CN、ZCode 和 Hermes 发送 0；pi 在 takeover 模式下发送最近 3 个用户轮对应的确切消息数，其他情况发送 0；OpenClaw 在阈值提交时发送 10，在 reset、`memory_store` 和压缩时发送 0。
- **服务端自动提交默认关闭**。`memory.session_auto_commit.enabled` 默认 `false`，关闭时空闲扫描器不会启动。新会话仍可以从 `server.user_config_defaults.auto_commit_policy` 获得策略，也可以通过 `POST /api/v1/sessions`、`PATCH /api/v1/sessions/{id}/config`、SDK，或 `ov session new --auto-commit-policy-json` 与 `ov session config set` 显式设置。策略的默认值是：待提交 token 150,000（严格大于）、100 条消息、86,400 秒空闲超时、`keep_recent_count` 0、无最小间隔。空闲超时还需要 `memory.session_auto_commit.enabled=true`。记忆插件不发送策略，所以没有上述设置时，只有客户端会提交。
- **批量写入**。共享插件每次 `POST /messages/batch` 最多发送 100 条消息，与服务端上限一致；批量接口返回 404 或 405 时改为逐条发送。
- **大块工具输出单独存放**。服务端把超过 20,000 字符的工具输出移到单独的记录中，留下 `tool_output_ref`。插件把自己的上限（`captureToolMaxChars`）提高到 1,000,000，只作为兜底。
- **部分写入在后台运行**。Claude Code、Codex 和 ZCode 默认把 `Stop` 的写入交给一个分离的 worker（`OPENVIKING_WRITE_PATH_ASYNC`），宿主不用等待。worker 启动后，关闭终端不影响它，但网络错误或进程被终止仍可能打断它。开启这项设置时，`Stop` 不再输出 `appended N turn(s)` 提示。

<a id="_3-3-2-常规-commit-触发条件"></a>

### 各集成何时提交

表中的阈值都在客户端判断；除非另有说明，读取的是服务端的待提交 token 数。

| 集成 | 会话中 | 显式操作或会话边界 | 压缩前后 |
|---|---|---|---|
| Claude Code | `Stop` 时待提交 token 达到 20,000 | `SessionEnd` 和 `SubagentStop` 总是提交；`SessionStart` 重放排队的写入 | `PreCompact` 总是同步提交 |
| Codex | `Stop` 时待提交 token 达到 20,000 | `SessionEnd`（Codex 0.145+）补齐漏掉的轮次，然后在分离的 worker 中提交。startup 或 clear 的 `SessionStart` 会提交已标记结束或空闲超过 30 分钟的会话 | `PreCompact` 补齐后提交全部内容 |
| TraeCode CLI 2.0 | 同 Codex | 同 Codex；没有 `SessionEnd` 时只有启动时的扫描 | 同 Codex |
| Cursor | 距上次提交捕获满 8 条消息时，在 `stop` 提交（`commitTurnThreshold`），本地计数 | `sessionEnd` 已注册，但实际不会运行 | `preCompact` 总是提交 |
| TRAE、TRAE CN | 每个捕获到内容的 `Stop` | 无 | 没有压缩前事件 |
| ZCode | 每个 `Stop`；漏掉的 `Stop` 对应的轮次，在下一个 `Stop` 从 rollout 文件补齐 | 无 | 没有压缩前事件 |
| Kimi Code | 距上次提交捕获满 8 条消息时，在 `Stop` 提交 | `SessionEnd` 和 `Interrupt` 捕获到新消息时提交 | `PreCompact` 捕获到新消息时提交 |
| OpenCode | v1 `session.idle`、v2 执行结束时：待提交 token 达到 20,000 | 删除会话、v1 `session.error`、v1 dispose 和 v2 cleanup 强制提交 | v1 在压缩前后各一次；v2 在压缩结束后一次 |
| DSH | `turn/end` 时待提交 token 达到 20,000 | Cordis teardown 提交每个会话 | 无 |
| pi，takeover 开（默认） | 本地估算达到 30,000 token，且用户轮多于 3 个 | `/viking commit` | `session_before_compact` |
| pi，takeover 关 | 每次同步后待提交 token 达到 20,000 | `session_shutdown` 和 `/viking commit` | `session_before_compact` |
| OpenClaw | 每轮结束后达到 `tokenBudget × commitTokenThresholdRatio`（默认 128,000 × 0.5） | `/new`、`/reset` 和 `memory_store` 提交并等待 | `compact()` 提交，并最多等待 5 分钟完成抽取 |
| Hermes（内置） | 无 | 会话结束、会话切换（`/new`、`/resume`、`/branch`、fork 型压缩）、gateway 缓存驱逐，以及退出处理器；`/undo` 和原地压缩不提交 | 只在 fork 型压缩时 |
| ov CLI | 无 | `ov session commit`；`ov add-memory` 会创建、写入并提交自己的会话 | 不适用 |

<a id="_3-3-3-关闭方式-×-harness-终局矩阵"></a>

### 退出时会发生什么

“提交”指退出路径会发出提交请求，“视情况”取决于最后一列的说明。已经写入的消息留在服务端会话中，由同一会话的下一次提交归档。本表假设服务端自动提交关闭。

| 集成 | 正常退出 | Ctrl+C | SIGTERM | 关闭终端或窗口 | `kill -9` 或崩溃 | 剩余内容如何回收 |
|---|---|---|---|---|---|---|
| Claude Code | 提交 | 提交 | 提交 | 提交 | 否 | `SessionEnd` 把提交交给自成进程组的分离 worker，SIGHUP 不会终止它。否则等下次阈值、`/compact` 或 `SessionEnd` |
| Codex | 提交 | 视情况 | 否 | 否 | 否 | 连按两次 Ctrl+C 属于正常退出，会触发 `SessionEnd`；只按一次不会。漏掉的内容在下一次 startup 或 clear 的 `SessionStart` 提交：结束标记还在就立即提交，否则等空闲 30 分钟后提交 |
| TraeCode CLI 2.0 | 否，除非版本带 `SessionEnd` | 否 | 否 | 否 | 否 | 下一次 `SessionStart` 的 30 分钟空闲扫描 |
| Cursor | 否 | 否 | 否 | 否 | 否 | 关闭或切换对话不触发任何事件。`sessionEnd` 只在关闭窗口时触发，而此时宿主已停止执行 hook 命令。不足 8 条消息阈值的部分要等同一会话的后续消息 |
| TRAE、TRAE CN | 否 | 否 | 否 | 否 | 否 | 每个 `Stop` 已经提交，最多丢失正在进行的那一轮 |
| ZCode | 否 | 视情况 | 否 | 否 | 否 | 按 Ctrl+C 时如果该轮的 `Stop` 已触发，分离的 worker 会写完。每个 `Stop` 都提交，漏掉的轮次在下一个 `Stop` 补齐 |
| Kimi Code | 视情况 | 视情况 | 未验证 | 未验证 | 否 | `SessionEnd` 和 `Interrupt` 只在捕获到新消息时提交；已被 `Stop` 捕获的尾部等待下一次提交 |
| OpenCode | 视情况 | 视情况 | 视情况 | 视情况 | 否 | v1 1.15.11+ 的 `dispose` 和 v2 cleanup 会提交所有会话，但宿主留给清理的时间有限，慢请求或会话较多时可能被截断。v2 cleanup 还会在空闲 60 分钟和插件热重载时运行 |
| DSH | 提交 | 提交 | 提交 | 否 | 否 | teardown 给每个会话一次 3 秒的提交，排在慢写入之后，整体处于 5 秒的进程宽限期内。第二次 Ctrl+C 会强制退出，跳过提交。Web 形态下关闭浏览器标签页不会触发 teardown |
| pi，takeover 开 | 否 | 否 | 否 | 否 | 否 | `session_shutdown` 保存 takeover 状态但不提交。等下次运行达到阈值，或执行 `/viking commit` |
| pi，takeover 关 | 提交 | 提交 | 提交 | 提交 | 否 | 失败的提交进入离线队列 |
| OpenClaw | 否 | 否 | 否 | 否 | 否 | `session_end` 处理器不提交。归档依赖 `/new`、`/reset` 和阈值 |
| Hermes（内置） | 视情况 | 视情况 | 视情况 | 视情况 | 否 | 退出处理器最多用 10 秒等待上传完成，然后提交；上传没有完成就跳过提交，不提交半个会话。SIGTERM 和 SIGHUP 默认有 1.5 秒宽限期，Hermes 的退出看门狗可能中断较慢的提交 |
| LangChain、Open WebUI、Agent Plugins、MCP 客户端 | 否 | 否 | 否 | 否 | 否 | 没有会话 hook。MCP 代理退出时发送的 `DELETE /mcp` 只关闭协议会话。LangChain 依赖调用方的 `close()` |

这张表的实际含义：

- **正常退出时会提交**：Claude Code、Codex 0.145+、OpenCode、DSH、takeover 关闭的 pi，以及 Hermes。其他集成依赖最后一列的回收方式。
- **`kill -9` 之后没有任何集成会提交**。已写入的消息保持未提交，直到该会话的下一次提交。带空闲超时的服务端自动提交策略是唯一的服务端兜底，而插件不会配置它。
- **TRAE、TRAE CN 和 ZCode** 的退出行为最简单，因为每轮都提交；代价是每个 `Stop` 都要做一次完整的归档和抽取。

<a id="_3-3-4-pending-queue-离线补偿对照"></a>

### 离线重试

| 集成 | 写入失败时 |
|---|---|
| Claude Code、Cursor、TRAE、TRAE CN、ZCode、Kimi Code、OpenCode、DSH、pi | 可重试的失败进入 `~/.openviking/pending` 下的磁盘队列，在会话开始时重放：每次最多 50 条，每条最多 3 次，保留 7 天。网络错误、408、429 和 5xx 可重试；其他 4xx（含 401 和 403）不入队。某条消息重放失败时停止，以保证顺序 |
| Codex、TraeCode CLI 2.0 | 新捕获的内容不入队。transcript 游标只越过服务端已接受的消息，下次捕获或启动扫描会重发剩余部分。`SessionStart` 仍会重放已排队的条目 |
| OpenClaw | 没有队列，失败的轮次不会重发 |
| Hermes（内置） | 上传在进程内线程中运行，不从磁盘重放。`$HERMES_HOME/openviking/pending_sessions/` 下的待提交标记让之后的启动能提交已退出进程留下的会话（仅 POSIX） |
| LangChain | 提交失败时在下一次 record 时重试，只保存在内存中。部分写入会抛出 `OpenVikingPartialWriteError`，附带计数，调用方可据此重试剩余消息 |
| 日志导入 | SQLite 游标库在发送前记录每批数据。崩溃后，下次运行根据服务端的消息数判断这批数据是否已送达 |

<a id="_3-3-5-subagent-会话对照"></a>

### 子代理

| 集成 | 子代理处理 |
|---|---|
| Claude Code | 每个子代理有自己的会话 `cc-<id>__subagent-<agent_id>`。`SubagentStop` 发送它的 transcript 并提交 |
| Codex、TraeCode CLI 2.0 | 子代理输出并入主会话 |
| OpenCode | 子代理使用 `oc-<parent>__subagent-<child>` 会话。会话开场上下文对它们跳过，召回不跳过 |
| DSH | 每个子代理是单独的 `dsh-<id>` 会话，与父会话没有关联，各自获得会话开场上下文 |
| Hermes | 委派任务以 `skip_memory=True` 运行，子代理没有 OpenViking 会话、召回或工具，输出也不会被捕获 |
| Cursor、TRAE、ZCode、Kimi Code、pi、OpenClaw | 没有特殊处理。有独立会话 ID 的子代理有自己的会话，否则消息并入主会话。OpenClaw 可以用 `bypassSessionPatterns` 排除会话 |
| 日志导入 | Claude Code 适配器跳过 sidechain 和 meta 记录，子代理对话不会导入 |

<a id="_3-4-压缩-compaction-接管"></a><a id="_3-4-1-判定矩阵"></a>

## 宿主压缩

宿主缩减上下文时，多数集成只确保被丢弃的消息已经提交，摘要由宿主生成。pi 和 OpenClaw 可以用 OpenViking 的归档替换宿主的摘要。

| 集成 | 方式 | 压缩前 | 压缩后 |
|---|---|---|---|
| Claude Code | 宿主摘要 | `PreCompact` 同步提交；这是唯一不在后台运行的写入，因为宿主紧接着就会重写 transcript | `source="compact"` 的 `SessionStart` 注入归档概览和最多 5 条摘要 |
| Codex、TraeCode CLI 2.0 | 宿主摘要 | `PreCompact` 补齐未捕获的轮次，提交全部内容，并开始一个新的 OpenViking 会话。补齐不完整时不提交，稍后重试 | resume 时注入归档摘要 |
| Cursor | 宿主摘要 | `preCompact` 提交 | 无 |
| TRAE、TRAE CN、ZCode | 宿主摘要 | 没有压缩前事件 | 无 |
| Kimi Code | 宿主摘要 | `PreCompact` 提交新捕获的消息 | 无 |
| OpenCode | 宿主摘要 | v1 刷新并提交；v2 捕获 transcript | v1 在 `session.compacted` 时再提交一次；v2 在 `session.compaction.ended` 后提交 |
| DSH | 未观察到 | 无。注入的上下文是一条用户消息，随宿主压缩一起缩减；profile 不再发送 | 无 |
| pi | takeover，默认开启 | 提交并等待归档概览 | 用概览替换 pi 的摘要；失败时 pi 照常压缩 |
| OpenClaw | 插件接管压缩 | `compact()` 提交并等待 | 下一次上下文组装从服务端重建历史 |
| Hermes（内置） | 宿主摘要 | fork 型压缩提交旧会话；原地压缩不做任何处理 | 无 |

<a id="_3-4-2-pi-takeover"></a>

### pi takeover

- **改变了什么**。pi 自己的历史不会被修改。扩展改写每次 `context` 事件中发送的消息：边界之前的内容合并成一条合成用户消息 `[OpenViking Session Context]`，内容是截到 3,000 token 的归档概览。它的时间戳取第一条保留消息之前的时刻，让发给 provider 的请求保持稳定，prompt 缓存继续生效。
- **何时运行**。由 token 压力触发，而不是 pi 的压缩事件：约 30,000 token，保留最近 3 个用户轮。提交时把这 3 轮对应的确切消息数作为 `keep_recent_count` 发送。
- **摘要来源**。该次提交产生的归档概览，每 2 秒轮询一次，最多 15 次。边界作为 `ov-takeover` 条目存在 pi 的 branch 中，重启后仍然有效。
- **何时回退**。指纹不匹配、历史短于边界或缺少概览时，pi 使用完整历史。如果概览一直为空，边界不移动，待提交计数归零，重新累积后再试。
- **pi 自己的压缩**。takeover 成功时，`session_before_compact` 把 OpenViking 摘要返回给 pi。没有 `firstKeptEntryId` 时，pi 执行默认压缩。

<a id="_3-4-3-openclaw-contextengine"></a>

### OpenClaw 上下文引擎

- OpenClaw 把插件注册为上下文引擎，并设置 `ownsCompaction: true`，宿主不再自己生成摘要。
- 上下文组装分两部分。`transformContext` 只添加召回，前面有五道 passthrough 检查。主组装调用 `getSessionContext(tokenBudget)`，用服务端的返回替换宿主的实时历史，按四档预算切分，前面有三道 passthrough 检查和一个消息清洗步骤。
- `compact()` 以 `keep_recent_count` 0 提交，每 500 ms 轮询一次，最多 5 分钟，然后把归档概览作为摘要返回。
- `ingest()` 和 `ingestBatch()` 有意不做任何事；对话在每轮结束后捕获。
- 存在归档时，system prompt 会加入一段简短指引，要求模型在回答“没有相关信息”之前先重读摘要，并用 `ov_archive_search` 至少尝试两组关键词。

<a id="_3-4-4-pi-与-openclaw-接管方式对照"></a>

### pi 与 OpenClaw 接管对比

| | pi takeover | OpenClaw 上下文引擎 |
|---|---|---|
| 宿主契约 | 在 `context` 事件中改写消息 | 上下文引擎，`ownsCompaction: true` |
| 历史来源 | pi 本地的 branch | 服务端的会话上下文 |
| 触发 | 客户端 token 阈值（30,000，保留最近 3 个用户轮） | 宿主每次调用 assemble 或 compact |
| 结果 | 一条合成用户消息，最多 3,000 token | 重建的消息列表，加上压缩摘要 |
| 失败时 | 完整历史 | 宿主的实时消息 |
| 召回 | 带会话 ID 的 context 检索 | 不带会话 ID 的 `/find`，没有扩写和去重 |

<a id="_3-6-降级与容错"></a><a id="_3-6-1-判定矩阵"></a>

## 服务端不可用时

召回出错时，宿主可以在没有注入记忆的情况下继续。但请求仍可能让提问一直等到请求结束或到达截止时间；出错后能继续，不代表不用等待。

| 集成 | 服务端不可达 | 缓存的失败状态 | 等待与错误传递 |
|---|---|---|---|
| Claude Code | 每个 hook 捕获错误并让宿主继续；会话开始时跳过队列重放 | context 检索标记 6 小时，本地 CLI 检测 7 天，健康状态 5 秒 | 召回等到请求结束或截止时间后再继续。URI guard 的拒绝是有意设计，与召回失败无关 |
| Codex、TraeCode CLI 2.0 | 每个 hook 捕获错误，不做任何处理 | context 检索标记；本地压缩器失败后停用到下次启动 | 召回等到请求结束或截止时间，然后提问继续 |
| Cursor、TRAE、TRAE CN、ZCode | 请求错误被吞掉，不注入任何内容。5 秒内拿不到锁的 hook 静默跳过 | 只有 context 检索标记，所以每轮都要等满 15 秒召回超时 | 每轮最多等到召回超时 |
| OpenCode | 召回、捕获和清理的错误被捕获并记录日志 | context 检索标记；健康检查不缓存 | 召回会等待网络请求；清理也可能等到截止时间 |
| DSH | 插件吞掉错误。会话初始化失败不缓存，所以每个 pre-step 会发两次 5 秒的健康检查 | context 检索标记；用户空间查询结果在进程生命周期内缓存 | pre-step 依次执行 profile 和召回，会话 flush 会阻塞 |
| pi | 启动时健康检查失败则不注册工具，之后的提问静默重试。只有 MCP 握手失败时，召回、同步和 takeover 不受影响；状态栏显示 `tools ✗`，`/viking` 打印错误 | context 检索标记。MCP 握手每轮重试一次，召回开始前可能用掉 5 秒的握手预算 | 召回等到请求结束或截止时间。不开 takeover 时 `session_shutdown` 最多等 30 秒；`turn_end` 遇到网络错误时每条消息等 10 秒 |
| OpenClaw | 500 ms 健康检查失败时跳过召回 | 无；每轮一次健康检查 | 只有 `memory_store` 的错误会返回给模型；`compact()` 最多可能等 5 分钟 |
| Hermes（内置） | 失败的连接在 30 秒内不重试，除非连接设置改变 | 记住失败的设置 | 召回在 4 秒总预算内等待，然后不带上下文继续 |
| ov CLI | 多数命令以状态码 1 退出；表格模式的 `ov status` 和 `ov health` 即使不健康也退出 0 | 无 | 不适用 |

<a id="_3-6-2-通用超时"></a>

### 共享超时与锁

共享插件的 HTTP 超时为 15 秒（最小 1 秒）。MCP 代理请求 15 秒超时，退出时发送的 `DELETE /mcp` 为 2 秒。跨进程锁等待 5 秒，60 秒后视为过期；正在重放的队列条目 10 分钟后被回收。MCP 代理不处理 SIGHUP，关闭终端时不会发送 `DELETE /mcp`；服务端以无状态方式运行 MCP，不会留下残留状态。

<a id="_3-1-接入形态、安装与配置体系"></a><a id="_3-1-1-判定矩阵"></a>

## 安装、会话与配置

从会话 ID 前缀可以看出服务端上的会话由哪个集成写入。

| 集成 | 安装方式 | 会话 ID | 配置来源 |
|---|---|---|---|
| Claude Code | 统一安装器（`--harness claude`）或插件市场 | `cc-<session id>`；子代理 `cc-<id>__subagent-<agent_id>` | 共享配置，`plugin.claude_code` |
| Codex | 统一安装器（`--harness codex`）或 `codex plugin marketplace add` | `cx-<id>`，由 Codex 会话推导 | 共享配置，`plugin.codex` |
| TraeCode CLI 2.0 | 统一安装器（`--harness trae-cli`），对 `traecli` 执行 Codex 的安装流程 | 同 Codex | 同 Codex |
| Cursor | 统一安装器；写入 `~/.cursor/hooks.json` 和 `mcp.json` | `cu-<conversation id>` | 共享配置，`plugin.cursor` |
| TRAE、TRAE CN | 统一安装器；写入 `~/.trae/` 或 `~/.trae-cn/` 下的 hook 与 MCP 文件 | `tr-` 或 `trcn-` | 共享配置，`plugin.trae` 或 `plugin.trae_cn` |
| ZCode | 统一安装器；合并进 `~/.zcode/cli/config.json` 并开启 hook | `zc-<id>` | 共享配置，`plugin.zcode` |
| Kimi Code | 统一安装器；Kimi Code 托管插件 | `kc-<id>` | 共享配置，`plugin.kimicode` |
| OpenCode | 统一安装器、npm（`@openviking/opencode-plugin`）或源码 | `oc-<id>`；子代理 `oc-<parent>__subagent-<child>` | 共享配置，`plugin.opencode` |
| DSH | 统一安装器或 `dsh plugin add @openviking/dsh-memory-plugin` | `dsh-<id>` | 共享配置，`plugin.dsh`，然后是 Cordis patch |
| pi | 统一安装器，装入 pi 的扩展目录 | `pi-<id>` | 共享配置，`plugin.pi` |
| OpenClaw | ClawHub、npm 安装器或离线包；之后运行 `openclaw openviking setup` | 会话 UUID，或会话 key 的 SHA-256 | `openclaw.json` 和少量环境变量 |
| Hermes（内置） | 随 Hermes 发布；运行 `hermes memory setup openviking` | Hermes 自己的会话 ID | `.env`、关联的 `ovcli.conf` 和 Hermes `config.yaml` |
| ov CLI | npm、`uv tool install`、cargo 或 GitHub Releases | 不管理 | `ovcli.conf` profile |

“共享配置”指[配置与工作区文件](#配置与工作区文件)中分层的插件配置。

<a id="_3-1-2-统一安装器"></a>

### 统一安装器

`examples/memory-plugin-shared/install.sh` 安装 Claude Code、Codex、TraeCode CLI 2.0、Cursor、TRAE、TRAE CN、ZCode、Kimi Code、OpenCode、pi 和 DSH。OpenClaw 和 Hermes 有各自的安装渠道。需要知道的几点：

- 不带 `--harness` 时显示多选菜单。各插件自带的 setup 脚本会替你传入 `--harness`。通过 `curl` 管道执行时，从 `/dev/tty` 读取输入。
- 从文档站下载；在仓库 checkout 中运行时，使用本地 checkout。
- hook 和 MCP 条目带有 `OPENVIKING_INTEGRATION_ID` 标记，重新运行只替换自己的条目，不动其他工具的条目。每个被修改的文件先备份为 `.bak`，再以 `0600` 权限原子替换。
- 凭据步骤为本地服务端、OpenViking Service 或自定义 URL 写入 `~/.openviking/ovcli.conf`。已有配置会先显示当前值（API key 打码），再让你选择保留或修改。
- `--uninstall` 支持 Cursor、TRAE、TRAE CN、ZCode 和 Kimi Code。其他集成通过宿主自己的插件管理卸载。
- 需要 Node.js 18 或更高版本。

<a id="_3-1-3-凭据体系"></a>

### 凭据来源

一共有四套凭据体系，各自使用不同的变量名和请求头。连接失败时，先确认集成用的是哪一套。

| 使用方 | 服务端 URL | API key | 身份 | 认证头 |
|---|---|---|---|---|
| 共享插件代码：Claude Code、Codex、Cursor、TRAE、ZCode、Kimi Code、OpenCode、DSH、pi、Agent Plugins | `OPENVIKING_URL`，其次 `OPENVIKING_BASE_URL` | `OPENVIKING_BEARER_TOKEN`，其次 `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_PEER_ID` | 只有 `Authorization: Bearer` |
| OpenClaw | `OPENVIKING_BASE_URL`，其次 `OPENVIKING_URL` | `OPENVIKING_API_KEY` 或 SecretRef | `OPENVIKING_ACCOUNT_ID`、`OPENVIKING_USER_ID` | `X-API-Key` |
| Hermes | `OPENVIKING_ENDPOINT` | `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_AGENT` | 同时发送 `X-API-Key` 和 `Bearer`。有 key 时不发租户头，服务端要求时补发并重试一次 |
| ov CLI | `ovcli.conf` | `ovcli.conf` | `--account`、`--user`、`--actor-peer-id` | `X-API-Key`；按 `auth_mode` 使用 Basic 或 Bearer。含两个及以上点号的 key 也会以 Bearer 发送 |

共享插件代码的规则：

- `OPENVIKING_CREDENTIAL_SOURCE`（`env`、`cli` 或 `auto`，默认 `auto`）决定凭据来源。`auto` 下，环境中只要有任一凭据变量就以环境为准；只有一个都没设、且 `ovcli.conf` 中有凭据时，插件才改用该文件。`env` 不读任何文件，URL 默认为 `http://127.0.0.1:1933`。
- hook 和 MCP 代理用同一个函数解析连接，且都不读取工作目录，所以从插件目录启动的代理与 hook 连到同一个服务端，使用同一身份。
- `X-OpenViking-Account` 和 `X-OpenViking-User` 只在 trusted 模式下发送。使用 API key 时，服务端从 key 中读取身份。

完整的解析顺序见[客户端配置字段](../configuration/02-client.md#连接与鉴权)和[插件开发指南](./18-plugin-development.md#_4-2-凭据不是普通-workspace-配置)。

<a id="_3-1-4-配置体系分层"></a>

### 配置与工作区文件

基于共享插件代码的集成按以下优先级（从高到低）读取行为配置：`OPENVIKING_*` 环境变量、每机工作区注册表、仓库的 `.openviking/config.local.json` 和 `.openviking/config.json`、`ovcli.conf` 的 `plugin.<harness>`、`ovcli.conf` 的 `plugin`，以及 `ov.conf` 中旧的 harness 段。工作区文件不能设置 URL、key 或其他凭据。见[插件配置](../configuration/02-client.md#插件配置)和[工作区配置](../configuration/02-client.md#工作区配置)。OpenClaw 读取 `openclaw.json`，Hermes 读取自己的 `.env` 和 `config.yaml`。

**工作区 peer**。这些集成默认用仓库规范化后的 `origin` URL 推导一个 peer 来标记记忆，所以同一仓库的所有 clone 共用一个 peer。不在仓库中时不发送 peer，记忆进入用户自己的空间。可以用 `peer.source` 和 `.openviking/config.json` 修改，见[工作区 peer](../configuration/02-client.md#工作区-peer)。OpenClaw 根据 `peer_role` 和 `peer_prefix` 推导 peer；`peer_role=sender` 时，如果宿主没有提供发送者，工具调用会失败。Hermes 默认不设助手 peer，除非由 `OPENVIKING_AGENT`、关联的 OpenViking peer 或 YAML 的 `agent` 键设置。Agent Plugins 不发送 peer。

只对部分集成生效的设置：

- `OPENVIKING_COMMIT_TURN_THRESHOLD`：Cursor 和 Kimi Code。TRAE、TRAE CN 和 ZCode 每轮都提交。
- `OPENVIKING_WRITE_PATH_ASYNC`：Claude Code、Codex 和 ZCode。
- `OPENVIKING_RECALL_COMPRESS=client`：Claude Code 和 Codex。`server` 和 `auto` 适用于[召回摘要](#召回摘要)中列出的所有集成。
- 工作区文件、`ovcli.conf` 的 `plugin` 段、`OPENVIKING_RECALL_DEDUP_TURNS`、`OPENVIKING_RECALL_QUERY_EXPANSION`、`OPENVIKING_PEER_SOURCE` 和 skill 清单相关设置：所有基于共享插件代码的集成，不包括 OpenClaw 和 Hermes。

<a id="_3-7-附加-ux-对照"></a>

## 其他宿主功能

| 集成 | 状态栏 | 命令 | 附带的 skill | 配置向导 |
|---|---|---|---|---|
| Claude Code | 有 | `/openviking-memory:ov` 显示服务端状态、身份和注入内容的来源 | `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`ov-memory-doctor` | 有 |
| Codex、TraeCode CLI 2.0 | 无 | 无 | 与 Claude Code 相同的四个 | 有 |
| Cursor | 无 | 无 | 一条常驻 rule，加上 `openviking-memory`、`openviking-skills`、`ov-experience-memory` | 安装器菜单 |
| TRAE、TRAE CN、ZCode | 无 | 无 | 无 | 安装器菜单 |
| OpenCode | 无 | 无 | 与 Cursor 相同的三个，仅在插件注册自己的 MCP 服务时提供 | 有 |
| DSH | 无 | 无 | 与 Cursor 相同的三个 | 无 |
| pi | 有 | `/viking`、`/viking commit` | 与 Cursor 相同的三个，`mcpEnabled` 为 `false` 时不提供 | 有 |
| OpenClaw | 无 | `/add-resource`、`/add-skill`、`/ov-search`、`/ov-query-config`、`/ov-recall-trace` | 三个插件 skill | 有；检查 key 的角色和版本兼容性 |
| Hermes（内置） | 无 | 无 | 无 | 有；`hermes memory status` 列出环境变量覆盖项 |
| ov CLI | 无 | CLI 本身 | 无 | `ov config` |

Claude Code、Codex、Cursor、TRAE、ZCode、Kimi Code、DSH 和 pi 还会安装 **URI guard**：文件工具的路径是 `viking://` URI 时，调用会被拒绝，并提示改用 OpenViking 工具；shell 命令中包含这种 URI 时照常执行，但附带一条提示。各宿主检查哪些工具不同，见[各集成说明](#各集成说明)。

<a id="_4-harness-档案卡"></a>

## 各集成说明

这里只写每个集成特有的内容，共享行为见上文各节。

### Claude Code

[Claude Code](./02-claude-code.md)。插件市场插件，包含 9 个 hook、一个 MCP 代理、一个斜杠命令、状态栏和 4 个 skill。

- 注册的 hook 比其他集成都多：`SessionStart`、`UserPromptSubmit`、`PostToolUse:Read`（skill 经验 hook，默认关闭）、作用于 Read、Glob、Grep、Edit、Write 和 Bash 的 `PreToolUse`（URI guard）、`Stop`、`PreCompact`、`SessionEnd`、`SubagentStart` 和 `SubagentStop`。
- 召回摘要默认通过本地 `claude -p` 生成，服务端摘要作为回退。
- 除 `kill -9` 外，所有退出方式都会发出提交。
- 捕获游标保存在 `/tmp` 下。系统清理后，整个会话会重新发送。
- 插件自身的开关关闭时，URI guard 仍然运行。

<a id="codex"></a><a id="trae-cli-traecode-cli-2-0"></a>

### Codex 与 TraeCode CLI 2.0

[Codex](./04-codex.md)、[TRAE](./13-trae.md)。插件市场插件，包含 6 个 hook（`SessionStart`、`UserPromptSubmit`、`PreToolUse:Bash`、`Stop`、`SessionEnd`、`PreCompact`）、一个 MCP 代理，以及与 Claude Code 相同的 4 个 skill。

- `SessionEnd` 只在干净退出且 Codex 0.145 及以上时触发。信号、崩溃、旧版本和 `codex app-server` 延后的情况都交给启动时的扫描，扫描使用 30 分钟空闲超时。
- `Stop` 和 `SessionEnd` 在分离的 worker 中写入。每个会话一把锁，按顺序处理 `Stop` worker、`PreCompact`、`SessionEnd` worker 和扫描。
- Codex 用 `apply_patch` 编辑文件，它没有路径参数，所以 Bash 上的 URI guard 只附加提示，从不拒绝。
- Codex 为每个 hook 保存信任记录。升级新增 hook 后，需要在 `/hooks` 中批准新的 hook。
- TraeCode CLI 2.0（可执行文件 `traecli`，配置 `~/.trae/traecli.toml`）是 Codex 系 CLI，安装同一个插件。只支持 2.0；早期面向 1.0 的独立插件已移除，`--harness trae-cli --uninstall` 仍可删除旧安装留下的副本。不带 `SessionEnd` 的版本会忽略该 hook。

### Cursor

[Cursor](./12-cursor.md)。hook 与 MCP 配置，包含 6 个 hook（`sessionStart`、`beforeSubmitPrompt`、`beforeReadFile`、`stop`、`preCompact`、`sessionEnd`）、一条常驻 rule 和 3 个 skill。

- URI guard 只在 `beforeReadFile` 运行，不受插件开关影响。shell 命令不检查，升级会移除旧版本注册的 `beforeShellExecution` 条目。
- 只捕获文本，所以 `ov-experience-memory` 能检索和应用 Experience，但无法把读取关联回所用的 Experience。
- `sessionEnd` 只在关闭窗口时触发，此时 Cursor 已停止执行 hook 命令，因此实际不会提交。
- 服务端不可达时，每轮都要等满 15 秒召回超时。

<a id="trae-trae-cn-ide-版"></a>

### TRAE 与 TRAE CN

[TRAE](./13-trae.md)。hook 与 MCP 配置，包含 4 个 hook：`SessionStart`、`UserPromptSubmit`、`PreToolUse` 和 `Stop`。MCP 服务名为 `openviking`。

- `PreToolUse` 拒绝对 `viking://` 路径执行 Read、Glob 和 Grep；Bash 和 RunCommand 命令中包含这种路径时附加提示。
- 每个有内容的 `Stop` 都会提交，退出时最多丢失正在进行的那一轮。
- TRAE 和 TRAE CN 只有会话前缀和安装路径不同。同样的工作在两个客户端中会产生不同的会话，要等抽取完成后才共享记忆。
- 不处理压缩，没有状态栏和 skill，也不单独处理子代理。

### ZCode

[社区插件：ZCode](./08-community-plugins.md#zcode-记忆集成)。hook 与 MCP 配置，包含 4 个 hook：`SessionStart`、`UserPromptSubmit`、作用于 Read、Glob 和 Grep 的 `PreToolUse`，以及 `Stop`。

- 捕获的轮次来自 rollout 文件 `~/.zcode/cli/rollout/model-io-<sid>.jsonl`；漏掉的 `Stop` 对应的轮次在下一个 `Stop` 补齐。
- 第一次捕获读取整个 rollout，所以在长时间运行的会话中安装会产生一次大上传。
- 捕获只去掉注入块，不做其他文本清理。
- `Stop` 默认在分离的 worker 中写入。

<a id="kimicode"></a>

### Kimi Code

[社区插件：Kimi Code](./08-community-plugins.md#kimi-code-记忆集成)。Kimi Code 托管插件，包含 7 个 hook（`SessionStart`、`UserPromptSubmit`、作用于 Read、Glob 和 Grep 的 `PreToolUse`、`Stop`、`PreCompact`、`SessionEnd`、`Interrupt`）和一个 MCP 代理。安装器在 `$KIMI_CODE_HOME/plugins/managed/openviking-memory` 下构建一份自包含的副本。

- 捕获的轮次来自 `wire.jsonl`。一轮对话只有在发送成功或存入离线队列后才会推进游标。
- 会话开场上下文在首次提问时注入，失败后在后续提问中重试，直到成功。
- `Stop`、`PreCompact` 和 `SessionEnd` 可以在后台写入。`Interrupt` 保持同步，请求预算 2 秒。
- `UserPromptSubmit` 返回纯文本上下文，不是 JSON。
- 安装时保留其他插件记录，不修改旧的 `config.toml` 或 `mcp.json`。

### OpenCode

[OpenCode](./10-opencode.md)。npm 插件 `@openviking/opencode-plugin` 用同一个入口支持 OpenCode v1（1.15.7 及以上）和 v2（2.0.15 及以上）。

- v2 每次提问只召回一次，把结果存进用户消息的 metadata；后续模型 step 直接复用，不发新请求，prompt 缓存因此保持稳定。每次执行结束时，v2 读取完整的用户、助手和工具消息用于捕获。
- v2 在每次执行结束后检查提交阈值，包括失败和被中断的执行，这些执行会保留状态。v2 只处理本 location 的会话，游标存在插件 storage 中。v2 没有 toast API，改为写日志。
- MCP 工具保留 `openviking_` 前缀；v2 设置 `codemode: false`。3 个 skill 只在插件注册自己的 MCP 服务时加入，hook-only 模式或关闭 `mcp.openviking` 时不加入。
- 提交超时 30 秒。

<a id="dsh-deepseek-harness"></a>

### DSH

[DeepSeek Harness](./17-dsh.md)。唯一的同进程 Cordis 插件。hook 直接调用 REST API；工具通过 `@deepseek-ai/dsh-mcp-client` 和共享的 stdio 代理接入。

- 监听 `agent/session-start`、`agent/pre-step`、`session/event`、`session/flush`、`tools/pre-execute` 和 `tools/post-execute`。
- `ctx.provide("openvikingMemory")` 让其他 Cordis 插件可以在它之上构建功能。
- 安装器默认使用 `web` profile，可以用 `--dsh-profile` 指定其他 profile。除 `dev` 外的所有模式都安装已发布的 npm 包。
- 默认捕获工具结果（`captureToolResults: true`），所以 `ov-experience-memory` 能完整运行；关闭捕获后，只能检索和应用 Experience。
- URI guard 先把工具名转成小写，在 `tools/pre-execute` 拒绝对 `viking://` 路径的文件工具调用，在 `tools/post-execute` 为包含这种路径的 `bash` 命令附加提示。
- Cordis patch 中写明的凭据优先于其他所有来源；对行为配置来说，patch 是优先级最低的一层。

<a id="pi-pi-coding-agent-extension"></a>

### pi

[pi](./11-pi.md)。原生扩展，包含 9 个事件处理器和 `/viking` 命令。召回、同步、会话开场上下文和 takeover 使用 REST；工具使用 pi 内置的 MCP 客户端。

- 工具就是服务端 `tools/list` 返回的内容，重命名为 `openviking_<tool>`。服务端增删工具后，pi 在下一个会话生效，不需要扩展发版。
- 从 0.3.x 升级时，所有工具都从 `viking_*` 改了名，没有别名过渡期。请更新 `--tools` 或 `--exclude-tools` 列表，否则工具会静默消失。
- `openviking_read` 返回文件内容，支持 `offset` 和 `limit`。目录 URI 会返回错误。旧的 `level="abstract"` 和 `"overview"` 读取在 MCP 中没有对应工具，可改用 `openviking_search(mode="context", detail="overview")` 或 `openviking_tree(include_abstract=true)`。
- 工具调用 15 秒超时（`OPENVIKING_TIMEOUT_MS`）。超时或按 ESC 只让本地调用失败，已发出的请求会继续执行，写入仍可能生效。
- `tool_call` 拒绝对 `viking://` 路径执行 read、grep、find、ls、write 和 edit；`tool_result` 为包含这种路径的 `bash` 输出附加提示。
- takeover 关闭时，用 `pi -c` 恢复会话会重新发送整个 branch。
- bypass 使用 `bypassSessionPatterns`；旧的 `bypassPatterns` 仍然有效。

### OpenClaw

[OpenClaw](./03-openclaw.md)。接管压缩的 context-engine 插件，包含 15 个工具、5 个斜杠命令、生命周期 hook、Gateway HTTP 路由，以及受功能开关控制的 RPC 方法。

- `memory_recall` 检索记忆，`ov_search` 检索资源和用户的 skill；通过显式参数，两个工具都能查另一类内容。
- 召回通过 `/find` 进行，不带会话 ID，所以没有查询扩写和去重，长会话中同一条记忆可能被重复注入。默认每条召回的记忆多一次 read（`recallPreferAbstract=false`）。
- 退出不提交。归档依赖 `/new`、`/reset` 和阈值。没有离线队列，失败的轮次不会重发。
- `compact()` 最多可能阻塞 5 分钟。
- 配置严格校验：有未知键或非法值时，插件进入 setup-only 模式。
- 提交阈值是 `tokenBudget`（默认 128,000）乘以 `commitTokenThresholdRatio`（默认 0.5）。

<a id="hermes-nous-research"></a>

### Hermes

[Hermes](./05-hermes.md)。Hermes 通过两种分发方式提供名为 `openviking` 的
memory provider：

- **目录插件**在本仓库的
  [`examples/hermes-plugin`](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin)
  中维护。先运行 `hermes plugins install openviking --enable`，再运行
  `hermes memory setup openviking`。
- **内置 provider** 存在于旧版 Hermes 的 `plugins/memory/openviking` 中，
  无需安装。本页的 Hermes 行描述
  [Hermes 提交 `989798c`](https://github.com/NousResearch/hermes-agent/tree/989798cd5e691230b54b2ea72e5937b68133014c/plugins/memory/openviking)
  时的内置 provider。

仍包含内置 provider 的版本会优先加载内置副本。当更新移除内置副本时，已配置
OpenViking 的 profile 会自动尝试安装目录插件。provider 名、配置和已存数据均保持不变。

两者是独立的代码库。行为差异如下：

| | 内置 provider（Hermes `989798c`） | 外部插件（本仓库） |
|---|---|---|
| 会话中的提交 | 无，只在会话边界 | 待提交 token 达到 20,000 时后台提交（`commit_token_threshold`） |
| 召回摘要 | 不支持 | 可选的服务端摘要（`recall_compress`） |
| 镜像 Hermes 内置记忆 | 只同步新增 | 同步新增、替换和删除，用 URI 注册表跟踪 |
| `viking_forget` | 带明确用户 ID 的用户记忆文件 | 还接受 `viking://~/`，拒绝不带用户 ID 的路径，删除前检查归属 |
| cron、子代理和 flush 上下文 | 该提交中没有说明 | 召回可用；跳过自动写入和镜像 |

上述固定提交中内置 provider 的行为：

- 每次模型调用前召回，走带会话的 `search/search`，以 `/find` 作为回退。默认值：6 条结果、分数阈值 0.15、4,000 字符、总计 4 秒、单请求 3 秒。
- `viking_remember` 把事实原样通过独立会话发送并提交，返回 `status: submitted`。
- 提交不保留消息（`keep_recent_count` 0）。上传不持久化，但在 POSIX 上，待提交标记让之后的启动能提交已退出进程留下的会话。
- 记忆写入 `viking://user/<uid>/memories/`；设置了 peer 时写入 `viking://user/<uid>/peers/<peer>/memories/`。
- 关联 OpenViking 的 `ovcli.conf` profile 会清除 Hermes `.env` 中的 5 个连接变量。`hermes backup` 会包含 `$HOME` 下默认或由环境变量指定的 `ovcli.conf`；通过 YAML 关联的文件需要单独备份。

<a id="_5-ov-cli-命令参考"></a><a id="_5-1-命令树"></a><a id="_5-2-全局选项与独有机制"></a><a id="_5-3-cli-独有于插件面的能力"></a>

### ov CLI

[安装与使用 CLI](../getting-started/05-cli-setup.md)。`ov` 是 REST API 的 Rust 客户端，没有宿主事件、自动召回或压缩处理；每个动作都是由你或脚本执行的命令。运行 `ov --help` 查看命令列表，JSON 输出见 [CLI 输出格式](../api/01-overview.md#cli-输出格式)。

CLI 提供而插件没有的能力：多个 `ovcli.conf` profile、账户与用户管理（`ov admin`）、用 `--sudo` 执行 root 操作、隐私策略管理（`ov privacy`）、快照、备份与恢复、导出与导入、`ov reindex`、在本地生成 root key（`ov system crypto init-key`）、`ov tui` 文件浏览器，以及会话自动提交策略（`ov session new --auto-commit-policy-json`、`ov session config set`）。其中的服务端操作也可以通过 HTTP API 和 SDK 完成。

在脚本中需要注意：

- `ov rm` 不确认就删除；`-r` 递归删除。
- 服务端报告不健康时 `ov health` 仍退出 0，表格模式的 `ov status` 始终退出 0。
- `echo_command` 开启时（默认开启），`find`、`search`、`ls`、`tree`、`grep` 和 `glob` 会先在标准输出打印一行 `cmd: …` 再输出结果，使用 `-o json` 时也是如此。
- 大多数命令在 compact JSON 模式下输出 `{"ok": true, "result": …}` 或 `{"ok": false, "error": …}`；`ov config` 系列命令输出 `{"status": "ok", "result": …}`。
- 运行命令前必须配置显示语言；在非交互 shell 中未配置时，CLI 以状态码 2 退出。
- `ov doctor` 只存在于用 `uv tool install openviking` 安装的 Python 包中，用于检查服务端的 `ov.conf`。npm 和 cargo 安装的二进制不包含它。

<a id="_6-自定义-agent-接入指南"></a><a id="_6-1-接入路径-×-能获得的能力"></a>

## 自建集成

如果你的 Agent 不在上面的列表中，按需要的行为选择工作量最小的方式：

| 方式 | 工作量 | Agent 可用的工具 | 自动召回与捕获 | 提交 |
|---|---|---|---|---|
| [通过 MCP 连接](#通过-mcp-连接) | 几分钟 | 服务端 MCP 工具 | 无；由模型自己调用工具 | 只有 `remember`，在它自己的会话中 |
| [Agent Plugins 插件包](./15-agent-plugins.md) | 几分钟 | 服务端 MCP 工具，外加一个教模型使用这些工具的 skill | 无 | 只有 `remember` |
| [HTTP API、SDK 或 LangChain](#调用-http-api-或-sdk) | 几小时 | 取决于你调用的接口 | 自己实现，或使用 LangChain 中间件 | 由你决定 |
| [复用共享插件代码](#复用共享插件代码) | 几天 | 通过代理使用服务端 MCP 工具 | 召回、捕获、提交和离线队列 | 取决于你接入的宿主事件 |

<a id="_6-2-路径1-通用-mcp-直连-推荐起步"></a>

### 通过 MCP 连接

支持 Streamable HTTP 的客户端可以连接服务端的 `/mcp`。下面的示例使用常见的 `mcpServers` 格式；其他客户端的字段见 [MCP 客户端](./06-mcp-clients.md)。

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://127.0.0.1:1933/mcp",
      "headers": {
        "Authorization": "Bearer <api_key>"
      }
    }
  }
}
```

使用用户或管理员 API key，服务端从 key 中读取账户和用户。`X-OpenViking-Account` 和 `X-OpenViking-User` 只在 trusted 身份模式下生效，不能覆盖普通 key 的身份。需要工作区 peer 上下文时，添加 `X-OpenViking-Actor-Peer`，见[认证](../guides/04-authentication.md)。只支持 stdio 的客户端可以使用 [Agent Plugins 代理](./15-agent-plugins.md)。直接通过 MCP 连接没有自动召回、捕获或提交。

<a id="_6-3-路径2-程序化接入"></a>

### 调用 HTTP API 或 SDK

- **REST**。召回用 `POST /api/v1/search/search`；查询扩写和去重需要 `mode: "context"`、`session_id` 和 `dedup_turns`（见[召回请求如何到达服务端](#召回请求如何到达服务端)），`rewrite` 用于请求[召回摘要](#召回摘要)。写入用 `POST /api/v1/sessions/{id}/messages/batch`（最多 100 条，会自动创建会话），提交用 `POST /api/v1/sessions/{id}/commit`，读取用 `GET /api/v1/content/read`。见[会话](../api/05-sessions.md)和[检索](../api/06-retrieval.md)。服务端自动提交见[服务端会话与提交机制](#服务端会话与提交机制)。
- **LangChain 与 LangGraph**（`pip install langchain-openviking`）。`OpenVikingContextMiddleware` 在模型调用前把召回内容注入 `<openviking_context>`，在 Agent 运行后按 `CommitPolicy`（默认 `never`）捕获并提交。只读调用重试一次，写入从不重试。部分写入会抛出 `OpenVikingPartialWriteError`，附带重试剩余部分所需的计数。见 [LangChain / LangGraph](./07-langchain-langgraph.md)。
- **Open WebUI**。`python -m openviking_openwebui` 启动一个独立的 OpenAPI 工具服务器，提供 7 个工具，没有删除工具和 hook。见[其他集成](#其他集成)。

<a id="_6-4-路径3-要自动-hook-面时复用参考实现"></a><a id="_2-2-memory-plugin-shared-共享层"></a>

### 复用共享插件代码

需要自动召回、捕获、提交和离线重试时，复用现有代码，不必重写：

- **`examples/memory-plugin-shared/lib/`**（Node.js）包含共享模块：带回退的召回、会话开场上下文、捕获清理、离线队列、批量发送、MCP 代理、会话 ID 和凭据。通过 hook 文件配置的宿主可以在 `examples/agent-hook-plugin/hosts/` 下添加适配器，Cursor、TRAE、ZCode 和 Kimi Code 都是这样接入的。
- **Agent Plugins 插件包**（`agent-plugins/`）由可移植的 `plugin.json`、`skills/` 和 `mcp.json` 组成，带 stdio 到 HTTP 的代理，没有 hook。它的 `plugin.test.mjs` 检查是否符合 Agent Plugins 规范，可以作为你自己插件包的 lint 基线。

[插件开发指南](./18-plugin-development.md)介绍模块职责、配置、生成副本、安装器和测试。无论构建什么，都遵守以下三条规则，让它的行为与现有集成一致：

1. 使用带会话 ID 和 `dedup_turns` 的 context 模式召回，让服务端能扩写查询并跨轮去重。
2. 不要让适配器自己的超时截断共享代码计算出的截止时间。
3. 在关闭时提交。否则低于阈值的对话尾部会一直未提交，直到其他事件触发提交。如果宿主没有关闭事件，开启 `memory.session_auto_commit.enabled` 并为会话设置策略。完成后，在真实宿主中检查退出事件是否触发、写入是否完成。

<a id="_7-附录-非-coding-集成速览"></a>

## 其他集成

| 集成 | 是什么 | 工具 | 会话与提交 | 失败处理 | 何时生效 |
|---|---|---|---|---|---|
| [Open WebUI](./08-community-plugins.md#open-webui-tool-server) | 独立的 OpenAPI 工具服务器 | 7 个：`ov_search`、`ov_recall_memories`、`ov_add_memory`、`ov_list_memories`、`ov_read_resource`、`ov_add_resource`、`ov_session_status`；没有删除工具 | 没有会话 | 不重试，不缓存失败。它的 `/health` 只回显配置，不连接 OpenViking | 你启动进程后 |
| [LangChain 与 LangGraph](./07-langchain-langgraph.md) | Python SDK 适配层：retriever、tools、store、middleware、recorder | `create_openviking_tools()` 提供 12 个工具；`viking_forget` 不在默认 agent profile 中 | 会话 ID 和 thread ID 由调用方提供；`CommitPolicy` 默认 `never` | 读取重试一次，写入不重试；部分写入抛出结构化错误 | 你构造它之后 |
| [Agent Plugins](./15-agent-plugins.md) | 带 stdio 到 HTTP MCP 代理的可移植插件包 | 服务端 MCP 工具；没有 hook，由 skill 告诉模型何时调用 | 只有 `remember` | 代理在 401 或 403 后用新凭据重试一次，在 400 或 404 后用新 MCP 会话重试一次 | 客户端加载后 |
| [MCP 客户端](./06-mcp-clients.md) | 直连 `/mcp` | 服务端 MCP 工具 | 只有 `remember` | 由客户端决定 | 服务端始终提供 |
| [日志导入](./09-log-ingestion.md) | `openviking-server ingest`，在日志所在的机器上运行 | 无，只导入 | 会话 ID `{prefix}__{harness}__{id}`；待提交 token 达到 6,000 或空闲 5 秒时提交，`keep_recent_count` 0 | 游标库、单实例锁，崩溃后核对服务端消息数 | 默认关闭；`ingest.enabled` 和各适配器的 `enabled` 都要开启。适配器：Claude Code、Codex、Hermes、OpenCode、OpenClaw、Cursor |
| [OpenViking Helper](./14-openviking-helper.md) | 闭源桌面应用 | 本页不涉及 | 本页不涉及 | 本页不涉及 | 本页不涉及 |

## 源码依据

以上对比依据以下位置核对。需要确认某项限制在你的版本中是否成立时，可以查看这些位置。

- 服务端 MCP 工具：`openviking/server/mcp_endpoint.py`，以其中的 `@mcp.tool` 注册为准。
- 写入与删除检查：`openviking/storage/content_write.py` 和 `openviking/storage/viking_fs/_access.py`。
- 服务端自动提交：`openviking_cli/utils/config/memory_config.py` 和 `openviking/session/auto_commit_policy.py`。
- 插件配置与默认值：`examples/memory-plugin-shared/lib/config-schema.mjs`。
- hook 宿主：`examples/agent-hook-plugin/hosts/`，对应 Cursor、TRAE、ZCode 和 Kimi Code。
- pi takeover：`examples/pi-coding-agent-extension/lib/takeover-core.mjs`。
- OpenClaw 工具和生命周期 hook：`examples/openclaw-plugin/registries/openviking-tools.ts` 和 `examples/openclaw-plugin/plugin/openviking-lifecycle-hooks.ts`。
- Hermes：[Hermes](#hermes) 中链接的旧版内置 provider 固定提交，以及目录插件的
  [README](https://github.com/volcengine/OpenViking/blob/main/examples/hermes-plugin/README.md)。

## 参见

- [选择 Agent 接入方式](./01-overview.md)
- [调整召回延迟](./19-recall-tuning.md)
- [开发与维护 Agent 插件](./18-plugin-development.md)
- [MCP 客户端](./06-mcp-clients.md)
- [MCP 工具与协议](../guides/06-mcp-integration.md)
- [客户端配置字段](../configuration/02-client.md)
- [检索 API](../api/06-retrieval.md)
- [会话 API](../api/05-sessions.md)
- [认证](../guides/04-authentication.md)
