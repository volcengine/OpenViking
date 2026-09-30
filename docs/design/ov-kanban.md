# ov-kanban：把结构化 handoff 做成 OpenViking 上的看板

| 项目 | 信息 |
| --- | --- |
| 状态 | 可用（skill 已落地，herdr 演练通过） |
| Skill | `agent-plugins/skills/ov-kanban/`（原名 `ov-tasks`，PR #5157） |
| 存储 | `viking://agent/kanban/<board>/<id>.md`；server 拒绝时兜底 `viking://resources/kanban`（见下） |
| 更新日期 | 2026-09-29 |

## 一句话

一个看板 = OV 里的一个目录，存放多个任务。一个任务 = 一个 markdown 文件：frontmatter 头（id/status/owner/board/updated）+ 固定七段正文（Goal/Context/Decisions/Progress/Next/Questions/Log）。文件本身就是 handoff，任何有 `ov` CLI 的 agent 读到就能继续，每轮结束整文件写回。过程详情折叠进 `archive/<id>.md`，不删除。没有其他状态。

## 改名原因（2026-09-29）

| 原因 | 说明 |
| --- | --- |
| 与 CLI 重名 | `ov task list` 指 server 的异步处理任务，`ov-tasks` 与之混淆 |
| 容器语义 | 看板存放多个任务；`tasks` 这个名字指不出容器 |
| `scope` 让位 | 原 `<scope>` 目录改称 `<board>`；`scope` 留给 roadmap 的 scoped kanban（看板声明自己关联的目录、分支） |

## 折叠，不压缩

长任务跨多个会话，状态必须在会话之外。压缩（摘要后丢弃原文）会丢掉下一轮需要的细节。ov-kanban 把过程折叠成层，agent 自上而下读，读到能回答问题的那一层为止。

| 层 | 内容 | 读取 |
| --- | --- | --- |
| 看板列表 | 每个看板一条摘要 | `ov ls <root>` |
| 看板视图 | 每个任务的 title/status/owner/updated | `ov grep '^(title\|status\|owner\|updated):'`，一次调用 |
| 任务文件 | 当前状态，足够继续工作 | `ov read` |
| archive | 从任务文件移出的原文：被替换的 Next/Decisions、10 行以外的 Log、超过一行的证据 | 按需 `ov grep` / `ov read` |

不变量：文本只能通过移入 archive 离开任务文件。PR #5157 的版本里 `Log` 只保留最近 10 行（其余丢弃）、archive 为可选，本次改为上述规则。

## 与 PowerContext / LoopX 的对应

| 概念 | PowerContext | LoopX | ov-kanban |
| --- | --- | --- | --- |
| 隔离边界 | Scope（server 生成的 opaque id） | Goal + project registry | `<root>/<board>/` 目录，`ov acl` 管权限 |
| 目标与完成边界 | Work Contract | Objective + Operating Contract | `## Goal` 验收清单 + `## Context` |
| 交接内容 | Prepared/Committed Handoff | ACTIVE_GOAL_STATE.md | 任务文件全文 |
| 待办 | — | Agent Todo / User Todo | `## Next` 清单 / `## Questions` + `status: needs_user` |
| 用户门 | Acknowledgement needs clarification | user gate | `status: needs_user`，agent 在会话内向用户提问并停下 |
| 证据 | Source / Task Outcome | evidence + Progress Ledger | `## Progress`（只写已验证条目，带证据）+ `## Log` |
| 所有权 | receiver acknowledgement | claim / lease | `owner` + `updated`；超过 2h 未更新视为可重新认领 |
| 历史 | 不可变 Revision + lineage | run history | `archive/<id>.md`，追加式；status 变化和折叠出的原文都写入 |
| 长期运行 | — | quota + scheduler + kernel | `references/loop.sh`：一轮一次 agent 调用，直到无可跑任务或轮数用尽 |
| 跨 agent | 同 Scope 内继续 Handoff | peer agents + writeback | 同一文件；agent id 写在 `owner` |
| 检索 | Handoff Report | `loopx status` | 看板视图（`ov grep`）、`ov find`、`ov ls` |

## 丢弃的概念与原因

| 丢弃 | 原因 |
| --- | --- |
| LoopX Kernel / Capability / Provider / Extension 分层 | OV 只负责存储与检索；执行方是任意 CLI agent。分层没有承载者，30 行 loop.sh 覆盖"一轮一 tick"。 |
| LoopX quota / 调度 / 自修复 | 用 `max_ticks` 一个数字替代。任务不会自旋，因为每轮必须写回且 needs_user 不可跑。 |
| LoopX typed claim / lease / 原子 writeback | OV write 没有 CAS。用 `owner`+`updated` 的 2h 约定替代，并在 skill 中写明。并发同一任务是已知上限。 |
| PowerContext 不可变 Revision / lineage / Acknowledgement / Task Outcome | 四个对象折叠为一个可变文件 + 追加式 archive。Acknowledgement = claim 时写 Log；Outcome = 最终 status + Progress。 |
| PowerContext Candidate 审核、Experience/Skill 家族 | 与 handoff 无关；经验沉淀走 OV 已有的 `remember`。 |
| PowerContext opaque scope id | OV URI 本身就是 scope 且带 ACL，不需要第二套 id。 |
| Dashboard / Lark 看板投影 | `ov ls`、`ov tui`、`ov find` 已够；需要时再做。 |
| retrieval tags 镜像 status/owner | 云端 0.4.17.3 的 write 不接受 `--tags`，需要第二次 `set-tags` 调用；frontmatter + `ov grep` 已能过滤，避免双写漂移。 |

## 存储位置：viking://agent/kanban 优先，探测失败兜底 viking://resources/kanban

| 事实 | 证据 |
| --- | --- |
| 云端 server 拒绝 `viking://agent/<非 skills>` 的 mkdir/write（v0.4.17.3 用 `agent/tasks` 验证；2026-09-29 `loop.sh` 探测 `agent/kanban` 同样退回兜底） | `PERMISSION_DENIED: viking://agent/{agent_id} is deprecated. Use viking://user/.../peers/{agent_id}` |
| main 分支 namespace 只把 `agent/skills` 视为内容目录 | `openviking/core/namespace.py:16` `_CONTENT_TYPES_BY_SCOPE["agent"] = {"skills": "skill"}`；`viking://agent/<其他目录>/x.md` 得到 `context_type=resource`，main 上无显式写保护 |
| `viking://resources/kanban` 在云端可用：create / read / append / grep 均通过 | 本文演练；原 `viking://resources/tasks` 已于 2026-09-29 用 `ov mv` 迁入 |

开源 main 已允许在 `viking://agent` 下新建目录。skill 与 loop.sh 每次先 `ov stat`/`ov mkdir viking://agent/kanban` 探测，失败才退回 `viking://resources/kanban`；`OV_KANBAN_ROOT` 可强制指定。协议与 root 无关。

## `ov compile` 接入（下一步）

目标：`ov compile --from <root>/<board> --to <root>/<board> --skill viking://agent/skills/ov-kanban`，由 VikingBot 按 skill 执行一轮并把任务文件写回。

| 阻塞点 | 位置 |
| --- | --- |
| `--to` 只允许 resource/memory/skills 目录 | `openviking/service/compile_service.py:464` `_validate_target`（`viking://agent/kanban/<board>` 需放行） |
| `--from` 必须是目录 | `compile_service.py:446`，单任务文件需放行文件源，或约定 from = board 目录 + `--instruction "task <id>"` |
| Compile agent 的产物是 wiki bundle（`submit_wiki_bundle`） | 需要一个"原地改写源文件"的输出工具；改动在 VikingBot 侧 |

建议顺序：先把 `ov-kanban` 用 `ov add-skill agent-plugins/skills/ov-kanban -p viking://agent/skills` 装到 OV，再改 compile 的两处校验，最后补写回工具。

## Roadmap

任务 DAG（`blocked_by`）、scoped kanban、会话启动召回、claim-aware loop、原子 claim、`ov compile`，见 [skill README](https://github.com/volcengine/OpenViking/blob/main/agent-plugins/skills/ov-kanban/README.md#roadmap)。

## 演练记录（2026-09-18，herdr，board `demo`）

演练时 skill 名为 `ov-tasks`，存储在 `viking://resources/tasks/demo`。

| 轮 | 执行者 | 动作 | 结果 |
| --- | --- | --- | --- |
| A | codex（`codex exec`） | 认领 `wc-top`，只做 Next 第一项，验证后 handoff（status open、owner 清空） | 文件写回正确；archive 记录 open→in_progress→open；脚本可运行 |
| B | claude（`claude -p`） | 读 A 的 handoff 继续，补测试和 README | status done，Goal 三项全勾且带证据；本地复跑 `pytest` 4 passed |
| C | `loop.sh demo 4` 驱动 codex | tick1 认领 `makefile`：写好 Makefile，但本机 `make` 被 Xcode license 挡住 → `needs_user` 并写明问题；tick2 认领 `report-format`：输出格式未定 → `needs_user`；tick3 无可跑任务，循环停止 | 两次 user gate 都是真实阻塞，没有假 pass；循环按预期收敛 |

演练中修的两个问题：`write --mode create` 需要 `archive/` 目录先存在（skill 已写明）；`ov` 输出 JSON 前有一行 `cmd:`，loop.sh 改为只解析 JSON 行。另外 codex 侧的 OV MCP `write` 会在 15s 超时后仍写入成功，skill 已注明优先用 CLI 并回读确认。
