# ov-tasks：把结构化 handoff 做成 OpenViking 上的任务板

| 项目 | 信息 |
| --- | --- |
| 状态 | 可用（skill 已落地，herdr 演练通过） |
| Skill | `agent-plugins/skills/ov-tasks/` |
| 存储 | `viking://agent/tasks/<scope>/<id>.md`；server 拒绝时兜底 `viking://resources/tasks`（见下） |
| 更新日期 | 2026-09-18 |

## 一句话

一个任务 = OV 里的一个 markdown 文件：frontmatter 头（id/status/owner/scope/updated）+ 固定七段正文（Goal/Context/Decisions/Progress/Next/Questions/Log）。文件本身就是 handoff，任何有 `ov` CLI 的 agent 读到就能继续，每轮结束整文件写回。没有其他状态。

## 与 PowerContext / LoopX 的对应

| 概念 | PowerContext | LoopX | ov-tasks |
| --- | --- | --- | --- |
| 隔离边界 | Scope（server 生成的 opaque id） | Goal + project registry | `<root>/<scope>/` 目录，`ov acl` 管权限 |
| 目标与完成边界 | Work Contract | Objective + Operating Contract | `## Goal` 验收清单 + `## Context` |
| 交接内容 | Prepared/Committed Handoff | ACTIVE_GOAL_STATE.md | 任务文件全文 |
| 待办 | — | Agent Todo / User Todo | `## Next` 清单 / `## Questions` + `status: needs_user` |
| 用户门 | Acknowledgement needs clarification | user gate | `status: needs_user`，agent 在会话内向用户提问并停下 |
| 证据 | Source / Task Outcome | evidence + Progress Ledger | `## Progress`（只写已验证条目，带证据）+ `## Log` |
| 所有权 | receiver acknowledgement | claim / lease | `owner` + `updated`；超过 2h 未更新视为可重新认领 |
| 历史 | 不可变 Revision + lineage | run history | 可选 `archive/<id>.md`，只在 status 变化时追加 |
| 长期运行 | — | quota + scheduler + kernel | `references/loop.sh`：一轮一次 agent 调用，直到无可跑任务或轮数用尽 |
| 跨 agent | 同 Scope 内继续 Handoff | peer agents + writeback | 同一文件；agent id 写在 `owner` |
| 检索 | Handoff Report | `loopx status` | `ov grep '^status: open'`、`ov find`、`ov ls` |

## 丢弃的概念与原因

| 丢弃 | 原因 |
| --- | --- |
| LoopX Kernel / Capability / Provider / Extension 分层 | OV 只负责存储与检索；执行方是任意 CLI agent。分层没有承载者，30 行 loop.sh 覆盖"一轮一 tick"。 |
| LoopX quota / 调度 / 自修复 | 用 `max_ticks` 一个数字替代。任务不会自旋，因为每轮必须写回且 needs_user 不可跑。 |
| LoopX typed claim / lease / 原子 writeback | OV write 没有 CAS。用 `owner`+`updated` 的 2h 约定替代，并在 skill 中写明。并发同一任务是已知上限。 |
| PowerContext 不可变 Revision / lineage / Acknowledgement / Task Outcome | 四个对象折叠为一个可变文件 + 可选追加式 archive。Acknowledgement = claim 时写 Log；Outcome = 最终 status + Progress。 |
| PowerContext Candidate 审核、Experience/Skill 家族 | 与 handoff 无关；经验沉淀走 OV 已有的 `remember`。 |
| PowerContext opaque scope id | OV URI 本身就是 scope 且带 ACL，不需要第二套 id。 |
| Dashboard / Lark 看板投影 | `ov ls`、`ov tui`、`ov find` 已够；需要时再做。 |
| retrieval tags 镜像 status/owner | 云端 0.4.17.3 的 write 不接受 `--tags`，需要第二次 `set-tags` 调用；frontmatter + `ov grep` 已能过滤，避免双写漂移。 |

## 存储位置：viking://agent/tasks 优先，探测失败兜底 viking://resources/tasks

| 事实 | 证据 |
| --- | --- |
| 云端 server (v0.4.17.3) 拒绝 `viking://agent/tasks` 的 mkdir/write | `PERMISSION_DENIED: viking://agent/{agent_id} is deprecated. Use viking://user/.../peers/{agent_id}` |
| main 分支 namespace 只把 `agent/skills` 视为内容目录 | `openviking/core/namespace.py:16` `_CONTENT_TYPES_BY_SCOPE["agent"] = {"skills": "skill"}`；`classify_uri("viking://agent/tasks/x.md")` 得到 `context_type=resource`，main 上无显式写保护 |
| `viking://resources/tasks` 在云端可用：create / read / append / grep / set-tags 均通过 | 本文演练 |

开源 main 已允许在 `viking://agent` 下新建目录。skill 与 loop.sh 每次先 `ov stat`/`ov mkdir viking://agent/tasks` 探测，`PERMISSION_DENIED` 才退回 `viking://resources/tasks`；`OV_TASKS_ROOT` 可强制指定。协议与 root 无关。

## `ov compile` 接入（下一步）

目标：`ov compile --from <root>/<scope> --to <root>/<scope> --skill viking://agent/skills/ov-tasks`，由 VikingBot 按 skill 执行一轮并把任务文件写回。

| 阻塞点 | 位置 |
| --- | --- |
| `--to` 只允许 resource/memory/skills 目录 | `openviking/service/compile_service.py:464` `_validate_target`（`viking://agent/tasks/<scope>` 需放行） |
| `--from` 必须是目录 | `compile_service.py:446`，单任务文件需放行文件源，或约定 from = scope 目录 + `--instruction "task <id>"` |
| Compile agent 的产物是 wiki bundle（`submit_wiki_bundle`） | 需要一个"原地改写源文件"的输出工具；改动在 VikingBot 侧 |

建议顺序：先把 `ov-tasks` 用 `ov add-skill agent-plugins/skills/ov-tasks -p viking://agent/skills` 装到 OV，再改 compile 的两处校验，最后补写回工具。

## 演练记录（2026-09-18，herdr，scope `demo`）

| 轮 | 执行者 | 动作 | 结果 |
| --- | --- | --- | --- |
| A | codex（`codex exec`） | 认领 `wc-top`，只做 Next 第一项，验证后 handoff（status open、owner 清空） | 文件写回正确；archive 记录 open→in_progress→open；脚本可运行 |
| B | claude（`claude -p`） | 读 A 的 handoff 继续，补测试和 README | status done，Goal 三项全勾且带证据；本地复跑 `pytest` 4 passed |
| C | `loop.sh demo 4` 驱动 codex | tick1 认领 `makefile`：写好 Makefile，但本机 `make` 被 Xcode license 挡住 → `needs_user` 并写明问题；tick2 认领 `report-format`：输出格式未定 → `needs_user`；tick3 无可跑任务，循环停止 | 两次 user gate 都是真实阻塞，没有假 pass；循环按预期收敛 |

演练中修的两个问题：`write --mode create` 需要 `archive/` 目录先存在（skill 已写明）；`ov` 输出 JSON 前有一行 `cmd:`，loop.sh 改为只解析 JSON 行。另外 codex 侧的 OV MCP `write` 会在 15s 超时后仍写入成功，skill 已注明优先用 CLI 并回读确认。
