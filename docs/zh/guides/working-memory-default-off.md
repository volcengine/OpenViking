# Working Memory 默认关闭

OpenViking 现在默认 `working_memory.enabled=false`。commit 仍会保存原始消息并调度长期记忆抽取，但除非显式开启 WM，否则不会生成归档 overview 或保留 checkpoint 摘要。这个开关不影响长期记忆、recall、profile 或工具。

## 升级服务端与插件

升级 OpenViking Python 包**不会**更新已安装在各 agent 宿主中的插件副本。请按各插件的安装说明逐个更新，然后重启对应宿主。如果某个会话依赖 OV 历史，请先完成这一步，再把它切换到宿主原生的历史管理。旧版接管（takeover）插件不支持新的默认值，服务端也无法绕过它们的轮询问题。

| 集成 | 新默认行为 | 显式开启 |
| --- | --- | --- |
| Claude Code、Codex / TraeCode CLI、OpenCode | resume/compact 时不自动注入归档；以宿主历史为准 | `resumeArchiveInject: true` 启用补充的归档注入，不会生成摘要 |
| Pi 官方扩展 | 使用 Pi 原生 compaction；不接管，也不在 resume 时注入归档 | `takeoverEnabled: true`；commit 时显式请求 WM |
| OpenClaw | `contextManagementMode: "native"`：保留宿主消息，压缩交给宿主 | `contextManagementMode: "openviking"`；commit 时显式请求 WM |
| VikingBot | `session_context_enabled: true`：由 OV 管理历史与压缩，commit 时显式请求 WM | 设置 `session_context_enabled: false` 使用 VikingBot 现有的本地模式 |
| LangChain / LangGraph | 中间件只做捕获和 recall，不再拉取 OV 会话历史 | 旧版 OV 历史适配器需要开启 WM，并由应用显式决定 |

已有的显式插件配置和环境变量仍优先于默认值。如果某个安装之前开启了接管或注入，要切换时请删除这些配置或设为 false，安装器不会覆盖你的选择。Hermes、WorkBuddy、AstrBot 等其他捕获/recall 类集成沿用服务端默认值，它们的宿主历史不受此变更影响。

Pi 的实验性上下文管理扩展不在本次 WM 关闭适配范围内，请勿把它当作 WM 关闭后的历史后端。

## 已持久化的策略与队列中的任务

旧版序列化在 `working_memory` 为 true 时会省略该字段。因此，即使通过 Studio 或常规的用户/会话保存 API 显式开启，该字段也可能已丢失。现在，被选中的策略缺少该字段时一律视为 **false**，无法还原用户当初的意图，需要的话请重新显式开启 WM。原始配置中仍保留 true 的策略，在被选中时保持不变。策略优先级仍然是整份策略选择，缺失字段不会从低优先级策略补齐。

新的序列化始终包含 WM 布尔值。新的队列快照包含 `memory_policy_version: 1`。没有版本号的旧队列/恢复快照，在缺少 WM 字段时仍沿用提交时的 WM=true 语义，因此这些已提交的任务在升级后仍可能生成摘要。这不会把已保存的用户/会话策略标记为已开启。如果不能接受升级后仍有 WM 生成，请在升级前先排空旧任务。

## 按次显式生成

`POST /api/v1/sessions/{id}/commit` 接受 `enable_working_memory`：

- 省略或 `null`：使用解析后的会话/用户/服务端策略。
- `true` 或 `false`：**仅覆盖本次 commit 的 WM**，不改变 `self`、`peer`、`memory_types` 或已保存的策略。
- 字符串和数字会被拒绝。响应中的 `effective_enable_working_memory` 反映实际生效的 commit 策略。

更新后的接管类集成在依赖摘要之前，要求拿到明确的 true 确认。缺少确认、WM 已关闭，或归档已完成/失败但没有可用摘要时，保留宿主原生历史。Pi 在轮询时检查归档状态，到达终态边界时再读取一次，然后立即回退到原生 compaction，不会等到超时。

## 历史 API 的返回内容

`GET /sessions/{id}/context` 返回的是有边界的 prompt 视图，不是完整的对话记录。关闭生成策略后，仍可读取已有的合格摘要。一旦更新的 WM 关闭归档完成，其边界会截断该视图：overview 为空，只保留 retained/active 消息和更新的待处理原始消息。它不会恢复更早的摘要，也不会回放所有归档。WM 关闭下的正常完成不算归档失败。

`GET /sessions/{id}/archives/{archive_id}` 对已完成的 WM 关闭归档返回原始 `messages`，其中 `overview: ""`、`abstract: ""`。显式的归档工具仍可使用。待处理、失败、缺失或损坏的归档保持原有的错误行为。Recall 内部仍可使用合格的旧摘要；关闭生成不会删除已存储的历史。

## 应用与旧会话

LangChain 请使用 `with_openviking_memory(..., history_factory=...)` 并配合宿主的历史提供者。未提供 provider 的旧版 `with_openviking_context` 会警告它依赖 OV 历史。中间件默认 `include_session_context=False`。请把它放在原生 summarization 中间件之前，使原始消息在框架替换它们之前被捕获。消息捕获水位线保存在 graph state 中。可运行的[原生历史示例](https://github.com/volcengine/OpenViking/blob/main/examples/langchain-langgraph/langgraph/middleware/native_history.py)及其固定版本的依赖，演示了 LangChain summarization 配合 SQLite checkpointer 的用法。

VikingBot 是“默认使用宿主历史”的例外：它仍然使用 OV Working Memory 管理上下文和压缩。它的会话上下文 commit 会显式发送 `enable_working_memory: true`，包括已保存策略关闭了 WM 的既有会话。它保持现有的 commit-and-clear 流程；之后的上下文读取使用 OV 的 overview/checkpoint 和保留的消息，生成仍在进行时则使用待处理归档中的消息。如果服务端未确认 WM 生成，该 commit 会被视为失败，本地历史保留以便重试。此变更不会替换 VikingBot 的本地压缩。

部分由 OV 管理的旧会话已不再有完整的宿主对话记录。请停止写入，并使用[历史导出工具](https://github.com/volcengine/OpenViking/blob/main/examples/session-history-export/README.md)，通过宿主支持的文件/上下文入口准备一个新的宿主会话。继续之前，请确认宿主拥有必要的历史。仅导出**不等于**已导入或已验证的迁移；该工具不会修改宿主的私有对话记录、不会更改策略，也不会删除源数据。如果原始来源不完整，不要自动切换依赖历史的会话。

## 诊断与回滚

OpenClaw 的健康检查仅在 commit 确认 WM=true 时才要求有 overview。Pi 的 live E2E 默认使用原生历史；如需测试显式接管，请设置 `E2E_TAKEOVER=1`。测试 WM/checkpoint 的用例需要显式开启。

重新开启 WM 只影响之后的 commit，不会为之前的归档补建缺失的摘要。回滚服务端行为时，请保留原始备份和更新后的插件。旧版客户端可能重新应用旧的接管边界，不得悄悄替换已迁移到宿主历史的会话。
