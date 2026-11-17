# OpenViking Memory for Kimi Code

为 Kimi Code 提供由 OpenViking 支撑的长期记忆。插件捕获模型实际看到的对话，在你提问时召回相关记忆，并通过 MCP 暴露服务端自己的工具。

## 与旧 Kimi 集成的关系

本目录是面向 Kimi Code 2.x 的独立分发、自包含插件。与旧适配器一样，它是社区维护的实现，不是 OpenViking 官方一方集成。它不修改、不导入、也不依赖旧的
`examples/agent-hook-plugin/hosts/kimicode` 适配器；后者面向 Kimi Code CLI
0.43.x，代码和文档都保持独立。

两个包的插件名都是 `openviking-memory`。宿主注册表同名只保留一个条目；安装本插件不会改动旧发行包的文件或文档。不要在同一宿主同时启用两者：两个 Kimi 记忆插件不能重复捕获同一会话。

`scripts/shared/` 下的模块由 OpenViking 公共的
`examples/memory-plugin-shared/lib/` 生成，不是旧适配器的副本。本插件的文档位于本目录和
`docs/{en,zh}/agent-integrations/22-kimi-code.md`；旧集成的文档独立存在，未做改动。

## 已验证与未验证的范围

**已验证**：hook payload、MCP 命名和 wire 协议取自 **Kimi Code 2.6.2** 内嵌源码。Windows Node 24 下用 fake 服务端覆盖捕获、召回、提交、恢复、分支、fork、MCP 和归档；最终 ZIP 解包后的六个入口与 MCP 握手也已跑通。

**本机实测（2026-11-16）**：通过宿主注册表从本地路径安装（doctor 无 failure）；宿主触发的 `UserPromptSubmit` 注入了记忆；真实 OV 0.5.5 对主会话与三个 subagent 完成记忆提取；`Stop`、`PreCompact`、`SessionEnd` 均在真实边界触发并提交已关闭轮次；MCP `health`/`search` 与直接召回注入可用；backfill 导入 1530 条主会话消息。

**未验证**：POSIX、远程/Web 环境和真实 turn-sliced fork；媒体按设计不支持。TUI 与 Desktop 共用同一 hook 路径，不作为两个独立验证面。POSIX 进程处理、三个结束事件与 fork/session 元数据由 2.6.2 源码确认——是宿主契约证据，不是运行时测试。

## 安装

用 Kimi Code 插件管理器安装本地检出的目录：

```text
/plugins install <本目录路径>
```

主线仓库发布 release 后，release ZIP 可以一条命令安装（tag 命名遵循仓库 `<包名>@<版本>` 惯例）：

```text
/plugins install https://github.com/volcengine/OpenViking/releases/download/kimi-code-memory-plugin@<version>/kimi-code-memory-plugin-<version>.zip
```

用 `/plugins info openviking-memory` 检查，重跑安装命令即可升级；卸载见[卸载](#卸载)。hook 需要宿主继承的 `PATH` 中有 Node.js **22.19+**——GUI 启动可能不读取 shell profile。MCP proxy 可回退到宿主自带 Node。

`pack.mjs` 也可以打出 ZIP 归档：

```bash
node ./scripts/pack.mjs --out openviking-memory.zip
```

归档根目录就是插件根目录——`kimi.plugin.json` 位于顶层——并包含 `scripts/`（含生成的 `scripts/shared/`）、`servers/`、`skills/` 和 `locales/`。测试、`node_modules` 与 `.git` 不会进入包内。发布已自动化：推送 tag `kimi-code-memory-plugin@<version>` 即构建 ZIP 并挂到该 release。

## 行为

| 事件 | 做什么 | 输出 |
| --- | --- | --- |
| `SessionStart` | 重放队列中的写入，扫描此前已结束的会话 | 无 |
| `UserPromptSubmit` | 先补齐上一轮，再注入 profile 与召回 | 一段文本 |
| `PreToolUse` | 拒绝把 `viking://` 路径交给本地文件工具 | deny JSON 或无 |
| `Stop` | 送达已闭合轮次，达到阈值则提交 | 无 |
| `PreCompact` | 补齐后提交，不受阈值限制 | 无 |
| `SessionEnd` | 先写结束标记，再补齐并提交 | 无 |

其余都是显式的：由模型调用 OpenViking MCP 工具，或由随包 Skills 说明工作流。

### 捕获

会话的每个 agent 各有一份 `wire.jsonl`。插件增量读取——只消费完整行、保留字节游标、单次最多 32 MB——并投影**活动分支**。wire 是一棵树：`agent.switched` 记录把 agent 切到基于更早某行的分支上，只有递归得到的那条链才是模型看到的内容。undo、clear、compaction 都按宿主自身语义处理；fork 只捕获 `forked` 标记之后的新内容。

只有当宿主会把某输入作为用户消息展示给模型时，它才成为 `user_query`。task 提示、system trigger、注入内容和 hook result 都不算。助手文本按顺序成为 `assistant_step`；工具调用留在助手消息里，其结果成为通过同一个 `tool_id` 关联的 `tool_transport` 消息。thinking、媒体二进制和压缩摘要都不捕获。

### 送达与提交

消息按服务端批次发送。只有服务端确认过的消息才算送达；进入本地队列的消息可以推进**交接游标**，但在 ledger 中仍是 queued，**不算**送达，且会阻止后续新消息抢先发送。非重试失败只记录一次，不会在每个事件上盲试——只有捕获配置或凭据来源的有效指纹发生变化才重试；在此之前该 agent 无法提交，因为归档一份有洞的对话比等待更糟。

服务端报告足够 pending token 时、或已送达足够多的完整轮次时、或到达 `PreCompact`/`SessionEnd`（总会提交已送达内容）时请求 `commit`。`keep_recent_count` 固定为 `0`：Kimi Code 自己持有上下文，服务端不需要保留尾部。

只落到本地队列的 commit 不算提交，只有后续重放报告服务端已接受才结算。

### 召回

首次成功提问会同时注入用户 profile 与服务端 skill 清单；之后只注入召回。`noAutoInject` 关闭 profile 与 skill 清单，`autoRecall` 关闭逐轮召回，`minQueryLength` 限制检索的最小长度。三者互相独立，也都不影响显式 MCP 调用。

召回失败时放行。服务不可达、超时或空结果都不产生任何文本——绝不会把错误信息写进你的提问。连续两次失败暂停 1 分钟，第三次暂停 5 分钟，成功后复位。

### 本地保护

交给 `Read`、`Glob`、`Grep`、`Edit`、`Write` 或 `ReadMediaFile` 的 `viking://` URI 会被拒绝，并给出能回答它的 MCP 工具。shell 命令不会被拦截：命令行里的 `viking://` 通常是数据。该宿主的 `PreToolUse` 不展示 `additionalContext`，所以由 Skills 说明 MCP 路径，而不是依赖提示。

## 配置

所有设置来自共享的 OpenViking 配置链，并按**会话所在目录**（而非插件目录）解析：

```text
OPENVIKING_* 环境变量 → workspace registry → .openviking/config.local.json
  → .openviking/config.json → ovcli.conf plugin.kimicode → ovcli.conf plugin
  → ov.conf kimicode → schema 默认值
```

凭据由 `resolveConnection()` 单独解析，且从不来自 workspace 文件。完整配置项、别名与默认值见[共享库 README](../memory-plugin-shared/README.md)；本页只说明 Kimi Code 特有的部分。

| 配置 | 本插件行为 |
| --- | --- |
| `enabled`、`autoCapture`、bypass | 与其他宿主一致；bypass 只影响 hook，不影响 MCP |
| `autoRecall`、`noAutoInject`、`minQueryLength` | 上述三个独立开关 |
| `recallQueryFilters` | 在长度判定前改写或丢弃召回查询，与 CC、Codex 一致 |
| `commitTokenThreshold`、`commitTurnThreshold` | 普通 `Stop` 何时提交 |
| `autoCommitOnCompact` | `false` 只跳过 `PreCompact` 的提交 |
| `captureMaxLength`、`captureToolMaxChars`、`captureFilters` | 由共享捕获清洗执行 |
| `profileTokenBudget`、`skillCatalog`、`skillCatalogTokenBudget` | profile 块 |
| `commitKeepRecentCount` | **不使用**；本插件固定发送 0 |
| `mcpEnabled` | **不使用**；请用 `/plugins mcp enable\|disable openviking-memory openviking` |
| `writePathAsync` | **不使用**；没有 hook 会 detach worker |

`captureToolResults` 在此路径没有消费者：工具证据始终捕获。`sessionStartMaxBytes`、resume/takeover/repo-context 相关配置和 `recallCompress*` 没有对应的宿主路径，不予宣传。

## 工具

MCP server 名为 `openviking`，宿主显示为 `mcp__plugin-openviking-memory_openviking__<tool>`。工具目录就是服务端 `tools/list` 返回的内容；本插件没有副本。

| 工作流 | Skill |
| --- | --- |
| 检索、记住、遗忘 | `openviking-memory` |
| 查找、读取、创建、安装服务端 Skills | `openviking-skills` |
| 检索适用的 Rule | `openviking-rule-search` |
| 检索并应用 Experience | `ov-experience-memory` |

Kimi Code 显示为 `openviking-memory:<skill>`。Skills 只描述服务端真实提供的工具，并说明当某个服务端版本缺少 `rule_search` 或 `add_skill` 时怎么办。

## 诊断与恢复

```bash
node ./scripts/doctor.mjs             # 完整报告
node ./scripts/doctor.mjs --offline   # 不发起任何网络请求
node ./scripts/doctor.mjs --json
```

doctor 检查 manifest 与 hook 注册、宿主插件注册表、`config.toml` 中残留的手工 hook、解析后的配置与凭据、会话布局、每个会话的阻塞状态、离线队列，以及在非 `--offline` 时的服务端健康。

永久失败的捕获、或被队列丢弃的消息，会有意阻止提交。两条命令可以解除阻塞，都需要会话 id：

```bash
node ./scripts/recover.mjs retry-commit --session <id> --agent <agentId>
node ./scripts/recover.mjs accept-loss  --session <id> --agent <agentId> --yes
node ./scripts/recover.mjs accept-loss  --session <id> --all-agents --yes
```

只要还有消息仅在队列中，`retry-commit` 就会拒绝：在未送达消息之上重试提交，正是这个阻塞要防止的洞。它会提示服务端已接受的 commit 可能被归档两次。`accept-loss` 用 tombstone 记录损失、把游标推进到损失之后，并保持 `hasCaptureGaps`；只有其余 agent 都已结算时才报告 `partial_loss`。两条命令都不联网、不删除 wire 文件、不删除记忆。

## 导入已有历史

hook 只从首次看到会话的时刻开始记忆，安装前的历史不会自动回放。需要导入时使用非 hook 入口：

```bash
node ./scripts/backfill.mjs --dry-run        # 只列出计划，不发请求、不写状态
node ./scripts/backfill.mjs                  # 导入所有符合条件的会话
node ./scripts/backfill.mjs --session <id>   # 只导入一个原生会话
```

没有插件状态文件的会话会从首条记录开始读，所有已闭合轮次走与 hook 相同的捕获管线，最后一轮强制提交，因此短会话也会触发提取。写入的状态文件就是 hook 使用的那个，之后普通捕获从游标继续。

已有状态的会话只沿游标追平，绝不重放已捕获前缀：OpenViking 没有服务端消息去重，重放会生成第二份记忆。损坏或不可读的状态会跳过，留给 `doctor`/`recover` 处理。`autoCapture=false` 与 `bypassSession` 同样生效；被 hook 排除的会话不会被该命令导入。命令在第一次写入前检查 `/health`，服务不可达时直接失败，不会把整段历史塞进离线队列。

活动会话的开放尾部不会发送，留给下一次 hook 或再次运行 backfill；在它闭合前，该会话的 commit 会被普通边界相同的守卫阻止，消息保持未提交。fork 只导入 `forked` 标记之后的内容，继承前缀不复制，因为父会话可能已经拥有它。删除插件状态文件后重跑会再次导入整段 transcript——状态文件是唯一的去重依据。非本地环境、legacy 或不兼容 wire 会被跳过。

每轮读取有上限并立即落盘，中断后可从游标续跑。`--timeout <seconds>`（默认 300）限制单个会话，`--limit <n>` 限制处理数量，`--json` 输出机器可读结果。

## 限制

- **至少一次，而非恰好一次。** 发送成功但状态写入失败的消息会被重放。稳定 wire id 能抑制多数重复；该集合有上限，超长会话可能重复一条旧消息。
- **已送达的轮次无法撤回。** undo、clear、分支切换只影响尚未送达的内容；服务端已有的消息会保留。
- **不自动导入安装前的历史。** 插件首次看到的会话从当前文件尾开始；导入过去需要显式运行 `scripts/backfill.mjs`。
- **不支持媒体。** 只捕获文本；图片、音频、视频不产生内容，也从不读取磁盘。
- **仅本地环境。** `environmentId` 非本地的会话会被跳过并留下诊断：它的 wire 不在这台机器上。
- **崩溃尾部依赖 sweep。** 只有同时存在结束标记、持有锁、且两次读取之间 wire 大小与 mtime 不变时，开放轮次才会被闭合。没有标记的崩溃要等宿主补写终态记录。
- **legacy wire 不做猜测。** 没有 `metadata` 行的 wire 会被报告并跳过，不会当作协议 1.5 解析。
- **每次 sweep 最多一次提交。** 积压会跨多次启动逐步消化，这是设计选择。

## 卸载

```text
/plugins disable openviking-memory
/plugins remove openviking-memory
```

按宿主文档，`remove` 只删除注册记录并保留插件文件。记忆、待发写入和其他插件都不受影响。

## 开发

```bash
npm test                       # 单元 + 契约 + e2e
node ./scripts/pack.mjs --check
```

`scripts/shared/` 中的共享模块与四份 skills 都是**生成物**：

```bash
node examples/memory-plugin-shared/sync.mjs
```

不要手改这些副本。修改 `examples/memory-plugin-shared/lib/` 或 `examples/skills/` 后重新生成。

测试使用临时 `KIMI_CODE_HOME`、临时 pending 目录，以及监听系统分配端口的 fake OpenViking 服务，因此不会接触真实会话或真实服务。
