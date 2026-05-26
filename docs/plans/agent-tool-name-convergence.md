# OpenViking Agent 工具名与通用接口收敛

这份文档从现有插件真实暴露的工具和 hook 出发，整理 OpenViking 面向不同 Agent 提供通用能力时的命名和下沉边界。

当前结论：OpenViking 自己提供的通用 Agent / MCP 工具名默认不带 `viking_` 前缀，例如 `find`、`search`、`remember`、`forget`、`health`。原因是工具执行入口已经是 OpenViking MCP server 或 OpenViking 插件，`viking://` URI 也已经表达了资源域；继续在工具名里重复 `viking_` 只会增加噪音。Hermes 是例外：它在 Hermes 官方仓里作为 memory provider 接入，宿主内可能同时存在其他 memory provider，因此可以保留 `viking_*` 作为 provider 级显式区分。

判断标准：只有不改变原功能、默认 scope、参数语义、返回语义和 lifecycle 行为时，才标记为“可以直接收敛”。如果多个插件重复实现同一能力，才考虑把能力下沉为 OpenViking 通用接口；单宿主专用能力继续留在插件内。

本次盘点覆盖：

- 本仓插件：OpenClaw、Claude Code、Codex MCP、opencode-memory-plugin、opencode skill plugin。
- 本仓外插件：Hermes 官方仓内置 OpenViking memory provider，本仓没有实现代码，本 PR 只记录命名原则。
- 通用 MCP 客户端：Cursor / Trae / Manus / Claude Desktop / ChatGPT 等直接消费 OV server `/mcp`。

## OpenViking 通用工具名

| 通用工具名 / 能力名 | 当前是否由 OV 提供 | 说明 |
|---|---|---|
| `find` | 是：REST `/api/v1/search/find`、MCP `find`、CLI `ov find` | 轻量语义检索。调用方决定 `target_uri`、limit、score threshold 和展示格式。 |
| `search` | 是：REST `/api/v1/search/search`、MCP `search`、CLI `ov search` | session-aware / deep search。可结合 `session_id` 做上下文检索。 |
| `remember` | 是：REST `/api/v1/sessions/*`、MCP `remember` | 写入消息并 commit，触发 memory extraction。 |
| `forget` | 是：REST `DELETE /api/v1/fs`、MCP `forget`、CLI `ov rm` | 按明确 `viking://` URI 删除。 |
| `health` | 是：REST `/health`、MCP `health`、CLI `ov health` | 检查 OpenViking server 可达性和基础健康状态。 |
| `read` | 是：REST `/api/v1/content/read`、MCP `read`、CLI `ov read` | 读取指定 URI 的内容。 |
| `list` | 是：REST `/api/v1/fs/ls`、MCP `list`、CLI `ov ls` | 列出目录或命名空间下的节点。 |
| `add_resource` | 是：REST `/api/v1/resources`、MCP `add_resource`、CLI `ov add-resource` | 添加外部资源。 |
| `add_skill` | 是：REST `/api/v1/skills`、CLI `ov add-skill` | 添加或注册 Agent skill。 |
| `grep` / `glob` | 是：REST `/api/v1/search/grep` 等、MCP `grep` / `glob`、CLI `ov grep` / `ov glob` | 精确文本检索和路径匹配。 |
| `archive_search` / `archive_read` | 否 | OpenClaw archive 工具绑定 session archive 语义，目前 OV 只有底层 grep/read 能力。 |

## 1. Search

Search 是显式搜索：用户或模型主动发起 query，目标是查找 memories、resources、skills 或 session context，并把结果作为可继续读取 / 引用的候选返回。OpenViking 已提供 `find` 和 `search` 两层：`find` 是轻量检索，`search` 是 session-aware deep search。

| 插件 / 接入 | 当前暴露工具名 / 入口 | 当前实际调用的 OV 接口 | 能否直接收敛到通用工具名 | 结论 / 原因 |
|---|---|---|---|---|
| OV server `/mcp` | `find` / `search` | 直接调用 `service.search.find()` / `service.search.search()` | 已收敛 | 本 PR 新增 `find`，并让 `search` 保持 deep/session-aware 语义。 |
| Codex MCP | `find` | REST `/api/v1/search/find`，默认 `target_uri=viking://user/memories` | 已收敛到工具名 | 工具名已统一为 `find`；默认 memory-only scope 是 Codex 插件自己的安全约束。 |
| OpenClaw | `memory_search` | REST `/api/v1/search/find`；默认查 resources + agent skills | 否 | `memory_search` 已同步到 `openclaw.plugin.json`；它不等价于通用 `find`。 |
| Claude Code hooks / MCP | 无显式 search 工具；MCP 可直接用 OV `/mcp` 的 `find` / `search` | hooks 底层用 REST；MCP 直连 OV `/mcp` | 不改 | Claude Code 的显式搜索由通用 MCP 承担；hook search 行为归入 Recall。 |
| opencode-memory-plugin | `memsearch` | REST `/api/v1/search/find` 或 `/api/v1/search/search` | 否 | 它有 `auto` / `fast` / `deep` 模式，并会按 OpenCode session 自动选择接口和注入 `session_id`。 |
| opencode skill plugin | `ov search` / `ov find` 等 CLI 命令 | OV CLI | 已下沉，不改名 | 这是 shell skill，不是 native tool；`ov` 是 CLI 命令名前缀。 |
| Hermes OpenViking provider | `viking_search` | Hermes provider 内部调用 OpenViking semantic search | 不在本 PR 改 | Hermes 是外部官方仓 provider，工具名保留 `viking_*` 用于和其他 memory provider 区分。 |

## 2. Recall

Recall 是上下文召回：宿主在 prompt lifecycle 中自动取回相关 memory / skill / resource，并决定排序、预算、注入格式和触发时机。它不是简单的用户显式搜索，因此当前不定义一个通用 `recall` 工具名；底层可以复用 `find`、`search`、`read`。

| 插件 / 接入 | 当前暴露工具名 / 入口 | 当前实际调用的 OV 接口 | 能否直接收敛到通用工具名 | 结论 / 原因 |
|---|---|---|---|---|
| OpenClaw | `memory_recall` | REST `/api/v1/search/find`；默认并行查 user/agent memories，配置开启时追加 resources | 否 | 它是 OpenClaw 的记忆召回工具，默认 scope、资源开关、去重和返回格式都属于宿主语义。 |
| Claude Code hooks | `UserPromptSubmit` 自动召回，无模型可见工具名 | REST `/api/v1/search/find` + `/api/v1/content/read` | 不改 | hook 负责多 scope、ranking、token budget 和注入格式，底层已复用 OV。 |
| opencode-memory-plugin | 自动上下文注入路径 | REST `/api/v1/search/find` 或 `/api/v1/search/search` | 不改 | 召回绑定 OpenCode session 和注入策略，不是一个独立通用工具名。 |
| Codex MCP | 无自动 recall；模型显式调用 `find` | REST `/api/v1/search/find` | 不涉及 | Codex MCP 插件是显式工具，不做 lifecycle recall。 |
| opencode skill plugin | 无自动 recall；模型按 skill 说明调用 CLI | OV CLI | 不涉及 | shell skill 不做宿主 lifecycle 注入。 |
| Hermes OpenViking provider | 自动 provider context / prefetch | provider turn 前 prefetch 相关 memories 并注入 system prompt | 不在本 PR 改 | Hermes memory provider lifecycle 自带 recall：turn 前预取、非阻塞注入；不是 OV 通用 `recall` 工具。 |

## 3. Remember

Remember 是写入长期记忆：把文本、消息或会话内容写入 OpenViking session，并触发 commit / memory extraction。OV 通用能力名就是 `remember`；`memory_store` 只是 OpenClaw 当前宿主工具名，不代表 OV 还要提供另一个通用写入工具。

| 插件 / 接入 | 当前暴露工具名 / 入口 | 当前实际调用的 OV 接口 | 能否直接收敛到通用工具名 | 结论 / 原因 |
|---|---|---|---|---|
| OV server `/mcp` | `remember` | session message + commit | 已收敛 | 通用 MCP 客户端直接使用 `remember`。 |
| Codex MCP | `remember` | REST `/api/v1/sessions/*` | 已收敛到工具名 | 工具名已统一为 `remember`；插件仍保留同步等待提取结果的返回语义。 |
| OpenClaw | `memory_store` | REST `/api/v1/sessions/*` | 否 | 它会写 session message、commit、等待提取，并绑定 OpenClaw 的 session/agent 映射。是否改名仍需单独评估。 |
| Claude Code hooks | `Stop` / `PreCompact` / `SessionEnd` / `SubagentStop` 自动捕获，无模型可见工具名 | REST `/api/v1/sessions/*` | 不改 | hook 负责 transcript 解析、增量状态、subagent 隔离和异步写，底层已复用 OV。 |
| opencode-memory-plugin | `memcommit` | REST `/api/v1/sessions/{id}/messages` + `/commit` | 否 | 它绑定当前 OpenCode session，会先 flush pending messages，并等待或跟踪记忆提取。 |
| opencode skill plugin | 无 native remember 工具；模型按 skill 说明调用 CLI 写入/导入 | OV CLI | 不涉及 | shell skill 不注册 native tool。 |
| Hermes OpenViking provider | `viking_remember`；turn 后 `sync_turn()`；session end commit | `POST /sessions/{id}/messages` + `POST /sessions/{id}/commit` | 不在本 PR 改 | Hermes provider 会在响应后同步 conversation turn，并在 session end 触发 extraction；`viking_remember` 是显式记忆标注工具。 |

## 4. Forget

接口功能：按明确 `viking://` URI 删除 OpenViking 中的记忆或资源。

| 插件 / 接入 | 当前暴露工具名 / 入口 | 当前实际调用的 OV 接口 | 能否直接收敛到通用工具名 | 结论 / 原因 |
|---|---|---|---|---|
| OV server `/mcp` | `forget` | `DELETE /api/v1/fs` | 已收敛 | 通用 MCP 工具名就是 `forget`。 |
| Codex MCP | `forget` | `DELETE /api/v1/fs` | 已收敛 | 本 PR 将 Codex MCP 的删除工具改为 `forget`。 |
| OpenClaw | `memory_forget` | `DELETE /api/v1/fs`，必要时先 search 候选 | 否 | 它还支持按 query 搜索候选并删除唯一高置信 memory，不等价于通用 URI 删除。 |
| Claude Code hooks / MCP | hooks 无 forget；MCP 可直接用 OV `/mcp` 的 `forget` | OV `/mcp` 或 REST `DELETE /api/v1/fs` | 已下沉，不改 | 删除是显式危险操作，Claude Code 通过 MCP 工具使用 OV 通用能力。 |
| opencode-memory-plugin | 无独立 forget native tool | 无 | 不涉及 | 当前插件主能力是 search/read/browse/commit。 |
| opencode skill plugin | `ov rm` | OV CLI | 已下沉，不改名 | 这是 CLI 能力，不是 Agent native tool 命名。 |
| Hermes OpenViking provider | 无公开 `viking_forget` 工具 | 无公开删除路径 | 不在本 PR 改 | 最新 Hermes OpenViking provider 公开工具列举不包含 forget。 |

## 5. Health

接口功能：检查 OpenViking server 是否可达、可用。

| 插件 / 接入 | 当前暴露工具名 / 入口 | 当前实际调用的 OV 接口 | 能否直接收敛到通用工具名 | 结论 / 原因 |
|---|---|---|---|---|
| OV server `/mcp` | `health` | `/health` | 已收敛 | 通用 MCP 工具名就是 `health`。 |
| Codex MCP | `health` | `/health` | 已收敛 | 本 PR 将 Codex MCP 的健康检查工具改为 `health`。 |
| OpenClaw | 无模型可见 health 工具 | 初始化时调用 `/health` | 不涉及 | OpenClaw 只在插件初始化时探活，不需要暴露工具。 |
| Claude Code hooks / MCP | 无专用 health hook；MCP 可直接用 OV `/mcp` 的 `health` | OV `/mcp` 或 REST `/health` | 已下沉，不改 | 健康检查属于通用 MCP 能力。 |
| opencode-memory-plugin | 无独立 health native tool | 插件初始化 / 客户端可探活 | 不涉及 | 不作为模型可见工具收敛。 |
| opencode skill plugin | `ov health` | OV CLI | 已下沉，不改名 | 这是 CLI 命令名前缀。 |
| Hermes OpenViking provider | 无公开 `viking_health` 工具 | 配置 / provider 初始化检查 endpoint | 不在本 PR 改 | 最新公开工具列举不包含 health。 |

## 6. Add Resource / Add Skill / Archive / Read / Browse

Resource 和 skill 导入不再合并成 `import`。二者落点命名空间、底层 API 和参数不同：resource 进入 `viking://resources/...`，skill 进入 `viking://agent/skills/...`。因此 OpenClaw 的 Agent 可见工具应直接拆成 `add_resource` 和 `add_skill`，而不是继续暴露 `ov_import(kind=...)`。

| 插件 / 接入 | 当前暴露工具名 / 入口 | 当前实际调用的 OV 接口 | 能否直接收敛到通用工具名 | 结论 / 原因 |
|---|---|---|---|---|
| OpenClaw | 原 `ov_import(kind=resource)`，现 `add_resource` | local 文件 / 目录先 `temp_upload`，再走 `/api/v1/resources`；remote URL / Git URL 直接走 `/api/v1/resources` | 是 | resource 导入已有稳定 OV 能力名和底层接口。OpenClaw 插件仍负责本地文件 / 目录上传与 zip，但 Agent 可见工具名收敛为 `add_resource`。 |
| OpenClaw | 原 `ov_import(kind=skill)`，现 `add_skill` | local skill 文件 / 目录先 `temp_upload`，或 `data` 直接走 `/api/v1/skills` | 是 | skill 导入已有稳定 OV 能力名和底层接口，且参数与 resource 不同，拆成独立工具比 `ov_import + kind` 更清晰。 |
| OpenClaw slash command | 原 `/ov-import`，现 `/add-resource` / `/add-skill` | 分别调用 `add_resource` / `add_skill` 执行路径 | 是 | 用户手动命令也和 Agent 可见工具名保持一致；不再用 `--kind` 在一个命令里复用两种导入语义。 |
| OV server `/mcp` | `add_resource` | MCP tool `add_resource` 调 resource service | 已收敛 | 当前只支持 remote URL / Git URL，不支持本地 path，也没有 `add_skill`。 |
| OpenClaw | `ov_archive_search` | REST `/api/v1/search/grep` 等底层能力 | 否 | 语义是搜索当前 session history archive，不是通用 grep。 |
| OpenClaw | `ov_archive_expand` | REST `/api/v1/sessions/{id}/archives/{archive_id}` | 否 | 语义是读取当前 session archive 片段，不是通用 read。 |
| Codex MCP | 无 import / archive / browse 工具；可通过 OV `/mcp` 使用 `read` 等通用能力 | REST / MCP | 不涉及 | Codex 示例插件只保留显式 memory 四件套。 |
| Claude Code hooks / MCP | MCP 可直接用 OV `/mcp` 的 `read` / `list` / `add_resource` / `grep` / `glob` | OV `/mcp` | 已下沉，不改 | Claude Code 插件 `.mcp.json` 指向 OV server，直接消费通用 MCP 工具。 |
| opencode-memory-plugin | `memread` | REST `/api/v1/content/{abstract,overview,read}` + `/api/v1/fs/stat` | 否 | 它支持 `auto` 层级，会先 stat 再选择 overview/read。 |
| opencode-memory-plugin | `membrowse` | REST `/api/v1/fs/ls` / `tree` / `stat` | 否 | 它把 `list` / `tree` / `stat` / `simple` 视图合成一个工具。 |
| opencode skill plugin | `ov search` / `ov read` / `ov ls` / `ov rm` 等 CLI 命令 | OV CLI | 已下沉，不改名 | 这是 shell skill，不是 native tool；`ov` 是 CLI 命令名前缀，不属于 Agent 工具名收敛问题。 |
| Hermes OpenViking provider | `viking_read` / `viking_browse` / `viking_add_resource` | provider 内部调用 OpenViking read / browse / resource ingest | 不在本 PR 改 | Hermes 最新公开工具包含 read、browse、add_resource；未公开 archive_search / archive_read。 |

`import_ovpack` 不归入普通 Agent 导入能力。它走 `POST /api/v1/pack/import`，用于导入 `.ovpack` 上下文包，更接近备份、迁移和恢复，不是聊天中“把这个文档 / skill 加入 OpenViking”的 resource / skill 导入。

## 本 PR 的实际收敛

- OV server `/mcp`：提供 `find` 和 `search` 两层显式搜索；`find` 是轻量检索，`search` 是 session-aware deep search。
- Codex MCP：当前暴露 `find`、`remember`、`forget`、`health`，全部不带 `viking_` 前缀。
- OpenClaw：当前暴露 `memory_search` 并同步 `openclaw.plugin.json`；`memory_recall` 继续作为召回语义保留，不和 search 混成一个通用工具；原 Agent 可见 `ov_import` 已拆成 `add_resource` / `add_skill`。
- 不引入新的 ToolCatalog / agent tools 抽象，不迁移 Claude Code、opencode、Hermes 的既有工具名和执行语义。

## 后续下沉原则

1. OpenViking 直接提供的通用 Agent / MCP 工具名不带 `viking_` 前缀。
2. Hermes 这类外部 provider 场景可以保留 `viking_*`，用于和宿主内其他 provider 显式区分。
3. 先看插件是否能无语义变化调用 OV 已有接口；能就直接收敛。
4. OV 没有接口，但多个插件都重复实现同一能力，再补一个具体 OV 通用接口。
5. 只有单个宿主需要，或依赖宿主 lifecycle、默认 scope、返回格式的能力，继续留在插件里。
