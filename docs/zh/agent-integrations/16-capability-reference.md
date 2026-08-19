# 集成能力参考

## 导读

| 你想知道 | 去哪里 |
|---|---|
| 特定 harness 下，agent 可主动调用的工具列表 | [§1.1](#_1-1-主动工具面-agentic-调用能力) 主动工具面 + [§2.1](#_2-1-服务端-mcp-工具面)（MCP 面）+ 各档案卡 |
| 不同关闭方式下，记忆归档的时机 | **[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) 关闭方式 × harness 终局矩阵** |
| 自动召回是否携带 session_id 及其影响 | [§3.2.2](#_3-2-2-判定矩阵) / [§3.2.3](#_3-2-3-profile-开场注入) |
| 如何开启召回再摘要，以及服务端与客户端的职责划分 | [§3.2.5](#_3-2-5-召回再摘要) |
| `forget` 操作与删除功能的类型边界 | [§3.5](#_3-5-写入与删除的类型边界) |
| 特定环境变量在不同 harness 下的生效情况 | [§3.1.4](#_3-1-4-配置体系分层) 配置体系分层 + 各档案卡"配置" |
| 服务端是否具备自动 commit 兜底机制 | [§2.3](#_2-3-服务端会话与-commit-语义) |
| ov CLI 命令全集 | [§5](#_5-ov-cli-命令参考) |
| 自定义 agent 如何接入 OpenViking | [§6](#_6-自定义-agent-接入指南) |
| 某个集成怎么安装、怎么配、怎么排障 | 该集成的单独页面（[§4](#_4-harness-档案卡) 各档案卡首行给出链接） |

---

# 1. 能力总览

## 1.1 主动工具面（Agentic 调用能力）

- **MCP 型 harness（claude-code、codex/trae-cli、cursor、trae/trae-cn、zcode、opencode）的主动工具面完全一致，共 15 个工具**：这些工具由服务端统一定义，插件通过代理获得 `~/.openviking/ovcli.conf` 配置后连接服务器定义的 MCP 工具。

- trae-cli 指 TraeCode CLI 2.0（仅支持 2.0），经 codex 插件别名安装，插件与 codex 格式兼容，下文矩阵并入 codex 行。

| harness | 工具面形态 | 工具数（默认开） | 搜 memory | 搜 resource | 搜 skill | 写 memory | 写 resource | 写 skill | 删除类型边界 |
|---|---|---|---|---|---|---|---|---|---|
| claude-code | MCP 透传 | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | 无类型区分² |
| codex / trae-cli | MCP 透传 | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | 无类型区分² |
| cursor | MCP 透传 | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | 无类型区分² |
| trae / trae-cn | MCP 透传 | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | 无类型区分² |
| zcode | MCP 透传 | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | 无类型区分² |
| opencode | MCP 透传（宿主加 `openviking_` 前缀） | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | 无类型区分² |
| dsh | 原生注册（7 个 `viking_*`） | 7（全开） | ✅ | ✅ | ✅ | ✅ `viking_remember` | ✅ `viking_add_resource`（仅 URL） | ❌ | 无类型区分；query 删除需 score>0.8³ |
| pi | 原生注册（7 个 `viking_*`） | 7（注册需前置检查⁴） | ✅ | ✅ | ✅ | ✅ `viking_remember` | ✅ `viking_add_resource`（仅 URL） | ❌ | 无类型区分；query 删除需 score>0.8³ |
| openclaw | 原生注册（15 个 `memory_*`/`ov_*` 等） | 15（默认开 14⁵） | ✅ `memory_recall` | ✅ `ov_search`（默认双 scope） | ✅ `ov_search` | ✅ `memory_store` | 默认关⁵ | ✅ `add_skill` | memory-only 白名单 + 单候选 score≥0.85 才自动删 |
| hermes | 原生注册（6 个 `viking_*`） | 6（provider 激活即全开） | ✅ | ✅ | ✅ | ✅ `viking_remember`（直写文件，不走抽取） | ✅ 多协议摄取（HTTP/Git/SSH/本地文件/目录 zip） | ❌ | memory-only + `.md` 叶子校验 |
| ov CLI | CLI 命令 | ~40 命令组 | ✅ `ov find` | ✅ `ov find` | ✅ `ov find` | ✅ `ov add-memory` | ✅ `ov add-resource` | ✅ `ov add-skill` | `ov rm` 直接执行（TUI 删除有确认 + root/scope 禁删） |

¹ MCP `write` 的可写域是 `viking://resources|user|agent`，暂不支持 MCP 新增 skill；新增 skill 的入口是 openclaw `add_skill`、`ov add-skill` 与 REST。
² MCP `forget` 不区分 memory/resource/skill 类型；存储层保留了命名空间根保护机制（裸 `viking://`、`viking://user`、`viking://agent` 根拒删），详见 [§3.5](#_3-5-写入与删除的类型边界)。
³ dsh/pi 的 `viking_forget`：其 `recursive` 参数固定为 false（不删除目录），按 query 删除要求匹配分 >0.8。
⁴ pi 工具注册前置：需未命中 bypassPatterns、`client.health()` 通过、`ensureSession` 成功（`index.ts:66-113`）；health 未通过时本轮不注册工具面。
⁵ openclaw 的 `add_resource` 需经过双重 opt-in 后方可开启。

**skill 的增删边界**：新增入口包含 openclaw `add_skill`（默认开）、`ov add-skill` 与 REST；删除边界分四档，详见 [§3.5](#_3-5-写入与删除的类型边界)。

## 1.2 自动 hook 面（通过 Harness 自动实现）

| harness | 接入方式 | 自动召回 | 召回带 session_id | 再摘要（客户端）* | profile 注入 | 接管宿主压缩 | 离线补偿（pending queue） | statusline |
|---|---|---|---|---|---|---|---|---|
| claude-code | 9 hook + MCP 代理 + slash + statusline + skill | ✅ | ✅ | ✅ 本地 `claude -p` / 服务端 rewrite（默认 auto） | ✅（10000） | ❌（PreCompact 只 commit） | ✅ | ✅ |
| codex / trae-cli | 4 hook + MCP 代理 + skill | ✅ | ✅ | ✅ 本地 `codex exec`（默认开） | ✅（10000） | ❌ | ❌ 无磁盘队列（靠游标不推进、下一轮重发补偿） | ❌ |
| cursor | 7 hook + MCP 代理 + rule + skill | ✅ | ✅ | ❌ | ✅（6000） | ❌ | ✅ | ❌ |
| trae / trae-cn | 4 hook + MCP 代理 | ✅ | ✅ | ❌ | ✅（6000） | ❌ | ✅ | ❌ |
| zcode | 4 hook + MCP 代理 | ✅ | ✅ | ❌ | ✅（6000） | ❌ | ✅ | ❌ |
| opencode | 7 plugin hook + MCP 代理 | ✅ | ✅ | ❌ | ✅（10000）+ repo 列表进 system prompt | ❌（compacting 前后各 commit 一次） | ✅ | ❌（有 toast） |
| dsh | Cordis 原生插件（同进程） | ✅ | ✅ | ❌ | ✅（10000，每 session 一次） | ❌ | ✅ | ❌ |
| pi | 原生扩展（8 事件） | ✅ | ✅ | ❌ | ✅（10000，进 systemPrompt 每轮重拼） | ✅ **takeover**（默认开） | ✅ | ✅ |
| openclaw | context-engine 插件（`ownsCompaction:true`） | ✅ | ❌（走 `/find`，该接口无 session_id 字段） | ❌ | ❌ | ✅ **ContextEngine 全接管** | ❌ 失败轮次不重放 | ❌ |
| hermes | MemoryProvider 原生插件 | ✅ | 部分（仅 `search/search` 首选路径；降级 `/find` 不带） | ❌ | ❌（静态工具指引块） | ❌ | ✅ 进程内队列（不落盘） | ❌ |
| ov CLI | 一次性命令 | ❌（`ov find/search` 是显式命令） | —（`ov search --session-id` 为显式参数） | ❌ | ❌ | ❌ | ❌ | ❌ |

\* 此列指客户端是否内建了针对召回结果的本地压缩；服务端则在 context 检索面上，统一为所有调用方提供 digest 能力（`rewrite` 参数，[§3.2.5](#_3-2-5-召回再摘要)）。

**session_id 携带现状**：除 openclaw（其调用的 `/find` 接口无该字段）与 hermes 的降级路径外，其余所有 harness 的自动召回均显式携带 session_id，并有跨插件回归测试钉死（`examples/memory-plugin-shared/recall-session-wiring.test.mjs:16-39`）。

## 1.3 形态分组

- **全家桶型**（hook 自动化 + MCP 工具面 + 周边 UX 齐全）：claude-code、codex（trae-cli 经别名安装同属此档）。
- **瘦 hook 型**（共享 agent-hook-runtime，核心行为基本一致，差异仅体现在宿主事件与阈值上）：cursor、trae/trae-cn、zcode。
- **plugin 事件型**：opencode（宿主事件面最丰富，dispose 覆盖关闭）。
- **同进程原生型**：dsh（Cordis）、pi（扩展 + takeover 压缩接管）、openclaw（context-engine 全接管）、hermes（MemoryProvider）。
- **工具型**：ov CLI（所有操作均为显式调用，不存在任何自动行为）。
- **非 coding**：Open WebUI（工具服务器）、LangChain（SDK 库）、Agent Plugins 便携包（MCP+skill 规范包）、通用 MCP 直连、log ingestion（日志反向导入）、Helper（桌面端）。

---

# 2. 公共能力核

per-harness 章节（档案卡）只写差异；所有共享事实均在本章一次性说明完毕。

## 2.1 服务端 MCP 工具面

这些工具定义在服务端，后续更新也会在服务端统一发布，Harness 只需通过插件代理 MCP 拿到最新的 `~/.openviking/ovcli.conf` 即可。

| # | 工具名 | 功能 | 参数要点（定义行号） |
|---|---|---|---|
| 1 | `find` | 不依赖会话上下文的快速语义检索 | `query, target_uri="", limit=10, min_score=0.35, level, context_type`（`:259`） |
| 2 | `search` | 深检索，可带 `session_id` + 意图分析 | `session_id` 仅在服务端 `retrieval.enable_intent`（默认 true）开启时才会加载会话（`:285`，`:302-304`） |
| 3 | `read` | 读取单个或多个 `viking://` 文件全文 | 并发信号量 10；单条失败返回 `(nothing found at <uri>)` 不抛错（`:389`） |
| 4 | `list` | 列目录（函数名 `ls`，注册名显式改写为 `list`） | `recursive=False`（`:423`） |
| 5 | `tree` | 递归目录树 | `level_limit=3, node_limit=1000, include_abstract=False`（`:449`） |
| 6 | `remember` | 写长期记忆 | 内部建一次性会话 `mcp-store-<uuid12>` 并立即 `commit_async`（`:504-523`）——这是 MCP 面唯一的 commit 入口；MCP 没有显式 commit 工具 |
| 7 | `write` | 写 `viking://` 文件 | `mode=replace\|append\|create`，replace 遇 NotFound 自动降级 create；新建文件扩展名白名单 `.md .txt .json .yaml .yml .toml .py .js .ts`；可写域 `resources/user/agent`；用户根下 `skills/ peers/ privacy/ sessions/` 只读；已存在的 `.abstract.md/.overview.md` sidecar 可改正文，但公共 API 不能创建（`:529`；`content_write.py:60-81`） |
| 8 | `edit` | 精确字符串替换 | `old_string` 空/0 命中/多命中且非 replace_all 均报错，且文件内容不变（`:569`） |
| 9 | `add_resource` | 资源摄取（远程 URL / 本地文件签名上传 / Connector） | `watch_interval` 单位为分钟（0=不 watch）；本地路径分支返回签名上传 URL（TTL 默认 600s），上传后自动入库，无需二次调用（`:723-947`） |
| 10 | `list_watches` | 列 watch 订阅，商业版尚未支持 | scheduler 未运行时返回错误串（`:958`） |
| 11 | `cancel_watch` | 按 `to_uri` 取消，商业版尚未支持 | 刻意不暴露 pause/resume/trigger/update（`:990`） |
| 12 | `grep` | 正则内容检索 | 多 pattern 并发（信号量 10），`node_limit=10`（`:1032`） |
| 13 | `glob` | 文件名 glob | `node_limit=100`（`:1084`） |
| 14 | `forget` | 删除 URI（不可恢复） | 默认 `recursive=False`；类型边界详见 [§3.5](#_3-5-写入与删除的类型边界)（`:1110-1117`） |
| 15 | `health` | 健康检查 | 无参（`:1123`） |

配套机制：

- **可移植 schema 重写**（`:1149-1218`）：模块导入时把所有工具的 `anyOf`/`$ref` 折成扁平类型，以兼容 Gemini 等 OpenAPI 3.0 子集客户端；运行时校验仍用原始 Python 签名（例如 `read` 广播的 schema 是 array，但仍接受裸字符串）。所有 MCP 客户端拿到的 schema 都由服务端统一产出，客户端无差异。
- **身份中间件**（`:149-233`）：与 REST 共用 `resolve_identity`，依次读 `x-api-key` / `authorization` / `x-openviking-account` / `x-openviking-user` / `x-openviking-actor-peer`；account/user 缺省回落 `"default"`。

## 2.2 memory-plugin-shared 共享层

`examples/memory-plugin-shared/lib/` 下共 18 个 `.mjs` 模块，是 JS 系 harness 的唯一事实源。两种消费形态：

1. **Vendoring（复制）**：由 `sync.mjs` 分发到 7 个目标，每个文件首行加 `// GENERATED FROM ... DO NOT EDIT.`（因此 vendored 副本行号 = lib 源行号 + 1，交叉读行号引用时要换算）。分发清单：claude-code / codex / opencode 各 16 个（HARNESS 13 + mcp-proxy-core + async-writer + batch-send）；dsh / pi 各 13；zcode 全量 18 个；agent-plugins 4 个。当前 HEAD 各目标与 lib 源零漂移。
2. **相对路径直接 import（不复制）**：cursor / trae / trae-cn 直接 `import "../../memory-plugin-shared/lib/..."`；安装器把包与共享 lib 一起复制到 `~/.openviking/agent-integrations/{<client>,memory-plugin-shared}/`，使相对层级成立。运行期这个共享目录被这几个 harness 共用，任一重装都会整体覆盖。

核心模块速览（细节在各维度章展开）：

| 模块 | 职责 | 消费方 |
|---|---|---|
| `recall-core.mjs` | 召回请求构造 + 三级降级 + 本地兜底排序注入 | 全部 JS 系 harness |
| `agent-hook-runtime.mjs` | "瘦 hook"一体化运行时（配置面 19 个 env、session id 派生、跨进程锁、fetch、commit） | cursor / trae / trae-cn / zcode |
| `mcp-proxy-core.mjs` | stdio↔streamable-HTTP MCP 代理内核 | 全部 MCP 型 + agent-plugins |
| `pending-queue.mjs` | 磁盘离线队列 + 会话启动重放 | cc / cursor / trae×2 / zcode / opencode / dsh / pi |
| `batch-send.mjs` | 100 条/批写入 + 404/405 逐条降级 + 连续前缀入队 | cc / codex / opencode + agent-hook 系 |
| `profile-inject.mjs` | session-start 的 profile + 可用记忆清单注入 | 9 个 harness（openclaw / hermes 除外） |
| `recall-compress-core.mjs` | 召回压缩 prompt + URI 编辑距离修复 + 缓存 | claude-code |
| `capture-utils.mjs` | 消息归一 + 注入回流防护 + 捕获过滤 | codex / opencode / dsh / pi |
| `credentials.mjs` | 凭据解析链（详见 [§3.1.3](#_3-1-3-凭据体系)） | 全部 JS 系 |
| `session-model.mjs` | 会话 id 前缀派生 + bypass glob | 全部 JS 系 |
| `async-writer.mjs` | 写路径 detach（drain stdin → spawn → approve → write → unref；spawn 失败回落同步） | cc / codex / zcode |
| `workspace-peer.mjs` | cwd → actor peer（非字母数字全替换成 `-`） | 全部 JS 系 |
| `uri-guard.mjs` / `agent-uri-guard.mjs` | 拦截 `viking://` 被误当成本地路径的情况 | 各 harness 的 PreToolUse/tool.execute.before 类 hook |
| `plugin-config.mjs` | ovcli.conf `plugin` 段读取 | claude-code / codex |
| `setup-wizard.mjs` | 交互式写 ovcli.conf | cc / codex / opencode / pi 暴露入口 |
| `retryable.mjs` | 可重试判定：status 0/408/429/≥500，或 409 且 `error.details.retryable===true`；4xx（含 401/403）不重试 | 全部 JS 系 |

## 2.3 服务端会话与 commit 语义

- **会话隐式创建**：插件普遍不显式调用 `POST /sessions`（dsh 例外，它会发一个只含 `session_id` 的 create）；首次 `POST /sessions/{id}/messages(/batch)` 时由服务端 `auto_create=True` 建会话。召回侧 `mode="context"` 的 `_load_session(auto_create=True)` 也会建（第一次召回即在服务端创建会话）。
- **commit 两阶段**：`POST /sessions/{id}/commit` 的 Phase 1（归档 archive）同步完成后才返回，Phase 2（记忆抽取）作为后台任务返回 `task_id`。`keep_recent_count` 服务端默认 **0**（全量归档，不留 live tail）。
- **服务端自动 commit 默认关闭，且插件建会话的路径不会打开它**：
  - `memory.session_auto_commit.default_enabled = false`、`idle_enabled = false`（`memory_config.py:15-16`）；无存储 policy 的会话自动 commit 关闭（`session_service.py:637-638`）；idle 扫描器在 `idle_enabled=false` 时根本不创建（`core.py:440-448`），构成双重门控。
  - `POST /messages` 的 auto_create 不接受 policy 参数；只有 `POST /sessions`（create）与 `PATCH /sessions/{id}/config` 能设 `auto_commit_policy`。
  - 当前没有插件下发 `auto_commit_policy`；第一方客户端中会下发该字段的是 **ov CLI**（`ov session new --auto-commit-policy-json` / `--no-auto-commit`、`ov session config set`）。
  - policy 显式启用时，服务端默认阈值是 `pending_token_threshold=150000（严格大于）/ message_count_threshold=100 / idle_timeout_seconds=86400 / keep_recent_count=0 / min_commit_interval_seconds=0`。注意这组服务端默认值与各插件客户端的 20000/10 是相互独立的两层配置。
- **由此**：现状下所有自动 commit 都是客户端各自实现的阈值逻辑（[§3.3](#_3-3-会话与-commit-生命周期)），服务端不做兜底；进程异常终止后遗留的 pending 消息，需在同一会话后续再触发 commit 才会归档与抽取。
- **tool output 外置**：服务端 `tool_output_externalization.enabled=True`、`threshold_chars=20000`（`server/config.py:257-258`）——客户端普遍把 `captureToolMaxChars` 设到 1000000 仅作兜底，真正的截断/外置在服务端做，externalized 结果通过 `tool_output_ref` 引用（openclaw 有三个专门工具读它）。
- **服务端召回相关熔断**：`retrieval.recall_intent_timeout_s=5.0`（query expansion）、`recall_rewrite_timeout_s=30.0`（digest，[§3.2.5](#_3-2-5-召回再摘要)）、`enable_intent=true`。客户端超时预算按这两条推导（[§3.2.4](#_3-2-4-超时与预算链)）。

---

# 3. 维度详解

## 3.1 接入形态、安装与配置体系

### 3.1.1 判定矩阵

| harness | 集成形态 | 安装通道 | 会话 id 前缀/格式 | 配置来源 | 独立 setup 向导 |
|---|---|---|---|---|---|
| claude-code | CC 插件（marketplace）：包含 9 hook + MCP 代理 + slash + statusline + skill | 一键 `install.sh --harness claude`（支持现代 plugin 路径与 legacy `claude mcp add` 兼容路径）/ 手动 marketplace / TOS 镜像 | `cc-<CC session_id 原文>`；subagent 格式为 `…__subagent-<agent_id>` | env + ovcli.conf `plugin.claude_code` + ov.conf `claude_code` | ✅ `scripts/setup.mjs` |
| codex | Codex 插件（marketplace）：包含 4 hook + MCP 代理 + skill | 一键 `--harness codex` / `codex plugin marketplace add`（TOS 走 dumb-HTTP git 以保留远程更新能力） | `cx-<safeId>`（确定性推导，不读取 state） | env + ovcli.conf `plugin.codex` + ov.conf `codex` | ✅ |
| trae-cli | **codex 插件别名安装**（TraeCode CLI 2.0，仅支持 2.0；Codex 系：binary `traecli`、配置 `~/.trae/traecli.toml`；能力面与 codex 一致） | 一键 `--harness trae-cli`（复用 codex 安装流程；marketplace 命令会随指向的 binary 执行，如 `traecli plugin marketplace add`） | 与 codex 的派生规则一致 | 与 codex 一致（env + ovcli.conf `plugin.codex` + ov.conf） | ✅（同 codex） |
| cursor | 配置驱动（写入 `~/.cursor/hooks.json`+`mcp.json`）+ rule + skill | 一键 `--harness cursor` | `cu-<conversation_id>` | 仅 env | ❌（共用安装器 TUI） |
| trae / trae-cn | 配置驱动（`~/.trae{,-cn}/hooks.json` + 平台相关 mcp.json） | 一键 `--harness trae,trae-cn` | `tr-` / `trcn-` | 仅 env | ❌ |
| zcode | 配置驱动（合并进 `~/.zcode/cli/config.json`，并强制 `hooks.enabled=true`） | 一键 `--harness zcode` | `zc-<sess_…>` | 仅 env | ❌ |
| opencode | npm 插件 `@openviking/opencode-plugin`（config hook 自注入 MCP 条目） | 一键 `--harness opencode`（npm 注册 + 代理快照兜底）/ 手动 npm / 源码 | `oc-<id>`；subagent 格式为 `oc-<parent>__subagent-<child>` | `openviking-config.json`（4 级搜索）+ env | ✅ |
| dsh | Cordis 同进程插件（`cordis.patch.yml` plugin group） | 执行 `dsh plugin add ./examples/dsh-memory-plugin`（统一安装器暂不覆盖 dsh） | `dsh-<session.id 原样>`；subagent 各自独立会话 | cordis patch config + 4 个 env（凭据优先级 patch>env，行为开关优先级 env>patch） | ❌ |
| pi | pi 原生扩展（目录装载，jiti 直译 TS） | 一键 `--harness pi`（含 `pi install` 注册） | `pi-<piSessionId>` | `config.json`（凭据字段由凭据链统一解析）+ env | ✅ |
| openclaw | context-engine 插件（`ownsCompaction:true`）+ 15 工具 + 5 slash + 4 hook + HTTP 路由 | ClawHub 执行 `openclaw plugins install clawhub:@openviking/openclaw-plugin` 搭配 `openclaw openviking setup` / npm 安装器 / TOS 离线包 | UUID 原样小写，否则 `sha256(sessionKey)`；`memory_store` 临时会话 `memory-store-<ts>-<rand>` | `openclaw.json` 的 `plugins.entries.openviking.config`（严格校验：存在未知键/非法值时插件进入 setup-only 模式）+ 少量 env | ✅ `openclaw openviking setup`（交互/非交互 + key 角色探测 + 版本兼容检查） |
| hermes | Hermes bundled MemoryProvider（随 Hermes 发布，无需装插件） | 执行 `hermes memory setup openviking`（curses 向导）或手动 `config set memory.provider openviking` + `.env` | 由 Hermes 生成 `%Y%m%d_%H%M%S_<hex6>`，插件原样使用 | `.env`（`OPENVIKING_*`）或 ovcli.conf 联动（`use_ovcli_config` 模式会清空 .env 里的 5 个对应变量）+ config.yaml | ✅（多层菜单） |
| ov CLI | Rust 原生二进制 | npm `@openviking/cli` / `uv tool install openviking` / cargo / GitHub Releases | 无自有会话（`ov chat` 默认使用 machine-uid） | `ovcli.conf`（多 profile）+ 少量 env | ✅ `ov config`（TUI 向导） |

### 3.1.2 统一安装器

统一安装脚本 `examples/memory-plugin-shared/install.sh`（3253 行）覆盖 9 个 harness id：`claude, codex, cursor, trae, trae-cn, trae-cli, zcode, opencode, pi`（其中 dsh 与 openclaw 走各自渠道；`trae-cli` 则复用 codex 安装流程，[§3.1.1](#_3-1-1-判定矩阵)）。要点如下：

- 双分发：`--dist github|tos`；三源：`--source remote|archive|dev`。以 `bash <(curl …)` 方式执行时会从 `/dev/tty` 读取输入，从而保留交互。
- 官方 docs 的规范一键命令是不带 `--harness` 的裸命令（执行后进入 TUI 多选）；而各插件自带的 setup-helper 转发脚本在调用时会自动补 `--harness`。
- 幂等合并：hooks/mcp 条目按 `OPENVIKING_INTEGRATION_ID` 标记识别自有条目，做到剔旧追新的同时不动第三方；写入采用原子操作——先备份 `.bak`，写 tmp 后 rename 覆盖，权限 0600。
- 凭据向导写 `~/.openviking/ovcli.conf`：三选一（本地 `http://127.0.0.1:1933` / 火山云 `https://api.vikingdb.cn-beijing.volces.com/openviking` / 自定义），已有配置先展示当前值再问"沿用/重配"，API key 掩码。
- 卸载：`--uninstall` 覆盖 cursor / trae / trae-cn / zcode，并顺带清理 trae-cli 遗留的旧 hook 配置；claude / codex / trae-cli / opencode / pi 这些 Codex 格式或宿主托管的插件，通过各自宿主的插件管理卸载。
- 安装后自检：grep 配置 + `node --check` + 一次 `OPENVIKING_MEMORY_ENABLED=0` 的 smoke run。
- Node 门槛：安装器检查 18+。

### 3.1.3 凭据体系

代码中并存着四套并行的凭据解析体系，env 变量名与认证头各不相同，排障时先分清对象：

| 家族 | 消费者 | URL env | Key env | 身份 env | 认证头 |
|---|---|---|---|---|---|
| **A. JS 共享核**（`credentials.mjs`） | claude-code / codex（含 trae-cli）/ cursor / trae×2 / zcode / opencode / pi / dsh / agent-plugins | `OPENVIKING_URL` → `OPENVIKING_BASE_URL` | `OPENVIKING_BEARER_TOKEN` → `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID` | `Authorization: Bearer`；注意 codex 的 4 个 hook 脚本会额外发送 `X-API-Key` 兼容头 |
| **B. openclaw**（自有 `config.ts`） | openclaw | `OPENVIKING_BASE_URL` → `OPENVIKING_URL` | `OPENVIKING_API_KEY`（支持 SecretRef env/file） | `OPENVIKING_ACCOUNT_ID` / `OPENVIKING_USER_ID`（注意此处带 `_ID`） | `X-API-Key`（指向 OV Cloud 时注意其实际采用 Bearer 认证） |
| **C. hermes**（Python） | hermes | `OPENVIKING_ENDPOINT` | `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT`（=actor peer） | `X-API-Key` + `Bearer` 双发；有 key 时默认不发租户头（被服务端以 trusted 报错拒绝时会自动补头重试一次） |
| **D. ov CLI**（Rust） | ov | conf 文件为主 | conf | `--account/--user/--actor-peer-id` | `X-API-Key`；LDAP Basic / OIDC Bearer 按 `auth_mode` 切换；api_key 含 ≥2 个 `.` 时自动附加 Bearer（JWT 兜底） |

家族 A 的解析链如下（其余家族见档案卡）：

1. 模式由 `OPENVIKING_CREDENTIAL_SOURCE`（别名 `_CREDENTIALS_SOURCE`）控制，取值 ∈ `env|cli|auto`（默认 auto）。
2. **auto 语义是 env 优先**：只要任一 env 凭据字段存在，整体就走 env；只有 env 全空、且 ovcli.conf 存在并含凭据字段时，才整体切换到文件（此时 key/account/user/peer 全部只从文件取）。
3. baseUrl：env → ovcli `url` → ov.conf `server.url` → `http://{server.host|127.0.0.1}:{server.port|1933}`（其中 `0.0.0.0` 归一为 `127.0.0.1`）；兜底 `http://127.0.0.1:1933`。
4. apiKey：`BEARER_TOKEN` → `API_KEY` → ovcli `api_key` → ov.conf `codex.apiKey` → `server.root_api_key`。
5. mcpUrl：`OPENVIKING_MCP_URL`（非 cli 模式）→ `${baseUrl}/mcp`。
6. 统一请求头：`Authorization: Bearer` + `X-OpenViking-Account/User/Actor-Peer` + `User-Agent: openviking-memory-<harness>/<version>`。

**workspace peer**（家族 A 全体 + agent-plugins）：无显式 peerId 且 `OPENVIKING_WORKSPACE_PEER≠0` 时用 cwd 派生——把路径中所有非字母数字字符替换成 `-`（`/Users/x/Dev/OpenViking` → `-Users-x-Dev-OpenViking`），该值随 `X-OpenViking-Actor-Peer` 发送；服务端会对该头校验，含 `/` 或 `\` 返回 400。openclaw 的 peer 由 `peer_role`/`peer_prefix` 推导（`peer_role=person` 时需保证 sender 信息可用，否则工具调用报错）；hermes 的 peer 就是 `OPENVIKING_AGENT`（默认 `hermes`）。

### 3.1.4 配置体系分层

| 配置层 | 生效范围 | 备注 |
|---|---|---|
| env `OPENVIKING_*` | 各家族见上；行为旋钮见各档案卡 | 唯一横跨所有 JS 系的层 |
| ovcli.conf `plugin` 段（`plugin.claude_code` / `plugin.codex` / 共享标量） | claude-code / codex | 其余 harness 名下的 `plugin.<x>` 条目不参与解析。注意：`ov config add/edit` 会以 Rust Config 结构重写整个文件，从而丢弃其不识别的 `plugin` 段；而 `ov config switch` 为字节复制，不受影响 |
| ov.conf harness 段（`claude_code.*` / `codex.*`） | claude-code / codex（legacy 回落） | |
| harness 自有配置文件 | opencode `openviking-config.json`、pi `config.json`、dsh cordis patch、openclaw `openclaw.json`、hermes `config.yaml`+`.env` | |

**配置项生效范围速查**（这些旋钮只在列出的 harness 上生效）：

- `OPENVIKING_COMMIT_TURN_THRESHOLD`：仅 cursor（trae×2/zcode 每 Stop 必 commit，不走该阈值）。
- `OPENVIKING_WRITE_PATH_ASYNC`：claude-code / codex / zcode。
- 召回再摘要相关（`OPENVIKING_RECALL_COMPRESS` / `OPENVIKING_RECALL_REWRITE` 及配套项）：claude-code / codex（服务端 `rewrite` 参数本身对所有调用方可用，[§3.2.5](#_3-2-5-召回再摘要)）。
- `OPENVIKING_RECALL_DEDUP_TURNS`、`OPENVIKING_RECALL_QUERY_EXPANSION`：claude-code / codex。
- ovcli.conf `plugin` 段：claude-code / codex。

## 3.2 自动召回与注入

### 3.2.1 机制底座：一条共享管线，两条服务端路径

JS 系 harness 的召回逻辑均由 `recall-core.mjs` 中的三级降级链处理：

1. **context face**：调用 `POST /api/v1/search/search`，参数设定为 `mode:"context"` 且 `purpose:"coding"`。其核心设计原则为"只声明意图，机制交服务端"：对于 `quotas`/`max_tokens`/`query_expansion`/`rewrite_max_bullets` 等参数，仅在用户显式配置时（带有 configured 哨兵字段）才会发送，否则直接采用服务端的默认设定。
2. **legacy `/recall`**：若 context face 请求返回 400/422 错误，且响应报文包含 `extra`/`mode`/`unexpected` 等特征字段，则判定对接了旧版服务端。此时会在本地写入 6 小时的负缓存（路径为 `~/.openviking/state/context-face.json`，该文件全机共享，一旦被任一 harness 标记，同机所有 JS 系 harness 均会跳过 context face 阶段）。随后降级调用已弃用的 `/api/v1/search/recall` 接口；若 `peer_scope` 被拒，则去掉该参数重试一次。
3. **raw find 兜底**：并发请求 `viking://user/memories` 与 `viking://user/skills`，调用两次 `POST /search/find`（注意：resources 被刻意排除在自动召回之外，资源类文档由模型主动调用 `search` 获取）。客户端收到结果后进行本地重排（权重规则为：leaf +0.12 / 时间意图 +0.10 / 偏好意图 +0.08 / 词面重叠 ≤0.2）、去重，最后按客户端 token 预算装填。`recallTokenBudget`、`recallMaxContentChars` 与 `recallPreferAbstract` 三个旋钮只在这一级生效；而在 context face 下，注入预算由服务端 `max_tokens`（默认 1600）决定。

服务端在处理 session_id 时，分为两条截然不同的执行路径：

- **路径 A：`mode="context"`**（适用于 context face 与 `/recall` preset）。此路径负责 query expansion 与跨轮去重台账。expansion 设有三重闸门：`retrieval.enable_intent` 需开启（默认 true） → 会话必须已物化（即 `messages.jsonl` 文件存在） → `latest_archive_overview` 或 `current_messages` 不能为空。扩写后原 query 永远排第一，追加的 planned queries 上限为 3。台账（`.recall_log.json`）按 `dedup_turns` 冷却已发正文的 URI；若"当轮只发了 URI 没发正文"，该记录则不参与冷却；digest 判定 no_relevant 时亦不记账。
- **路径 B：`mode="list"`**（不写 mode 时的默认行为）。此路径下，IntentAnalyzer 会整体替换 typed_queries（原 query 不保证保留），无台账、无原-query 保底。实际落在这条路径上的调用方包括：codex 的第二级降级 `searchScope`、hermes 的 `viking_search(mode="deep")` 以及 prefetch 的首选路径。尽管它们带了 session_id，但拿不到 context 面的 expansion 与去重机制。

**`dedup_turns` 的三点说明**：① 服务端 context 面的默认值是 **0**，常见的"5"实则源自客户端 `recall-core.mjs` 兜底与 `/recall` preset（后者仅当带 session_id）——不经共享库直接打 API 的第三方即使带了 session_id，也要显式发 `dedup_turns` 才有跨轮去重；② "turn"的计数单位是消息条数而非对话轮（`_resolve_turn` 用 `total_message_count`），对同时推 user+assistant 的 harness，默认 5 ≈ 1-2 个真实对话轮；③ 注意：`autoCapture=0` 且 `autoRecall=1` 时消息数恒 0 → 台账时钟不走 → 已发过正文的 URI 在本会话内持续冷却；可用 `OPENVIKING_RECALL_DEDUP_TURNS=0` 关闭去重（claude-code / codex）。

### 3.2.2 判定矩阵

| harness | 触发点 | query 构造 | session_id | 服务端路径 | 注入格式 / 位置 | 再摘要（客户端）* |
|---|---|---|---|---|---|---|
| claude-code | 每轮 `UserPromptSubmit` | prompt 原文 trim | ✅ `cc-` | A（context face） | `<openviking-context>` → `hookSpecificOutput.additionalContext` | ✅ 本地/服务端（默认 auto，[§3.2.5](#_3-2-5-召回再摘要)） |
| codex / trae-cli | 每轮 `UserPromptSubmit`（整 hook 120s 硬截止） | prompt 原文 | ✅ `cx-`（确定性推导，不读 state） | A；二级降级 searchScope 落入 B | `<openviking-context source="auto-recall" format="digest">` | ✅ 本地 `codex exec`（[§3.2.5](#_3-2-5-召回再摘要)） |
| cursor | `beforeSubmitPrompt` | prompt 原文；基于事件 id 与 500ms 窗口去重，同 promptHash 复用缓存块 | ✅ `cu-` | A | `additional_context` | ❌ |
| trae / trae-cn | `UserPromptSubmit` | 剥离历史注入块后的 prompt（只认 `input.prompt`） | ✅ `tr-`/`trcn-` | A | `additionalContext` | ❌ |
| zcode | `UserPromptSubmit` | 剥离三类注入块（含 `<system-reminder>`） | ✅ `zc-` | A | `additionalContext`（严格 JSON） | ❌ |
| opencode | 每条 `chat.message` 中的 user 消息 | 拼接非 synthetic text part；若正文已含 `<openviking-context` 则跳过本轮召回 | ✅ `oc-` | A（timeoutMs=30000） | 合成 synthetic part 并 `unshift` 到 parts 最前 | ❌ |
| dsh | `agent/pre-step` waterfall（先 await next 再 append） | claimed batch 全部消息（过滤自身注入的内容） | ✅ `dsh-` | A | 借由 `createUserMessage` append 到 `decision.messages` 尾部（source: plugin/openviking-memory） | ❌ |
| pi | `before_agent_start` 阶段排队；在 `context` 事件内检索（当前轮 prompt 拿当前轮记忆） | prompt 原文 | ✅ `pi-`（会话未建立时不带） | A | 前置到最后一条真实 user 消息（通过 `<openviking-context` 幂等检测） | ❌ |
| openclaw | context-engine transformContext assemble（设有 7 道 passthrough 门） | 最后一条 user 消息纯 text，清洗后截 4000 字符 | ❌（`/find` 无该字段） | `/find` | 以 `<relevant-memories>` + `Source: openviking-auto-recall` 格式前置进最后一条 user 消息 | ❌ |
| hermes | 每轮 API 调用前同步执行 `prefetch` | 原始用户输入，双层剥 skill 脚手架；<5 字符跳过 | 部分携带（仅 `search/search` 首选路径，落 B；降级 `/find` 时不带） | B / find | `<memory-context>` fenced 块追加到当轮 user 消息（只进 API 请求体，不写回持久化） | ❌ |

\* 同 [§1.2](#_1-2-自动-hook-面-通过-harness-自动实现)：此列的"再摘要"特指客户端本地压缩，而服务端 digest 对所有调用方均可用（[§3.2.5](#_3-2-5-召回再摘要)）。ov CLI 无自动召回，不在本表。

### 3.2.3 profile / 开场注入

- **实现**：`profile-inject.mjs`——读 `viking://user/<space>/memories/profile.md` 全文 + `preferences/`、`entities/` 递归清单（abs_limit=512）。预算估算引入 CJK 感知（≥U+3000 记 1.5 token/字，其余 chars/4）；profile 占一半预算，超限则采用"头 8 行 + 尾部"的中段省略；清单超限则追加 `... +N more` 提示。
- **谁注入、何时、预算**：claude-code（SessionStart 全部 source，10000）；codex（SessionStart startup/clear/resume，10000）；cursor/trae×2/zcode（SessionStart，6000，2s 去抖）；opencode（每会话首条 chat.message 一次，10000，进程内 Set 去重，subagent 会话跳过；注意开场注入每会话只尝试一次，失败后本进程内不重试）；dsh（每 session 投一次 `profileDelivered`，10000；compaction 之后不重投）；pi（进 systemPrompt，每个 prompt 重拼，10000 常驻）。openclaw、hermes 无 profile 注入。
- **archive 注入**（resume 场景把上次归档摘要拉回来）：claude-code（source=resume/compact，`token_budget=32000`，≤5 条 pre_archive_abstracts）；codex（resume 且本地 ovSessionId 已清时，32000/截 6000 字符）；opencode（开场注入 B 部分，32000）；pi 非 takeover 模式（32000）。
- **repo 上下文注入**：opencode 独有——通过 `experimental.chat.system.transform` 把已索引仓库列表放进 system prompt。

### 3.2.4 超时与预算链

- 家族 A 客户端推导：带 rewrite → `max(timeoutMs, 45000)`；带 expansion → `max(timeoutMs, 15000)`；对应服务端熔断 5s（expansion）/30s（rewrite）——设计上让客户端预算覆盖服务端各阶段，防止客户端提前 abort 丢掉整个响应。
- 实际值：cc 15s（hook 预算 60s）；codex 召回整 hook 120s 硬截止 + 压缩子进程 110s；cursor/trae×2/zcode 15s（宿主 hook 预算 20s）；opencode 30s；dsh 15s（阻塞 pre-step）；pi 15s；openclaw 整个召回流程外层 5s 硬超时（500ms health precheck；默认 `recallPreferAbstract=false` 时每条 leaf 记忆多一次 read，预算内最多 1 find + 6 read + 1 health）；hermes 总预算 4s / 单请求 3s（可配）。
- 注入体预算：服务端 `max_tokens` 默认 1600（家族 A 默认不发、由服务端决定）；openclaw / hermes 用字符预算 4000（两家都是"装不下整条跳过"而非截断）。

### 3.2.5 召回再摘要

**服务端实现（对所有调用方可用）**：context 检索面（涵盖 REST 的 `mode="context"` 与 legacy 的 `/recall`）支持传入 `rewrite` 参数——可选值为 `false | true | "auto"`，默认为 `false`；并配套提供 `rewrite_max_bullets`（默认 6，1-20）。开启后，服务端会用 `query_planner` 模型（`rewrite=true` 时未配置则回落主 `vlm`；`"auto"` 仅在显式配置了 `query_planner` 时生效）把召回结果改写成带引用的 digest：`OpenViking memory digest:` 头 + `- ` bullets，每条 ≤500 字符且必须引用一条本次命中的 `viking://` URI（无引用或引用越界的 bullet 被丢弃）；判定无相关记忆时输出哨兵并清空注入块（该轮不记入去重台账）。模型调用受 `retrieval.recall_rewrite_timeout_s=30s` 熔断，超时回落未改写的 rendered 块（`rewrite.py:78-141`、`pipeline.py:122-130`、`search.py:195-196`）。

**客户端侧现状**：

- **claude-code**：`recallRewrite` 四态 `off|client|server|auto`，默认 **auto**——先探测本地压缩器是否可用（`claude --version` 探测，缓存 7 天），若可用则在本地起 `claude -p --model sonnet --effort low --strict-mcp-config` 子进程压缩（30s 超时，输入 <1500 字不压缩，digest 单条缓存；子进程环境强制降级防递归；失败回落未压缩块；URI 编辑距离吸附回真实 URI，修不回的整条丢弃）。若本地不可用，则下发 `rewrite:"auto"` 交服务端。这是唯一接入服务端 rewrite 的 harness。
- **codex**：布尔 `recallCompress` 默认 **true**，完全依赖本地压缩（不使用服务端 rewrite）：模型 profile 从 `~/.codex/models_cache.json` 读，候选 `gpt-5.3-codex-spark` → `gpt-5.6-luna`，缓存 7 天；命令 `codex --sandbox read-only --ask-for-approval never exec --ephemeral --ignore-user-config --skip-git-repo-check --output-last-message <tmp> -`，超时 110s；运行期失败后同一会话内跳过压缩、下次 SessionStart 自愈重测；输出规范化截 4000 字符；压缩关闭或失败时用确定性 `fallbackDigest` 兜底。
- **其余 harness**：均不发送 `rewrite`、无本地压缩，而是直接注入服务端返回的原始召回块。直连 API 的第三方可自行传 `rewrite` 获得服务端 digest。

### 3.2.6 注入回流防护

为防止注入内容被二次捕获，注入时会加确定性包装（`<openviking-context>` 等），捕获时再机械剥离：capture-utils 的 `sanitizeCapturedText` 剥注入块、digest 块、元数据围栏与时间戳前缀。各端的特殊处理包括：trae/zcode 用各自的 clean 函数（其中 zcode 剥三类注入块）；openclaw 在 afterTurn 写回与下轮 query 构造时各剥一次 `<relevant-memories>`；hermes 则更彻底，直接把三个召回类工具的 tool_call/result 从 sync batch 里整条剔除（写类工具保留）。

## 3.3 会话与 commit 生命周期

### 3.3.1 机制底座

- **写入路径**：JS 系统一通过 `batch-send.mjs` 处理（对应接口 `POST /messages/batch`，每批最多 100 条，与服务端的 `max_length=100` 限制保持一致；若遇 404/405 错误则降级为逐条发送）。增量游标由各家自行实现（cc 按 transcript turn 序号计算，codex 机制相同；cursor 采用 `sha256(index+role+content)`；zcode 基于 rollout 的 `turn_id`；opencode 依赖事件流 Map；dsh 基于事件白名单；pi 依据 branch 条目水位；hermes 则按当前轮切片）。
- **commit 是客户端触发的**（详见 [§2.3](#_2-3-服务端会话与-commit-语义)）：服务端默认不自动 commit。下表所有的"阈值/触发"条件，均指客户端逻辑。
- **keep_recent_count 差异**（即 commit 后给宿主留多少 live tail）：服务端默认值为 0。各家传参如下：cc/codex 阈值提交传 10；cursor、trae×2、zcode 发空 body `{}`，即传 0（每次均为全量归档）；opencode 传 10；dsh 传 10；pi 在非 takeover 模式传 10，takeover 模式传 3（本地含义是"保留 3 个用户轮"，而服务端按消息条数解释，实际保留更少）；openclaw 在 afterTurn 阈值触发时传 10，在 compact/reset/memory_store 操作时传 0；hermes 恒定传 0。
- **写路径 detach**（`async-writer.mjs`，cc/codex/zcode 的 Stop 默认开）：drain stdin → spawn detached worker → approve → write payload → unref（注：spawn 失败时尚未 approve，回落同步恰好只输出一次）。detached worker 自成进程组，不受终端信号波及——这是保证 cc 在关闭链路时具有高可靠性、zcode 在按下 Ctrl+C 时不丢失写入数据的关键机制。附带效果：执行 detach 后，Stop 的 `appended N turn(s)` 提示将不再展示（设置 `OPENVIKING_WRITE_PATH_ASYNC=0` 可恢复该提示）。

### 3.3.2 常规 commit 触发条件

| harness | 轮内阈值触发 | 显式/边界触发 | 压缩触发 |
|---|---|---|---|
| claude-code | Stop：`pending_tokens ≥ 20000`（读服务端值），keep 10 | SessionEnd：无条件触发；SubagentStop：无条件触发（无阈值）；SessionStart：重放 pending | PreCompact：无条件触发（同步执行，不 detach） |
| codex / trae-cli | Stop：同上，20000 / keep 10 | SessionStart(startup\|clear)：基于 active-window 的启发式（2min 内恰好 1 个 state → commit；≥2 个则跳过） + idle-TTL 扫描（>30min 一律 commit 并清理） | PreCompact：全量 commit（发空 body `{}`），随后置 `ovSessionId=null` |
| cursor | stop：`capturedSinceCommit ≥ 8`（按消息条数计算，≈4 轮问答；纯客户端计数），keep 0 | sessionEnd：已注册该事件（但实践中不触达，见 [§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)） | preCompact：无条件触发 |
| trae / trae-cn | 每个有内容的 Stop 都 commit（无阈值），keep 0 | — | 无（上游无 PreCompact 事件） |
| zcode | 同 trae（每 Stop 都 commit，keep 0；rollout 增量游标保守推进，若有漏掉的轮次，将在同会话的下个 Stop 补齐） | — | 无（上游无 PreCompact 事件） |
| opencode | `session.idle` 路径：flush 执行后，需满足 `pending_tokens ≥ 20000` 才 commit，keep 10 | `session.deleted` / `session.error`：强制 commit；dispose：强制 commit | 在 `experimental.session.compacting` 前与 `session.compacted` 后各触发一次（即一次宿主压缩 = 两次 commit） |
| dsh | `turn/end`：`pending_tokens ≥ 20000`（30s 超时），keep 10 | teardown（见 [§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)） | 无（不监听 compaction 事件） |
| pi（takeover 默认开） | `onTurnSynced`：本地估算 `pendingTokens ≥ 30000` 且 `lastSeenUserTurns > 3` 时，执行 commitAndAdvance（keep 3；overview 15×2s 轮询，拿不到则边界不推进，但 pendingTokens 会清零，重新累计后重试） | 手动执行 `/viking commit` | `session_before_compact`（需 `firstKeptEntryId` 非空） |
| pi（takeover off） | syncBranch 执行后：服务端 `pending_tokens ≥ 20000`，keep 10 | `session_shutdown`：无条件 commit；手动执行 `/viking commit` | `session_before_compact`：无条件 commit |
| openclaw | afterTurn：`pending_tokens ≥ floor(tokenBudget × 0.5)`（ratio 默认 0.5，tokenBudget 缺省 128000，即阈值 ~64000），wait=false，keep 10 | `before_reset`（执行 `/new` `/reset`）：wait=true，keep 0；`memory_store` 工具：wait=true，keep 0 | `compact()`：wait=true，keep 0（Phase2 轮询上限 5 分钟） |
| hermes | 无阈值 commit——触发面全是会话边界：`on_session_end`（drain 10s，drain 不净则本次不 commit）、`on_session_switch`（涉及 `/new`、`/resume`、`/branch` 或压缩 fork，异步 drain 预算 65s）、gateway 缓存驱逐；`/undo` 与原地压缩不 commit。用幂等集合防二次 commit；keep 0 | atexit 兜底 | fork 型压缩边界 commit；原地压缩不 commit |
| ov CLI | 无 | `ov session commit`；`ov add-memory` 第三步固定 commit | — |
| ingest | `pending ≥ 6000` 或 idle 5s，keep 0；backfill 在每个会话结束时执行 `commit_if_needed` | 退出时执行 `_flush_all()` | — |
| LangChain | `CommitPolicy.mode` 默认 `never`；`pending_tokens` 模式阈值为 8000；`always` 模式每次 record 均触发 | 调用方自理 | — |

### 3.3.3 关闭方式 × harness 终局矩阵

图例：**C** = 会 commit；**C\*** = 会 commit 但有前提（见注）；**—** = 不 commit（已 POST 的消息仍留在服务端 live 区：不丢消息本体，等待后续触发归档与抽取）；**n/a** = 无此形态。所有行的服务端侧行为一律为 **—**（[§2.3](#_2-3-服务端会话与-commit-语义)）。

| harness | 正常退出 | Ctrl+C | SIGTERM | SIGHUP/关终端/关窗口·tab | kill -9/崩溃 | 补救路径 |
|---|---|---|---|---|---|---|
| claude-code | **C**（SessionEnd → detach 子进程 commit，用户不等待） | **C** | **C** | **C**（detached worker 自成进程组，不受 SIGHUP 波及） | **—** | 下次 Stop 越阈值 / `/compact` / 下次 SessionEnd |
| codex / trae-cli | **—**（上游不提供 SessionEnd 类事件） | **—** | **—** | **—** | **—** | 下次 `SessionStart(startup\|clear)` 的启发式（2min 内恰好 1 个活跃 → commit）或 30min idle-TTL 扫描；≥2 并发会话时启发式让位于 TTL 扫描 |
| cursor | **—**（chat 关闭 / new-chat 无事件） | **—** | **—** | **—**（`sessionEnd` 已注册且仅 window_close 触发，但此时宿主已销毁 shell-exec host，hook 在 spawn 前中止） | **—** | 结束在 <8 条消息水位的会话，尾部依赖同一会话的后续消息触发 commit |
| trae / trae-cn | **—**（无 session-end 类事件） | **—** | **—** | **—** | **—** | 每 Stop 已 commit，最大待归档量 = 最后一轮 in-flight |
| zcode | **—**（无 session-end 类事件） | **C\*** | **—** | **—** | **—** | C\* 前提：Ctrl+C 时该轮 Stop 已触发（detached worker 照常写完）；每 Stop 已 commit，漏掉的轮次靠 rollout 游标在同一会话的下个 Stop 补回 |
| opencode | **C\***（≥1.15.11 的 `dispose` → `flushAll({commit:true})`，覆盖全部四种关闭；<1.15.11 无该 hook → —） | **C\*** | **C\*** | **C\*** | **—** | C\* 前提：宿主 shutdown 预算 5s，单会话最坏 health 5s + batch 10s + commit 30s，多会话串行累加——超预算的 commit 会被截断，且 pending queue 不覆盖此场景（fetch 未 settle 不入队）；重启后 `init()` 不主动 flush 遗留会话 |
| dsh | **C**（Cordis teardown → 每 session 一次 3s 超时 commit，无阈值） | **C**（第一次；第二次 Ctrl+C 强退 → —） | **C** | **—**（无 SIGHUP 监听） | **—** | teardown commit 与阈值 commit 共享同一条串行写链，5s 进程 grace 内前方有慢请求时可能挤不进；web 形态关浏览器 tab 不触发 teardown |
| pi（takeover 默认） | **—**（`session_shutdown` 全关闭方式都触发且被 await，handler 持久化本地 takeover 状态，不 commit） | **—** | **—** | **—** | **—** | 下次续跑攒满 30000，或手动 `/viking commit` |
| pi（takeover off） | **C**（`await sync.commit()`，失败入 pending 队列） | **C** | **C** | **C** | **—** | — |
| openclaw | **—**（上游发 awaited `session_end(reason=shutdown\|restart)`，handler 缓存 agentId 后返回，不触发 commit；`gateway_stop` 未注册；无信号处理） | **—** | **—** | **—** | **—** | 显式 `/new` `/reset` 与 ~50% 阈值；低于阈值的会话依赖这两条路径归档 |
| hermes | **C**（atexit `_run_cleanup` → flush 10s → on_session_end） | **C**（交互与非交互最终都走 atexit） | **C**（`_signal_handler`，grace 默认 1.5s → 干净退出 → atexit） | **C**（SIGHUP 同 SIGTERM 路径） | **—**（atexit 不执行） | drain 不净时本次不 commit（避免提交半截数据）；退出看门狗 30s 终止慢 commit |
| ov CLI | n/a（一次性命令） | n/a | n/a | n/a | n/a | 无 pending queue；命令失败重跑即可 |
| ingest | **C**（`finally _flush_all`） | **C**（SIGINT → stop） | **C** | **—**（无 SIGHUP handler） | **—** | 唯一有崩溃恢复的写路径：`needs_commit` 持久化在游标库，下次运行 reconcile 补 commit |
| LangChain / Open WebUI / Agent Plugins / 通用 MCP | **—**（无会话生命周期挂钩；MCP 代理退出时的 `DELETE /mcp` 只释放协议会话，与记忆 commit 无关） | — | — | — | — | LangChain 靠调用方 `close()`；进程内 pending-commit 集合随进程消失 |

**三点阅读提示**：

1. 正常退出即 commit 的有五家：claude-code、opencode(≥1.15.11)、dsh、pi(takeover off)、hermes；其余各家均依赖表中"补救路径"列的回收机制。
2. kill -9 场景所有集成都不触发 commit——已提交的消息保留在服务端 live 区，后续同一会话再触发 commit 时一并归档；服务端提供 per-session idle 兜底能力（[§2.3](#_2-3-服务端会话与-commit-语义)），当前插件默认不下发该 policy。
3. 每轮必 commit 的 trae×2 / zcode 关闭语义最简单（即最大待归档量 = 最后一个未走到 Stop 的回合），代价是每个 Stop 都触发一次全量归档 + 记忆抽取（keep 0）。

### 3.3.4 pending queue / 离线补偿对照

| harness | 机制 | 要点 |
|---|---|---|
| cc / cursor / trae×2 / zcode / opencode / dsh / pi | 磁盘队列 `~/.openviking/pending`（0700/0600） | 仅可重试的失败入队（4xx 含 401/403 判为不可重试，不入队，debug 日志可见）；重放在会话启动时执行：≤50 条/次、≤3 次/条、TTL 7 天；`.processing` 原子认领，10min 陈旧回收；addMessage 失败即 break 保序 |
| codex / trae-cli | 无磁盘队列 | 服务端不可达时，靠 `capturedTurnCount` 游标不推进、下一轮 Stop 重发同批实现补偿（进程存活且有下一轮时生效） |
| openclaw | 无本地队列 | addSessionMessage 失败被 catch，该轮消息不重放 |
| hermes | 进程内 daemon 线程队列 | drain 有预算（10s/65s）；不落盘 |
| LangChain | 进程内 `_pending_commit_sessions` 集合 | commit 失败下次 record 自动重试；不落盘。部分成功时抛 `OpenVikingPartialWriteError`（携带 `messages_written`、`input_messages_consumed`、`context_attached`，调用方可按位置切片重试后缀）——全部集成里唯一的部分成功上报协议 |
| ingest | SQLite 游标库 + 单实例锁 | append 前持久化意图，崩溃后 reconcile 按服务端消息数判定该批是否落地——唯一有崩溃恢复语义的写路径 |

### 3.3.5 subagent 会话对照

| harness | 处理方式 |
|---|---|
| claude-code | 隔离最完整：SubagentStart 派生 `cc-<sid>__subagent-<agent_id>` 独立会话，SubagentStop 读 subagent transcript 推送后无条件 commit 并清 state |
| codex / trae-cli | 不单独建会话：subagent 输出（`agent_message` / `sub_agent_activity`）折叠进主会话的 assistant/tool part |
| opencode | `oc-<parent>__subagent-<child>` 挂在父命名空间下；开场注入跳过 subagent（召回不跳过）；ID 派生依赖事件顺序——`chat.message` 先于 `session.created` 到达时会丢 `__subagent-` 后缀 |
| dsh | 每个 subagent = 独立 `dsh-<id>` 会话，父子关系不保留；N 个 subagent = N 份 profile 注入 + N 个独立会话 |
| hermes | `delegate_task` 传 `skip_memory=True` → subagent 不接 OV（无会话/召回/工具面）；子任务产出不回灌 |
| cursor / trae×2 / zcode / pi / openclaw | 无 subagent 处理（有独立会话 id 就各自成会话，否则混入主会话；openclaw 可用 `bypassSessionPatterns` 屏蔽） |
| ingest | claude_code 适配器跳过 `isSidechain` / `isMeta` 记录——subagent 对话不入库 |

## 3.4 压缩 / compaction 接管

### 3.4.1 判定矩阵

| harness | 对宿主压缩的姿态 | 压缩前动作 | 压缩后动作 |
|---|---|---|---|
| claude-code | 不接管 | PreCompact 同步 commit（唯一不 detach 的写路径，因为 CC 随后立刻重写 transcript） | `source="compact"` 的 SessionStart 会把 OV 的 `latest_archive_overview` + ≤5 条 abstracts 重新注回 |
| codex / trae-cli | 不接管 | PreCompact 补齐未捕获轮次 → 全量 commit → `ovSessionId=null`（补齐不全时不 commit，留待重试）；无 PostCompact 接线，依靠 Stop 的转录收缩做防御性纠偏 | resume 时注入 archive digest |
| cursor / trae×2 / zcode | 不接管 | cursor：preCompact 无条件 commit（trae×2/zcode 上游无该事件） | — |
| opencode | 不接管 | compacting 前 flush+commit | `session.compacted` 触发后再 flush+commit（共两次） |
| dsh | 不感知（不监听 compaction 事件；注入走 pre-step user 消息，随宿主压缩一起收缩，profile 不重投） | — | — |
| pi | **takeover 双层接管**（默认开，[§3.4.2](#_3-4-2-pi-takeover)） | `session_before_compact`：flush → commit → pollOverview。成功则返回自定义 compaction 摘要覆盖 pi 的；失败则 fail-open 回退到 pi 默认压缩 | 成功后 resetBoundary |
| openclaw | **全接管**：`ownsCompaction: true`，宿主不再跑自己的摘要（[§3.4.3](#_3-4-3-openclaw-contextengine)） | `compact()` = commit(wait=true, keep 0) → 读回 overview 当 summary | 主 assemble 用 `[Session History Summary]` 重建上下文 |
| hermes | 不接管（`on_pre_compress` 接口预留，当前不参与压缩摘要） | fork 型压缩边界会触发旧会话 commit；原地压缩不动 | — |

### 3.4.2 pi takeover

- 接管面是 `context` 事件的 messages 改写，不接管 pi 的历史存储。触发是 token 压力（30000 + 保留 3 轮），而非 pi 的压缩事件。
- 替换动作：先定位边界，再把边界前的全部消息替换成一条合成 user 消息 `[OpenViking Session Context]`。其中 overview 按 3000 token 截断；timestamp 取保留首条 -1，以稳定 provider payload 吃 prompt cache。
- 数据源是 `GET /sessions/{id}/context` 的 `latest_archive_overview`（轮询 15×2s）；状态持久化在 pi 自己的 branch custom entry `ov-takeover`。
- 失败姿态 = fail-open 回完整历史（指纹不匹配 / 历史短于边界 / overview 拿不到三重回退）。
- 与 pi 原生压缩的关系：`session_before_compact` 成功时返回 `{compaction:{summary, firstKeptEntryId, …, details:{source:"openviking"}}}` 覆盖 pi 摘要；缺 `firstKeptEntryId` 时走 pi 默认压缩。

### 3.4.3 openclaw ContextEngine

- 实现宿主 `ContextEngine` 接口，`assemble()` 分两个分支：transformContext（只做召回前置注入，5 道 passthrough 守卫）与 main-assemble（`getSessionContext(tokenBudget)` → 用服务端返回替换宿主 live 历史，四层预算切分，3 道 passthrough 保护 + provider 消息 sanitize 管线）。
- `compact()` = `commit(wait=true, keep 0)`（500ms 轮询，Phase2 上限 5 分钟）→ `latest_archive_overview` 当 summary、`archive_uri` 末段当 `firstKeptEntryId`。`customInstructions`/`compactionTarget` 保留接口，当前不参与压缩产物。
- `ingest()/ingestBatch()` 是刻意 no-op，写入全走 `afterTurn`。
- 有归档时额外注入 20 行 "Session Context Guide" systemPromptAddition，指示模型在说"没有信息"之前先重读摘要，并用 `ov_archive_search` 尝试至少 2 组关键词。

### 3.4.4 pi 与 openclaw 接管方式对照

| 维度 | pi takeover | openclaw ContextEngine |
|---|---|---|
| 宿主契约 | 一个 context 钩子的 messages 改写 | 注册 ContextEngine，`ownsCompaction: true` |
| 历史真相源 | 仍是 pi 本地 branch | OV 服务端 getSessionContext |
| 触发 | 客户端 token 阈值 30000 + 保留 3 轮 | 宿主调用 assemble/compact |
| 压缩产物 | 一条合成 user 消息（3000 token 截断） | 重建后的整个 messages 数组 + compaction summary |
| 失败姿态 | fail-open 回完整历史 | passthrough 回宿主 live 消息 |
| 召回与 session | context face 带 session_id | `/find` 不带（expansion/台账不参与） |

## 3.5 写入与删除的类型边界

### 3.5.1 写入边界

MCP `write` / REST `content/write` 的三道 guard（`content_write.py`）：可写域限 `viking://resources|user|agent`；新建文件扩展名需符合白名单 `.md .txt .json .yaml .yml .toml .py .js .ts`；用户根下 `skills/ peers/ privacy/ sessions/` 四个托管子树只读（`_USER_MANAGED_SUBTREES`）。已存在的 `.abstract.md/.overview.md` sidecar 可以改正文，但公共写入 API 不能创建它们。

### 3.5.2 删除边界

**第一档：服务端通用防线（所有删除入口共享）**。`VikingFS.rm` 第一句 `_ensure_delete_access`（`_access.py:182-229`）实施 5 道检查：命名空间可访问性；用户删除进行中 → FailedPrecondition；actor-peer 隐藏视图 → PermissionDenied；命名空间根保护：裸 `viking://`、`viking://user` 根、`viking://agent` 根一律拒删；非 ROOT 删 `viking://temp` 拒。这一档按命名空间根设防，不区分 memory/resource/skill 类型——类型级差异由后三档在客户端实施。

**第二档：客户端零附加（MCP 面 + dsh/pi/langchain/ov rm）**。差异只在参数面：dsh/pi 的 `viking_forget` 里 `recursive` 固定 false（不删除目录），且 query 删除需 score>0.8；LangChain `viking_forget` 的 `recursive` 是模型可控参数（但默认不在工具面）；`ov rm -r` 则显式开递归、无确认提示（TUI 的 `d` 键有 y/n 确认 + root/scope 目录禁删）。

**第三档：memory-only 的两个删除面**。

- openclaw `memory_forget`：三条白名单正则只放行 `viking://user/[…/]memories`、`viking://user/<u>/peers/<p>/memories`、`viking://agent/[…/]memories`；显式 uri 不匹配直接拒绝；搜索路径候选先过同一 guard，且只有在候选唯一且 score≥0.85 时才自动删，否则列出候选让 agent 指名；底层 URL 固定 `recursive=false`。
- hermes `viking_forget`：六道顺序校验——非 str / 空拒；scheme≠viking 拒；带 query/fragment 拒；目录或非 `.md` 结尾拒；必须命中 4 种 memories 路径形状之一，且 `memories` 段后至少还有 2 段（不删除 `memories/` 与分类目录本身）；文件名不能是 `.abstract.md/.overview.md`。

**第四档：默认不提供删除（LangChain / Open WebUI）**。LangChain `viking_forget` 需配置 `profile="admin"` 或 `allow_forget=True` 才加入工具面；Open WebUI 则完全不提供删除工具。

**skill 的增删边界**：新增入口是 openclaw `add_skill`（默认开）、`ov add-skill` 与 REST。对 skill 完全只读（不增不删）的删除面是 openclaw `memory_forget` 与 hermes `viking_forget`；相对地，MCP 面、dsh、pi、`ov rm` 加不了 skill 但删得掉——加不了是写路径 `_USER_MANAGED_SUBTREES` 的保护，删得掉是删除路径不检查该集合。

## 3.6 降级与容错

### 3.6.1 判定矩阵

| harness | 服务端不可达时 | 负缓存 | HTTP 重试 | 失败阻塞宿主 |
|---|---|---|---|---|
| claude-code | 各 hook catch→approve，不阻塞；session-start 时连 pending 重放都跳过 | context-face 6h + host-cli 探测 7d + health 5s | 无（靠 pending 重放）；peer_scope 降级 1 次；batch→逐条 | 否（uri-guard deny 是设计意图） |
| codex / trae-cli | 各 hook catch→noop | context-face 6h + 压缩器 runtime_failed（至下次启动） | 同上（无磁盘 pending，靠游标重发） | 否 |
| cursor/trae×2/zcode | fetch 吞成 status:0，catch 返回空注入；锁 5s 拿不到则静默跳过 | context-face 6h（服务端整体不可达时无负缓存，每轮等满 15s） | 无 | 否 |
| opencode | 各路径 catch→WARN；`event`/`dispose` hook 无 try/catch（不可重试的 commit 失败会冒泡宿主） | 仅 context-face 6h；`/health` 无缓存（每轮一次往返） | 无同步重试；MCP 代理 401/403、400/404 各一次 | 基本否（event/dispose 例外） |
| dsh | client 全吞异常；`ensureState` 失败不缓存（服务端不可达时每 pre-step 两次 health 各 5s） | context-face 6h + user-space 缓存进程内不过期 | 无；pending 跨进程重放 3 次 | 是（pre-step 串行 profile+recall；session/flush 阻塞） |
| pi | health 失败时 start() 提前返回，此后每 prompt 静默重试连接 | context-face 6h | 无；仅 pending queue | 部分（session_shutdown 被 await：takeover 近 0、非 takeover 最坏 30s；turn_end 网络异常时逐条各等 10s） |
| openclaw | client 构造永不失败；health 吞异常；召回 500ms precheck 失败跳过 | 无负缓存（每轮一次 500ms health 预检） | 无（单次 fetch）；commit/afterTurn 的 Phase2 轮询 | 否（`memory_store` 重抛例外；`compact()` 最长阻塞 5 分钟） |
| hermes | `_client=None` 即运行期负缓存（本进程不再重试，除本地自启 waiter 外） | 无独立结构（`_client=None` 承担） | trusted 补身份 1 次、sync 全新 client 1 次、多档降级；commit 失败不重试 | 否（后台单 worker + 逐 provider try/except） |
| ov CLI | 多数 exit 1；`ov status` 表格模式始终退 0；`ov health` 即使 unhealthy 也退 0 | 无 | 仅网关 401 挑战重试 1 次 | n/a（无宿主） |

### 3.6.2 通用超时

通用 HTTP 15000ms（下限 1000）；MCP 代理请求 15000ms、DELETE 固定 2000ms；跨进程锁等待 5s、陈旧 60s；pending `.processing` 陈旧回收 10min。需注意 MCP 代理未注册 SIGHUP（关终端不发 `DELETE /mcp`；但服务端 `stateless_http=True`，影响有限）。

## 3.7 附加 UX 对照

| harness | statusline | slash command | rule/skill | setup 向导 | 其他 |
|---|---|---|---|---|---|
| claude-code | ✅ 独立进程写 settings.json（段位丰富，1min TTL） | ✅ `/openviking-memory:ov`（服务状态 + 身份 + 注入溯源） | 1 experience skill | ✅ 行式问答 | debug-recall/debug-capture 诊断脚本；uri-guard 不受插件开关门控 |
| codex / trae-cli | ❌ | ❌ | 1 experience skill | ✅ | VERIFICATION.md 8 步 SOP |
| cursor | ❌ | ❌ | rule（alwaysApply）+ skill | ❌（共用安装器 TUI） | 独立 uri-guard，不受插件开关控制 |
| trae/trae-cn | ❌ | ❌ | 无 | ❌ | — |
| zcode | ❌ | ❌ | 无 | ❌ | — |
| opencode | ❌（有 toast） | ❌ | 无（设计上不提供） | ✅ | — |
| dsh | ❌ | ❌ | 无 | ❌ | 工具调用卡片 presentCall；`ctx.provide("openvikingMemory")` 供其他 Cordis 插件二次开发 |
| pi | ✅ `ctx.ui.setStatus` | ✅ `/viking` `/viking commit` | 无 | ✅ | e2e-live.sh |
| openclaw | ❌ | ✅ 5 个（/add-resource /add-skill /ov-search /ov-query-config /ov-recall-trace） | 3 skill 随插件分发 | ✅（key 角色探测 + 版本兼容检查 + `status` 命令） | Gateway HTTP 路由做 recall trace 可视化；feature-gate RPC；健康检查脚本 |
| hermes | ❌ | ❌ | 无 | ✅ curses 多层菜单 | `hermes memory status`（含 env 覆盖列表）；`hermes backup` 带 ovcli.conf |
| ov CLI | ❌ | ❌（自身即命令） | 无 | ✅ TUI 向导 | 完整 help 系统（63 条 curated）；语言门禁；`ov tui` 全屏文件浏览器（含终端内图片预览） |

---

# 4. Harness 档案卡

每卡都是检索入口，只写该 harness 的独有事实与差异，共享机制回链维度章。统一字段：形态 / 能力亮点 / 行为要点 / 配置 / 维度索引。

## claude-code

- **集成文档**：[Claude Code 记忆插件](./02-claude-code.md)
- **形态**：CC 插件（marketplace），采用四合一架构：9 hook + MCP 代理（15 工具透传）+ slash command + statusline + 1 experience skill。版本 0.4.4。
- **能力亮点**：hook 覆盖最全的 harness——SessionStart(120s) / UserPromptSubmit(60s) / PostToolUse:Read(5s，默认关的 skill-experience) / PreToolUse:Read\|Glob\|Grep(5s，uri-guard) / Stop(45s) / PreCompact(30s) / SessionEnd(30s) / SubagentStart(10s) / SubagentStop(45s)；默认启用召回再摘要（本地 `claude -p`，本地不可用时自动回落服务端 rewrite，[§3.2.5](#_3-2-5-召回再摘要)）；支持 SubagentStart/Stop 的完整子会话隔离（[§3.3.5](#_3-3-5-subagent-会话对照)）；statusline + slash + uri-guard；关闭链路除 kill -9 外全部 commit（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）。
- **行为要点**：session id 为 `cc-<CC session_id 原文>`，subagent 为 `…__subagent-<agent_id>`；Stop 阈值 commit 20000/keep 10，PreCompact 同步 commit；自动召回排除 resources（[§3.2.1](#_3-2-1-机制底座-一条共享管线-两条服务端路径)）；增量游标存于 `/tmp`（被系统清理后，同一会话会整段重推）。
- **配置**：env + ovcli.conf `plugin.claude_code` + ov.conf `claude_code`（[§3.1.4](#_3-1-4-配置体系分层)），约 40 个旋钮；压缩器命令与模型固定为 `claude`/`sonnet`/`low`/30s。
- **维度索引**：工具面 [§2.1](#_2-1-服务端-mcp-工具面) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) ｜压缩 [§3.4](#_3-4-压缩-compaction-接管) ｜降级 [§3.6](#_3-6-降级与容错) ｜UX [§3.7](#_3-7-附加-ux-对照)。

## codex

- **集成文档**：[Codex 记忆插件](./04-codex.md)
- **形态**：Codex 插件（marketplace），4 hook（SessionStart 70s / UserPromptSubmit 130s / Stop 30s / PreCompact 60s）+ MCP 代理 + 1 experience skill。版本 0.7.5。
- **能力亮点**：本地召回压缩管线（`codex exec`，[§3.2.5](#_3-2-5-召回再摘要)）；SessionStart 的 active-window 启发式 + idle-TTL 扫描，可自动回收历史会话中未归档的消息（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）。
- **行为要点**：session id 为 `cx-<safeId>`（确定性推导，不读 state）；关闭时无 hook（上游不提供 SessionEnd），commit 依赖下次启动的回收路径；不使用磁盘 pending queue（离线靠游标不推进、下一轮重发补偿，[§3.3.4](#_3-3-4-pending-queue-离线补偿对照)）；active-window 120000ms / idle-TTL 1800000ms（env 配置）；Stop 默认 detach（`appended N turn(s)` 提示默认不展示）。
- **配置**：env + ovcli.conf `plugin.codex` + ov.conf `codex`；hooks 同时发送 Bearer 与 `X-API-Key` 兼容头（[§3.1.3](#_3-1-3-凭据体系)）。
- **维度索引**：工具面 [§2.1](#_2-1-服务端-mcp-工具面) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) ｜降级 [§3.6](#_3-6-降级与容错)。

## trae-cli（TraeCode CLI 2.0）

- **集成文档**：[TRAE 记忆集成](./13-trae.md)
- **形态**：TraeCode CLI 2.0 是 Codex 系 CLI（binary `traecli`，用户配置 `~/.trae/traecli.toml`，TUI 支持 `/plugins` `/skills` `/mcp`）。OpenViking 经 **codex 插件别名安装**接入：`--harness trae-cli` 复用 codex 安装流程，仅安装参数（binary / home / 配置路径）指向 TraeCode CLI。
- **能力面**：与 codex 完全一致——4 hook + MCP 代理 + experience skill、本地召回压缩、active-window/idle-TTL commit 回收、resume archive 注入等，详见 codex 档案卡。
- **版本支持**：仅支持 TraeCode CLI 2.0。1.0 与 2.0 不是同一套 CLI，2.0 才是 Codex 系、才能走 codex 插件别名安装；早期面向 1.0 的独立插件 `examples/trae-cli-memory-hooks`（`~/.trae/cli/hooks.json` + `[mcp_servers."openviking-memory"]` 方案）已废弃。
- **维度索引**：同 codex 卡。

## cursor

- **集成文档**：[Cursor 记忆集成](./12-cursor.md)
- **形态**：配置驱动（写 `~/.cursor/hooks.json`+`mcp.json`）+ MCP 代理 + always-on rule + skill。7 hook：sessionStart(30s) / beforeSubmitPrompt(20s) / beforeReadFile(5s) / beforeShellExecution(5s) / stop(30s) / preCompact(30s) / sessionEnd(30s)。相对 import 共享 lib（不 vendoring）。
- **能力亮点**：beforeReadFile/beforeShellExecution 双 uri-guard（不受插件开关控制）；rule + skill 随装。
- **行为要点**：session id 为 `cu-<conversation_id>`；stop 每 8 条消息 commit（`commitTurnThreshold=8`，消息条数计数，keep 0）；`sessionEnd` 仅 window_close 触发，且此时宿主已销毁 shell-exec host，实践中不执行（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）——结束在 <8 条消息水位的会话，尾部依赖后续同会话消息触发归档（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）；服务端不可达时，每轮等满 15s 召回超时。
- **配置**：仅 env（`plugin` 段不参与解析）。
- **维度索引**：工具面 [§2.1](#_2-1-服务端-mcp-工具面) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) ｜降级 [§3.6](#_3-6-降级与容错)。

## trae / trae-cn（IDE 版）

- **集成文档**：[TRAE 记忆集成](./13-trae.md)
- **形态**：配置驱动（`~/.trae{,-cn}/hooks.json` + 平台相关 mcp.json）+ MCP 代理。4 hook：SessionStart(30s) / UserPromptSubmit(20s) / PreToolUse:Read\|Glob\|Grep\|Bash\|RunCommand(5s) / Stop(30s)。相对 import 共享 lib。MCP server 名为 `openviking`。
- **能力亮点**：行为最简单直接的一档——每个有内容的 Stop 都 commit（keep 0），关闭场景下最大待归档量只有最后一轮 in-flight（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）。
- **行为要点**：trae 与 trae-cn 的差异是 session id 前缀 `tr-` / `trcn-` 与安装路径——同一份记忆在两个客户端落到两组不同 session，跨客户端共享靠服务端抽取后的记忆空间而非 session 复用；无 PreCompact/statusline/skill/subagent 处理。
- **配置**：仅 env。
- **维度索引**：工具面 [§2.1](#_2-1-服务端-mcp-工具面) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)。

## zcode

- **集成文档**：[社区插件 → ZCode](./08-community-plugins.md)
- **形态**：配置驱动（合并进 `~/.zcode/cli/config.json`，强制 `hooks.enabled=true`）+ MCP 代理。4 hook：SessionStart(30s) / UserPromptSubmit(20s) / PreToolUse:Read\|Glob\|Grep(5s) / Stop(30s)。唯一全量 vendoring 18 个共享文件的 harness。版本 0.1.1。
- **能力亮点**：以 rollout 文件 `~/.zcode/cli/rollout/model-io-<sid>.jsonl` 为增量真相源（`lastTurnId` 差集补齐漏掉的 Stop）；Stop 默认 detach（Ctrl+C 不丢写入）。
- **行为要点**：每 Stop commit（keep 0）；捕获路径仅剥离三类注入块（不做额外文本清洗，[§3.2.6](#_3-2-6-注入回流防护)）；首次捕获会一次性读取整个 rollout（长会话首装时单次推送量大）。
- **配置**：仅 env；`OPENVIKING_WRITE_PATH_ASYNC` 对 zcode 生效。
- **维度索引**：工具面 [§2.1](#_2-1-服务端-mcp-工具面) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)。

## opencode

- **集成文档**：[OpenCode 插件](./10-opencode.md)
- **形态**：npm 插件 `@openviking/opencode-plugin`，config hook 自注入 MCP 条目（工具带 `openviking_` 前缀）。7 个 plugin hook：config / event / tool.execute.before / experimental.chat.system.transform / chat.message / experimental.session.compacting / dispose。版本 0.2.4。
- **能力亮点**：`dispose` hook 覆盖全部四种常规关闭方式（宿主 ≥1.15.11）；repo 列表进 system prompt（[§3.2.3](#_3-2-3-profile-开场注入)）；宿主事件面最丰富（session.idle/compacted/deleted/error 各有语义）。
- **行为要点**：`commitTokenThreshold=20000`（取正数，0 回落默认值）；commit 超时 30000ms；一次宿主压缩 = 两次 commit；dispose 的 5s 宿主预算下，多会话慢 commit 可能被截断，且 pending queue 不覆盖此场景（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）；<1.15.11 无 dispose 时关闭不 commit；重启后不主动 flush 遗留会话；开场注入每会话尝试一次（[§3.2.3](#_3-2-3-profile-开场注入)）；`bypassSessionPatterns` 的目录匹配在 opencode 上不适用（input 无 cwd）。
- **配置**：`openviking-config.json`（4 级搜索）+ env。
- **维度索引**：工具面 [§2.1](#_2-1-服务端-mcp-工具面) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) ｜subagent [§3.3.5](#_3-3-5-subagent-会话对照)。

## dsh（DeepSeek Harness）

- **形态**：唯一同进程 Cordis 原生插件（`export function apply`），原生注册 7 个 `viking_*` 工具（`viking_search/read/browse/remember/forget/add_resource/archive_expand`），REST 直连。4 事件：agent/session-start（emit）/ agent/pre-step（waterfall）/ session/event / session/flush。版本 0.1.0。
- **能力亮点**：`ctx.provide("openvikingMemory")` 供其他 Cordis 插件二次开发；pre-step 注入走 user 消息，适配 DSH persona 的 `complete:true` 渲染模式。
- **行为要点**：安装走 `dsh plugin add ./examples/dsh-memory-plugin`（统一安装器暂不覆盖）；teardown commit 3s 无阈值，SIGHUP/二次 Ctrl+C 不触发（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）；compaction 不感知（注入内容随宿主压缩收缩，profile 不重投）；subagent 各自独立会话（[§3.3.5](#_3-3-5-subagent-会话对照)）；工具面较 MCP 面少 write/edit/tree/grep/glob/watch/health；uri-guard 匹配工具名不做大小写归一化。
- **配置**：cordis patch + 4 个 env；凭据 patch>env，行为开关 env>patch。
- **维度索引**：工具面 [§1.1](#_1-1-主动工具面-agentic-调用能力) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) ｜降级 [§3.6](#_3-6-降级与容错)。

## pi（pi Coding Agent Extension）

- **集成文档**：[pi Coding Agent 扩展](./11-pi.md)
- **形态**：pi 原生扩展（目录装载，jiti 直译 TS），原生注册 7 个 `viking_*` 工具，REST 直连（pi 无 MCP 支持）。8 事件 + `/viking` 命令。版本 0.1.0。
- **能力亮点**：takeover 压缩接管（默认开，[§3.4.2](#_3-4-2-pi-takeover)）；两段式召回（before_agent_start 排队 + context 事件同步检索，当前轮 prompt 拿当前轮记忆）；statusline；`session_shutdown` 在所有关闭方式下都触发且被 await。
- **行为要点**：默认 takeover 下退出不 commit（handler 持久化本地状态，归档靠下次续跑攒满阈值或 `/viking commit`，[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）；takeover 阈值 30000 token + 保留 3 轮（keep 3，服务端按消息条数解释）；非 takeover 阈值 20000/keep 10、退出无条件 commit；工具注册需 health+ensureSession 前置（[§1.1](#_1-1-主动工具面-agentic-调用能力)）；`viking_add_resource` 仅 HTTP URL（guard 在服务端）；非 takeover 模式下 `pi -c` 续跑会重新上报整条 branch。
- **配置**：`config.json` 行为旋钮 + env（凭据统一走凭据链，[§3.1.3](#_3-1-3-凭据体系)）；`bypassPatterns` 为前缀匹配（非 glob）。
- **维度索引**：工具面 [§1.1](#_1-1-主动工具面-agentic-调用能力) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜takeover [§3.4.2](#_3-4-2-pi-takeover) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)。

## openclaw

- **集成文档**：[OpenClaw 插件](./03-openclaw.md)
- **形态**：唯一 context-engine 全接管型（`ownsCompaction:true`）。15 原生工具（默认开 14）+ 5 slash + 4 hook + Gateway HTTP 路由 + feature-gate RPC。remote-only。版本 2026.6.18。
- **能力亮点**：检索拆两个默认不重叠入口——`memory_recall` 默认搜 memory、`ov_search` 默认搜 resource+user skills（都可显式参数越界）；`add_skill` 默认开；`memory_forget` memory-only 白名单（[§3.5](#_3-5-写入与删除的类型边界)）；三个 tool-result 工具读服务端外置输出（跨会话 guard）；ContextEngine 全接管（[§3.4.3](#_3-4-3-openclaw-contextengine)）；setup 向导带 key 角色探测与版本兼容检查。
- **行为要点**：召回走 `/find` 不带 session_id（expansion/去重台账不参与，长会话中同一记忆可能重复注入）；关闭不触发 commit，归档依赖显式 `/new`/`/reset` 与 ~50% 阈值（[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）；无本地 pending queue（失败轮次不重放）；`compact()` 最长阻塞 5 分钟；召回默认对每条 leaf 记忆多一次 read（`recallPreferAbstract=false`）；配置严格校验，存在未知键/非法值时插件进入 setup-only 模式。
- **配置**：`openclaw.json` 的 `plugins.entries.openviking.config` + 少量 env；认证头 `X-API-Key`（[§3.1.3](#_3-1-3-凭据体系)）；commit 阈值由 `commitTokenThresholdRatio` 控制（默认 0.5）；召回字符预算 4000。
- **维度索引**：工具面 [§1.1](#_1-1-主动工具面-agentic-调用能力) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜ContextEngine [§3.4.3](#_3-4-3-openclaw-contextengine) ｜删除 [§3.5](#_3-5-写入与删除的类型边界) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)。

## hermes（Nous Research）

- **集成文档**：[Hermes Agent](./05-hermes.md)
- **形态**：Hermes bundled MemoryProvider（Python 单文件实现，共 3725 行，随 Hermes 一同发布）。通过 `httpx` 直连，无需额外安装插件。提供 6 工具及 10+ 生命周期 hook（prefetch/sync_turn/on_session_end/on_session_switch/on_memory_write/…）。基线为发行版 `e12626b3`（= brew 2026.7.7.2）。
- **能力亮点**：具备最全面的资源摄取面——`viking_add_resource` 支持 HTTP、Git、SSH、`file://`、本地文件 temp_upload 以及本地目录 zip 打包上传（跳过 symlink + 越界文件）；`viking_remember` 直写记忆文件（不依赖 session commit/抽取）；支持本地服务端自启（当 endpoint 位于本地且不可达时，通过 `subprocess.Popen openviking-server` 拉起）；支持 trusted 补身份重试；无论正常退出还是接收到 Ctrl+C、SIGTERM、SIGHUP 信号，均能保证完成 commit（详见 [§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）。
- **行为要点**：在召回上，仅有 `search/search` 的首选路径携带 session_id 并落到路径 B（`mode="deep"`；而 `auto`/`fast` 走 `/find` 且不带 session_id，详见 [§3.2.2](#_3-2-2-判定矩阵)）。`queue_prefetch` 为同步实现，无预热；subagent 传入 `skip_memory=True` 时不接 OV（详见 [§3.3.5](#_3-3-5-subagent-会话对照)）。无 profile 注入/statusline/slash；commit 恒 keep 0，drain 不净则本次不 commit；进程内队列不落盘。session id 格式为 `%Y%m%d_%H%M%S_<hex6>`。召回参数：6 条/阈值 0.15/字符预算 4000/总超时 4s。记忆 URI 为 `viking://user/peers/{agent}/memories/{subdir}/mem_<uuid12>.md`。退出机制包含 SIGTERM grace 1.5s 与退出看门狗 30s。互补路径支持 `openviking-server ingest hermes`（离线重放，默认关，详见 [§7](#_7-附录-非-coding-集成速览) E）。
- **配置**：通过 `OPENVIKING_ENDPOINT`（非 `_URL`）、8 个 `OPENVIKING_RECALL_*` env 以及 `config.yaml` 进行配置。在 `use_ovcli_config` 模式下，系统会清空 `.env` 里的对应变量。
- **维度索引**：工具面 [§1.1](#_1-1-主动工具面-agentic-调用能力) ｜召回 [§3.2](#_3-2-自动召回与注入) ｜commit [§3.3.2](#_3-3-2-常规-commit-触发条件)/[§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵) ｜删除 [§3.5](#_3-5-写入与删除的类型边界)。

## ov CLI

- **集成文档**：[部署指南 → CLI](../guides/03-deployment.md#cli)
- **形态**：Rust 原生二进制，把服务端 REST 包成命令行；无宿主事件、无自动召回、不接管压缩。
- **能力亮点**：唯一会下发 `auto_commit_policy` 的第一方客户端（`ov session new --auto-commit-policy-json`、`ov session config set`）；提供多 profile、admin、privacy、snapshot、TUI 等插件面没有的能力（详见 [§5.3](#_5-3-cli-独有于插件面的能力)）。
- **维度索引**：完整命令参考请见 [§5](#_5-ov-cli-命令参考)。

---

# 5. ov CLI 命令参考

`ov`（clap 内部名 `openviking`）是 Rust HTTP 客户端，所有能力都在服务端，CLI 只做参数拼装、本地打包上传、输出渲染与多 profile 管理。它不是 harness 集成——没有宿主事件、不自动召回、不接管压缩；本章覆盖它作为"人/脚本直接使用 OV"的完整命令面，以及它独有于插件面的能力（TUI、多 profile、admin、`--sudo`、privacy、snapshot 等）。

> 版本说明：以 HEAD 源码为准（HEAD tag 已到 `cli@0.4.14`），0.4.10 等旧版本的差异处已标注。需要注意的是，`ov doctor` 不在 Rust 二进制里——pip 装的 Python wrapper 会拦截 `argv[1]=="doctor"` 并走 `openviking_cli.doctor`，读取的是服务端 `ov.conf` 而非 `ovcli.conf`；npm/cargo 装的纯 Rust `ov` 无该子命令。

## 5.1 命令树

命令 doc-comment 前缀 `[Data]`/`[Interactive]`/`[Admin]`/`[Experimental]` 等只影响 help 渲染，不影响运行时——即使是 `[Experimental]` 命令，默认也可用。

**数据写入**：`add-resource`（支持本地文件/目录/URL/Git/sitemap/RSS；目标 `--to`/`--parent`/`-p` 三选一；manifest 模式 `-m`；Connector `--add-type`；注意本地路径 + watch>0 会报错）｜`add-skill`｜`write`（`--content`/`--from-file` 互斥）｜`mkdir`｜`rm`（别名 del/delete，无确认提示，`-r` 显式递归）｜`mv`（别名 rename）｜`set-tags`（顶层隐藏，用 `attrs set-tags`）｜`add-memory`（实验性；执行逻辑为建 session → 批量 add → commit 三步串行）

**读取/检索**：`ls`（别名 list）｜`tree`（`-L` 默认 3）｜`stat`／`attrs get`｜`read`／`abstract`／`overview`（分别对应 L2/L0/L1）｜`get`（下载）｜`find`（query 或 `--image` 至少一个非空；`--image` 支持本地路径、data URI、http、viking://）｜`search`（实验性，比 find 多一个 `--session-id`）｜`grep`｜`glob`

**Skills**：`skills add`（支持本地路径、git 及 GitHub tree URL，`-s` 选装、`*` 全装，有交互确认）｜`skills list/find/show/update/remove`｜`skills validate`（唯一完全离线的命令）

**会话/记忆**：`session new`（`--auto-commit-policy-json` 与 `--no-auto-commit` 互斥——唯一下发 policy 的第一方客户端）｜`session list/get/delete`｜`session get-session-context`（`--token-budget` 默认 128000）｜`session get-session-archive`｜`session add-message(s)`｜`session config set`（改可变配置）｜`session commit`

**导入导出/快照**：`export`／`backup`／`import`／`restore`（.ovpack）｜`snapshot commit/restore/show/log/diff/ignore-*`（工作区快照，前向 commit 回滚）

**隐私**：`privacy categories/list/get/versions/version/activate/upsert`（`--key-<name>` 语法糖）

**状态/可观测**：`health`（healthy=false 时仍退 0）｜`status`（表格模式始终退 0）｜`observer {queue,vikingdb,models,retrieval,filesystem,system}`｜`wait`｜`task status/cancel/list`｜`task watch {ls,show,rm,pause,resume,update,trigger}`｜`version`（独立 3s 超时探服务端）

**配置/交互**：`config`（TUI 向导，5 项菜单含 User Management）｜`config show`（固定 json+compact 输出）｜`config validate`｜`config list/switch/add/edit/delete`（面向 Agent，退出码 2-6 语义化）｜`config add ov-service`（云）／`config add custom`｜`language`（别名 lang）｜`tui`（全屏文件浏览器 + 终端内图片预览 + 向量视图 + 删除确认）｜`chat`（VikingBot，300s 超时）｜`compile`（用 Skill 整理素材，`--wait` 本地轮询）

**管理（多为 ROOT/`--sudo`）**：`admin create-account/list-accounts/delete-account/set-role/migrate/register-user/list-users/remove-user/regenerate-key`｜`system wait/status/health/consistency`｜`system crypto init-key`（纯本地生成 32 字节 root key，权限 0600）｜`system backend sync-status/sync-retry`｜`reindex`（`--mode` 默认 vectors_only；`--wait` 默认 true，全 CLI 唯一）｜`doctor`（仅 Python wrapper）

## 5.2 全局选项与独有机制

- `-o/--output table|json`（默认取 conf `output`）｜`-c/--compact`（默认 true）｜`--account`/`--user`/`--actor-peer-id`｜`--sudo`（用 root_api_key，只允许 admin/system/reindex/task status/task list）｜`--profile`（隐藏）
- **多 profile**：active 为 `~/.openviking/ovcli.conf`，命名规范为 `ovcli.conf.<name>`；`switch` 是字节复制（保留 `plugin` 段），而 `add/edit` 走 serde 重写（会丢弃 Rust Config 不识别的键，含 `plugin` 段，见 [§3.1.4](#_3-1-4-配置体系分层)）。
- **语言门禁**：跑任何命令前要求先存显示语言（未存 + 非交互 → exit 2）；HEAD 起 `--help` 在门禁前豁免（0.4.10 尚无此豁免），但 `--version` 仍需先过门禁。
- **三种 JSON 输出格式（按命令组不同）**：普通命令 compact 下输出 `{"ok":true,"result":…}`，`-c false` 输出裸 payload，失败输出 `{"ok":false,"error":…}`；config 系为 `{"status":"ok","result":…}`；带 profile 时追加 `"profile":[…]`。脚本解析时需注意；此外 `echo_command` 默认 true 且不受 `-o json` 抑制（stdout 第一行是 `cmd: …`）。
- **env**：`OPENVIKING_CLI_CONFIG_FILE`、`OPENVIKING_UPLOAD_MODE`（local/shared）、`OPENVIKING_ASSETS_CREDENTIALS_FILE`、`OPENVIKING_LANG`/`LC_*`/`LANG`，以及 chat 用的 `VIKINGBOT_ENDPOINT`/`VIKINGBOT_API_KEY`/`OPENVIKING_URL`。

## 5.3 CLI 独有于插件面的能力

以下是插件面拿不到、只有 CLI/TUI 能做的：多 profile 切换与向导、`admin` 全套账号/用户管理、`--sudo` root 操作、`privacy` 隐私策略 CRUD + 版本、`snapshot`/`backup`/`restore`/`export`/`import` 数据搬运、`reindex` 重建索引、`system crypto init-key` 生成 root key、`ov tui` 交互浏览、依赖 VikingBot 能力的 `ov chat`／`ov compile`，以及 `ov session config set` 显式设 `auto_commit_policy`（唯一能开服务端自动 commit 的第一方入口）。

---

# 6. 自定义 agent 接入指南

如果你使用的 agent/harness 不在上面 11 家里，可以参考以下三条接入路径（按投入成本从低到高排列）。

## 6.1 接入路径 × 能获得的能力

| 路径 | 投入 | agent 主动工具面 | 自动召回/捕获 hook | 会话/commit | 压缩接管 |
|---|---|---|---|---|---|
| ① [通用 MCP 直连](./06-mcp-clients.md) | 分钟级（填写一段 config 即可） | ✅ 15 工具全量 | ❌ 由模型主动调用 | 仅 `remember` 建临时会话 | ❌ |
| ② HTTP API / SDK / [LangChain](./07-langchain-langgraph.md) | 小时级（需写代码） | 自选（按需调 REST） | 自己实现 | 自己实现（或用 LangChain middleware） | ❌ |
| ③ 复用 shared-core / [Agent Plugins 便携包](./15-agent-plugins.md) | 天级（需写 hook 适配） | ✅ 15 工具（经 MCP 代理） | ✅ 召回/捕获/commit/pending 全套 | ✅ | 视接入哪些事件而定 |

## 6.2 路径①：通用 MCP 直连（推荐起步）

任何支持 MCP 的 agent，只需在其 `mcpServers` 配置里指向服务端 `/mcp`（各客户端的具体配置位置见 [MCP 客户端](./06-mcp-clients.md)），即可立刻获得全部 15 个工具（[§2.1](#_2-1-服务端-mcp-工具面)）。最小配置如下：

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://127.0.0.1:1933/mcp",
      "headers": {
        "Authorization": "Bearer <api_key>",
        "X-OpenViking-Account": "<account>",
        "X-OpenViking-User": "<user>",
        "X-OpenViking-Actor-Peer": "<workspace-peer>"
      }
    }
  }
}
```

后三个 header 可选，缺省时没有 workspace peer 隔离与租户路由。stdio-only 的客户端可用便携代理（路径③）把 stdio 桥到 streamable-HTTP。该路径得到的是纯工具面——无自动召回、无捕获、无 commit（除非模型主动调 `remember`）。

## 6.3 路径②：程序化接入

- **直连 REST**：召回用 `POST /api/v1/search/search`（必须 `mode:"context"` + `session_id` 才有 expansion/去重，[§3.2.1](#_3-2-1-机制底座-一条共享管线-两条服务端路径)；可传 `rewrite` 获得服务端 digest，[§3.2.5](#_3-2-5-召回再摘要)）；写入用 `POST /api/v1/sessions/{id}/messages/batch`（≤100 条/批，auto_create）；提交用 `POST /api/v1/sessions/{id}/commit`；读取用 `GET /api/v1/content/read` 等。要用服务端自动 commit，需在 `POST /api/v1/sessions` 时显式带 `auto_commit_policy`，或通过 `PATCH /{id}/config` 修改（[§2.3](#_2-3-服务端会话与-commit-语义)）。
- **LangChain / LangGraph SDK**（`pip install langchain-openviking`）：`OpenVikingContextMiddleware` 提供 `wrap_model_call`（把召回内容注入 `<openviking_context>`）与 `after_agent`（捕获 + 按 `CommitPolicy` 提交，默认 `never`）。这是本组唯一带 session + token 预算的现成自动召回；容错是"只读方法重试一次、写方法不重试"，部分成功时抛 `OpenVikingPartialWriteError`（可按 `input_messages_consumed` 切片重试）。参考 [§7](#_7-附录-非-coding-集成速览) B。
- **Open WebUI**（OpenAPI 工具服务器）：`python -m openviking_openwebui` 起独立进程，在 Open WebUI 里添加 Tool Server URL，即可得 7 个工具（无删除、无 hook）。参考 [§7](#_7-附录-非-coding-集成速览) A。
## 6.4 路径③：要自动 hook 面时复用参考实现

如果希望实现召回、捕获、commit、pending 的全套自动化，完全不必从零开始编写。建议直接参考并复用以下两个现成的实现：

- **`examples/memory-plugin-shared/lib/`**（Node）：包含完整的核心功能模块，例如 `recall-core`（三级降级召回）、`profile-inject`、`capture-utils`（消息归一 + 注入回流防护）、`pending-queue`（离线重放）、`batch-send`、`mcp-proxy-core`（stdio↔HTTP 代理）、`session-model`（会话 id 派生）以及 `credentials`。构建瘦 harness 时，只需实现一个适配层，把宿主生命周期事件映射到这些模块即可（例如 `agent-hook-runtime.mjs` 就是 cursor、trae、zcode 共用的现成一体化运行时，接新宿主时的主要工作只是解析其 stdin JSON 字段名）。
- **Agent Plugins 1.0 便携包**（位于 `agent-plugins/`）：采用 `plugin.json` + `skills/` + `mcp.json`（stdio→HTTP 代理）的规范化便携格式。该方案刻意不含 hooks（召回/沉淀靠 skill 教模型自调工具），非常适合符合 Agent Plugins 规范的客户端直接加载；此外，`plugin.test.mjs` 定义了规范一致性校验（schema URL、name 规则、静态 headers 不含机密、`mcp.json` 引用不逃逸插件根等），可作为自行打包的 lint 依据。

**接入时务必对齐的三个约定**（与现有 harness 保持一致的行为）：① 召回调用点必须转发 `session_id`，这样才有服务端 expansion + 跨轮去重（详见 [§3.2.1](#_3-2-1-机制底座-一条共享管线-两条服务端路径)）；② 适配器不要用自己的超时压过 helper 下发的 deadline；③ 关闭时要安排一条 commit 路径，否则未达阈值的尾部对话需等待后续触发才能归档（详见 [§3.3.3](#_3-3-3-关闭方式-×-harness-终局矩阵)）——若宿主不提供关闭事件，可依赖服务端 idle 兜底（在服务端开 `memory.session_auto_commit.idle_enabled` 并下发 per-session policy）。这三条正是 `recall-session-wiring.test.mjs` 用跨插件正则钉死的。

---

# 7. 附录：非 coding 集成速览

| 集成 | 形态 | 工具面 | 会话/commit | 容错 | 默认状态 |
|---|---|---|---|---|---|
| **A. [Open WebUI](./08-community-plugins.md)** | 独立的 FastAPI OpenAPI 工具服务器 | 7 个本地 OpenAPI 路由（`ov_search`/`ov_recall_memories`/`ov_add_memory`/`ov_list_memories`/`ov_read_resource`/`ov_add_resource`/`ov_session_status`），无删除工具 | 无会话概念 | 最薄：裸 httpx，无 retry/负缓存；`/health` 仅回显配置，不探测 OV | 进程不启动即不存在 |
| **B. [LangChain/LangGraph](./07-langchain-langgraph.md)** | Python SDK 适配层（retriever/tools/store/middleware/recorder） | `create_openviking_tools()` 提供 12 个 StructuredTool（`viking_forget` 默认不在 agent profile 中） | `thread_id`/`session_id` 由调用方给；`CommitPolicy` 默认 `never` | 本组最稳：只读方法自动重试 1 次、写方法不重试（防重复）、部分成功抛结构化异常可切片重试 | 需全部显式构造才生效 |
| **C. [Agent Plugins 1.0](./15-agent-plugins.md)** | 便携包 `plugin.json` + `skills/` + `mcp.json`（stdio→HTTP 代理） | MCP 透传 15 工具；刻意无 hooks（召回靠 skill 教模型） | 仅 `remember` 建临时会话 | MCP 代理层重试（401/403 换凭据、400/404 重初始化各 1 次） | 客户端加载后工具即用 |
| **D. [通用 MCP 直连](./06-mcp-clients.md)** | 零本地组件，直连 `/mcp` | 同 C（15 工具） | 同 C | 由客户端自定 | `/mcp` 常驻 |
| **E. [log ingestion](./09-log-ingestion.md)** | `openviking-server ingest` CLI（跑在日志所在机器，反向导入） | 无（只写不召回） | 会话 id `{prefix}__{harness}__{sanitized native id}`；commit token 6000/idle 5s/keep 0 | 唯一有崩溃恢复：SQLite 游标库 + 单实例锁 + reconcile 判定批次落地 | 双重默认关（`ingest.enabled` + 每 harness `enabled` 都 false）；支持 claude_code/codex/hermes/opencode/openclaw/cursor 适配器 |
| **F. [OpenViking Helper](./14-openviking-helper.md)** | 闭源桌面 App | — | — | — | 不在本文代码基线内 |

---

*各集成的安装、配置与排障以对应的单独集成页面为准；当本页与单独页面不一致时，以单独页面为准。*

## 参见

- [Agent 集成概览](./01-overview.md)
- [MCP 客户端](./06-mcp-clients.md)
- [MCP 集成指南](../guides/06-mcp-integration.md)
- [检索 API](../api/06-retrieval.md)
- [会话 API](../api/05-sessions.md)
- [鉴权](../guides/04-authentication.md)
