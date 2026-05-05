# Agent 工具命名收敛表

这张表从现有插件真实暴露的工具出发，判断哪些能力可以在不改变语义的前提下复用 OpenViking 已有接口，哪些应该从插件内重复实现下沉为新的 OV 通用接口，哪些应继续留在插件内。

判断标准：只有在不改变原功能、默认 scope、参数语义、返回语义和 lifecycle 行为时，才标记为“可以直接收敛”。如果 OV 没有对应接口，但多个插件都需要同一能力，再考虑补 OV 通用接口；单宿主专用能力继续留在插件内。

注意：“当前 OV 是否已有对应接口”表示 OV 是否已有可以承载该语义的稳定接口，不表示插件暴露给 Agent 的工具名可以直接改成 `viking_xxx`。底层 REST / MCP / CLI 积木不应该长期直接暴露给插件拼装；插件应优先调用 OV 提供的语义级接口。只有当 OV 尚未提供语义级接口时，插件才临时调用底层接口并保留宿主侧默认 scope、参数名、返回格式或 lifecycle 行为。

本次盘点覆盖：

- 本仓插件：OpenClaw、Claude Code、Codex MCP、opencode-memory-plugin、opencode skill plugin。
- 本仓外插件：Hermes 官方仓内置 OpenViking memory provider，本仓没有实现代码，本 PR 不改。
- 通用 MCP 客户端：Cursor / Trae / Manus / Claude Desktop / ChatGPT 等直接消费 OV server `/mcp`，不是单独插件；本 PR 把显式记忆写入工具 `store` 收敛为 `remember`。

| 插件 / 接入 | 当前暴露工具名 / 能力 | 建议目标名 / OV 接口 | 当前 OV 是否已有对应接口 | 能否直接收敛 | 结论 / 原因 |
|---|---|---|---|---|---|
| OpenClaw | `memory_recall` | `viking_search` | 有：REST `/api/v1/search/find`、MCP `search`、CLI `ov search` | 否 | OV 已有基础搜索，但该工具默认只做 memory recall；resources 只有 `recallResources=true` 才会加入，不能直接等同全局 search。 |
| OpenClaw | `ov_search` | `viking_search` | 有：REST `/api/v1/search/find`、MCP `search`、CLI `ov search` | 否 | OV 已有基础搜索，但该工具自身和当前基础搜索不等价：默认 scope 是 `viking://resources` + `viking://agent/skills`，默认不搜 memories；参数名是 `uri` 而不是 `target_uri`；不设置默认 score threshold；多 scope 查询支持部分成功返回；返回文案和 details 结构也不同。 |
| OpenClaw | `memory_store` | `remember` | 本 PR 新增：REST `POST /api/v1/agent/remember` | 不改工具名，执行路径下沉 | 该工具语义是显式记忆写入：写 message、commit，并等待记忆提取完成。底层 session 写入与 commit 不应继续由插件拼装，本 PR 下沉为 OV 语义级 remember 接口；OpenClaw 仍保留 `memory_store` 对外工具名。 |
| OpenClaw | `memory_forget` | `viking_forget` | 有：REST `DELETE /api/v1/fs`、MCP `forget`、CLI `ov rm` | 否 | OV 已有 URI 删除，但该工具还支持按 query 搜索候选并删除唯一高置信 memory。 |
| OpenClaw | `ov_import` | `viking_add_resource` / `viking_import` | 部分有：REST `/api/v1/resources`、MCP `add_resource`、CLI `ov add-resource` | 否 | OV 已有 resource 导入，但该工具还支持 skill 导入；不能只改成 `viking_add_resource`。 |
| OpenClaw | `ov_archive_search` | `viking_archive_search` | 无 | 否 | OV 当前没有 archive search 的通用接口。 |
| OpenClaw | `ov_archive_expand` | `viking_archive_expand` | 无 | 否 | OV 当前没有 archive expand 的通用接口。 |
| Claude Code MCP | `store` | `remember` | 本 PR 新增：REST `POST /api/v1/agent/remember`，并由 OV server `/mcp` 暴露 `remember` | 改工具名，执行路径下沉 | 该工具是通用 MCP 客户端可见的显式记忆写入能力。OV server `/mcp` 已由 server identity 表明 provider，因此工具名使用能力名 `remember`，不带 `viking_` 前缀。 |
| Claude Code MCP | `search` / `read` / `list` / `add_resource` / `grep` / `glob` / `forget` / `health` | OV server `/mcp` 内置工具 | 有：`/mcp` 已提供 | 本 PR 不改 | 这些工具不是本轮 remember 收敛范围。 |
| Claude Code hooks | `UserPromptSubmit` 自动召回，无模型可见工具名 | `/api/v1/search/find` + `/api/v1/content/read` | 有 | 已调用 OV，但不改 | hook 负责多 scope、ranking、token budget、注入格式，这些是 Claude Code lifecycle 语义；底层检索已直接调用 OV。 |
| Claude Code hooks | `Stop` / `PreCompact` / `SessionEnd` / `SubagentStop` 自动捕获与 commit，无模型可见工具名 | `/api/v1/sessions/*` | 有 | 已调用 OV，但不改 | hook 负责 transcript 解析、增量状态、subagent 隔离和异步写；底层 session 写入和 commit 已直接调用 OV。 |
| Codex MCP | `openviking_recall` | `viking_search` | 有：REST `/api/v1/search/find`、MCP `search`、CLI `ov search` | 否 | OV 已有基础搜索，但该工具默认只搜 `viking://user/memories`，并按 memory 结果格式输出。 |
| Codex MCP | `openviking_store` | `remember` | 本 PR 新增：REST `POST /api/v1/agent/remember` | 改工具名，执行路径下沉 | 该工具语义是显式记忆写入并同步等待提取结果；本 PR 下沉为 OV 语义级 remember 接口，并将 Codex 对外工具名收敛为 `remember`。 |
| Codex MCP | `openviking_forget` | `viking_forget` | 有：REST `DELETE /api/v1/fs`、MCP `forget`、CLI `ov rm` | 是 | 语义基本是按 URI 删除；插件侧可以继续保留 memory-only guard，再调用 OV 删除接口。 |
| Codex MCP | `openviking_health` | `viking_health` | 有：REST `/health`、MCP `health`、CLI `ov health` | 是 | 健康检查语义一致。 |
| opencode-memory-plugin | `memsearch` | `viking_search` | 有：REST `/api/v1/search/find`、MCP `search`、CLI `ov search` | 否 | OV 已有基础搜索，但该工具有 `auto` / `fast` / `deep` 模式，并支持 session-aware deep search。 |
| opencode-memory-plugin | `memread` | `viking_read` | 有：REST `/api/v1/content/read`、MCP `read`、CLI `ov read/abstract/overview` | 否 | OV 已有基础 read，但该工具支持 `abstract` / `overview` / `read` / `auto` 读取层级。 |
| opencode-memory-plugin | `membrowse` | `viking_browse` | 有：REST `/api/v1/fs/ls`、MCP `list`、CLI `ov ls/tree` | 否 | OV 已有基础 browse，但该工具支持 `list` / `tree` / `stat` / `simple` 多种视图。 |
| opencode-memory-plugin | `memcommit` | `remember` | 有：REST `/api/v1/sessions/{id}/commit` | 否 | 基础 session commit 已有；该工具是提交当前 OpenCode session，并等待或跟踪记忆提取。 |
| opencode skill plugin | `ov search` / `ov grep` / `ov glob` / `ov read` / `ov abstract` / `ov overview` / `ov ls` / `ov tree` / `ov add-resource` / `ov rm` / `ov health` | OV CLI | 有 | 已下沉，不改 | 该插件不是注册 native tools，而是安装 skill，指导模型通过 shell 调 OV CLI；已经直接复用 OV 能力。 |
| Hermes memory provider | 外部 Hermes 官方仓 `openviking` provider | Hermes memory provider 接口 | 本仓无代码，需在 Hermes 仓单独盘点 | 不在本 PR 改 | Hermes 插件不在 OV 仓；本 PR 只能记录范围，不能在本仓做工具名或接口收敛。 |

本 PR 只落显式记忆写入收敛：

- 新增 `POST /api/v1/agent/remember`。
- Codex `openviking_store` 改名为 `remember`，并复用该语义级接口。
- Codex `openviking_forget` / `openviking_health` 改名为 `viking_forget` / `viking_health`，这两个语义和 OV 已有接口一致。
- OV server `/mcp` 的 `store` 改名为 `remember`，并复用该语义级接口。
- OV CLI `add-memory` 改名为 `remember`，并复用该语义级接口。
- OpenClaw `memory_store` 保留宿主惯用工具名，但执行路径下沉到该语义级接口。

本 PR 不引入新的 ToolCatalog / agent tools 抽象，不迁移 OpenClaw、Claude Code、opencode、Hermes 的其他既有工具名和执行语义。

## 后续下沉原则

1. 先看 OV 是否已有语义级接口；有且无语义变化，插件就直接复用。
2. 只有底层接口、但多个插件都在重复拼装同一能力时，补一个具体 OV 语义级接口，不让插件长期直接拼底层积木。
3. 只有单个宿主需要，或依赖宿主 lifecycle、默认 scope、返回格式的能力，继续留在插件里。
