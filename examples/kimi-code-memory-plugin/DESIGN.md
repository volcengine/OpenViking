# Kimi Code OpenViking 记忆插件设计

状态：**已实现，本机 Windows 已验收，POSIX 与远程环境待验收**。当前目录已包含可运行插件、共享运行时扩展和隔离测试；已在 Windows Node.js 24 下完成 fake OpenViking 行为测试、hook contract 和最终 ZIP 解包 smoke，并在真实 Kimi Code 2.6.2 与 OpenViking 0.5.5 上完成宿主安装、捕获、提交、提取与召回（证据见附录 B）。本文记录实现契约与验收边界，不把隔离测试等同于端到端验收。

本文依据：

- **宿主源码**：本机 `kimi.exe --version` 为 **2.6.2**，直接读取 PE `.rdata` 中保留的 JavaScript 及原始 TypeScript 模块路径；定位方法见附录 A。
- **共享源码**：当前仓库的 `docs/zh/agent-integrations/18-plugin-development.md`、`examples/memory-plugin-shared/` 和 OpenViking 服务端源码。
- **设计决策**：事件、预算、状态与恢复策略由第 11 章验收（证据见附录 B）。
- **仍待实测**：提取质量与延迟、POSIX 与远程/Web 环境、真实 turn-sliced fork、ZIP URL 安装与 reload/remove；完整边界见附录 B。

## 1. 目标、范围与身份

### 1.1 目标

插件为 Kimi Code 提供由 OpenViking 支撑的长期记忆：

- **自动捕获**：从每个 agent 的 `wire.jsonl` 增量读取活动分支，保留用户输入、助手文本和工具证据。
- **自动召回**：在用户提交问题时注入相关记忆；首次成功提交时同时注入用户 profile 和服务端 skill 清单。
- **主动工具**：通过本地 stdio MCP proxy 暴露 OpenViking 服务端实际提供的工具。
- **显式工作流**：交付记忆、Rule、Skill 和 Experience 相关的共享 Skills。
- **本地保护**：拒绝把 `viking://` URI 交给本地文件工具。
- **可恢复交付**：提供幂等安装、卸载、诊断、离线重放、退出补偿和版本检查。

### 1.2 非目标

首版不做以下事情：

- 不实现第二套配置、鉴权、召回、发送、重试、MCP transport 或 doctor。
- 不读取媒体二进制；纯图片、音频或视频输入没有文本时不产生消息。
- 不自动迁移安装插件之前的全部历史会话；显式 `scripts/backfill.mjs` 只对没有插件状态的会话从头导入，已有状态的会话仅沿游标追平，原因见 §6.5。
- 不支持非本地 `environmentId` 的 wire；远程/Web 环境需要另行验证记录位置。
- 不使用 `systemPrompt`、`systemPromptPath`、`sessionStart.skill` 或 `skillInstructions` 注入记忆。
- 不启动脱离 hook 生命周期的后台 commit worker。
- 不承诺撤回已经发送或已经进入 pending queue 的撤销轮次。
- 不把 peer 当作不同认证用户之间的授权隔离；授权由服务端身份负责。

### 1.3 固定身份

| 项目 | 约定 |
| --- | --- |
| 宿主基线 | Kimi Code **2.6.2**；这是当前唯一核对版本，不宣称兼容更旧版本 |
| 首要平台 | Windows；POSIX 分支有源码依据，但插件仍需单独实测 |
| Hook 运行时 | PATH 中的 Node.js **22.19+**；MCP 可使用宿主 Node 回退 |
| 插件目录 | `examples/kimi-code-memory-plugin/` |
| manifest / 安装 ID | `openviking-memory` |
| npm 包名 | `@openviking/kimi-code-memory-plugin` |
| 安装位置 | `${KIMI_CODE_HOME:-~/.kimi-code}/plugins/managed/openviking-memory/` |
| 注册表 | `${KIMI_CODE_HOME:-~/.kimi-code}/plugins/installed.json` |
| 共享 harness ID | `kimicode` |
| OpenViking session 前缀 | `kc-` |
| 独立包首个目标版本 | `0.6.0`；必须高于旧 Kimi 集成（`agent-hook-plugin/hosts/kimicode`）当时的版本，不能重新从 `0.1.0` 开始 |

## 2. 宿主契约

本章均为 2.6.2 内嵌源码所确认的事实；字段以真实 hook payload 与最终安装包复核为准。

### 2.1 插件与 manifest

宿主支持插件根部的 `kimi.plugin.json` 和 `.kimi-plugin/plugin.json`；本设计只使用前者。

关键规则：

- `name` 必须匹配 `/^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:@[A-Za-z0-9_-]+)?$/`。
- `hooks` 必须是**内联数组**。每项 strict schema 只有 `event`、`matcher`、`command`、`timeout`；`timeout` 为整数秒，1–600，默认 30。
- manifest hook 不接受 `cwd` 或 `env`。宿主加载插件时自行把 `cwd` 设为插件根，并加入 `KIMI_CODE_HOME`、`KIMI_PLUGIN_ROOT`。
- `mcpServers` 必须是**内联对象**，不能写成另一个 JSON 文件的路径。
- stdio MCP 的 `command` 可以是 PATH 命令或以 `./` 开头的插件内路径；未指定的 `cwd` 默认是插件根。
- MCP `command: "node"` 在原生二进制中由 `__plugin_run_node` 回退到宿主运行时；hook 命令没有这项回退。
- `skills` 使用 `./` 相对路径且不能逃逸插件根。每个目录 Skill 仍需合法的 `name`、`description` frontmatter。
- `systemPrompt`、`sessionStart.skill` 等字段虽然存在，但不适合按会话召回的记忆插件，本设计不用。
- `tools`、`apps`、`inject`、`configFile`、`config_file`、`bootstrap` 不是可用运行时扩展点。

目标 manifest：

```json
{
  "name": "openviking-memory",
  "version": "0.6.0",
  "description": "Long-term semantic memory for Kimi Code, powered by OpenViking.",
  "author": { "name": "OpenViking", "url": "https://github.com/volcengine/OpenViking" },
  "homepage": "https://github.com/volcengine/OpenViking",
  "license": "Apache-2.0",
  "keywords": ["memory", "openviking", "kimi-code"],
  "hooks": [
    { "event": "SessionStart", "command": "node ./scripts/session-start.mjs", "timeout": 10 },
    { "event": "UserPromptSubmit", "command": "node ./scripts/user-prompt.mjs", "timeout": 20 },
    { "event": "PreToolUse", "command": "node ./scripts/uri-guard.mjs", "timeout": 5 },
    { "event": "Stop", "command": "node ./scripts/stop.mjs", "timeout": 10 },
    { "event": "PreCompact", "command": "node ./scripts/pre-compact.mjs", "timeout": 15 },
    { "event": "SessionEnd", "command": "node ./scripts/session-end.mjs", "timeout": 10 }
  ],
  "mcpServers": {
    "openviking": {
      "transport": "stdio",
      "command": "node",
      "args": ["./servers/mcp-proxy.mjs"]
    }
  },
  "skills": "./skills/",
  "interface": {
    "displayName": "OpenViking Memory",
    "shortDescription": "Long-term semantic memory for Kimi Code",
    "longDescription": "Recall relevant memories, capture local session history, and use OpenViking tools.",
    "developerName": "OpenViking",
    "websiteURL": "https://github.com/volcengine/OpenViking"
  }
}
```

不在任何 hook 上写 `matcher: "*"`。`matcher` 是 JavaScript 正则，不是 glob；`*` 是无效正则，结果是永不匹配。首版全部省略 matcher，在入口内做精确判断。

### 2.2 生命周期事件

宿主定义 20 种 hook 事件，本插件只注册以下 6 种。

| 事件 | 宿主语义与关键输入 | 输出是否被消费 | 本插件用途 |
| --- | --- | --- | --- |
| `SessionStart` | 会话创建；`source` 为 `fork`、`reclaimed` 时跳过。含 `source`、`session_title`、`model`、`profile` | 否，返回值被丢弃 | 有界 pending 重放和退出补偿；profile 不在这里注入 |
| `UserPromptSubmit` | 仅 `promptMessage.origin.kind === "user"`；含 ContentPart 数组 `prompt`、`is_steer`，没有 prompt ID 或 agent ID | 是 | 注入 profile 与召回文本；不能阻断正常提问 |
| `PreToolUse` | 工具执行前；含 `tool_name`、`tool_input`、`tool_call_id` | 是，可拒绝 | 仅做 `viking://` 文件路径保护 |
| `Stop` | 最终 step 完成后；`finishReason` 为 `tool_calls`、`filtered` 或仍有 pending request 时不触发。输入只有 `stop_hook_active` 等会话事实 | 是，拒绝原因可让 Agent 继续 | 捕获稳定轮次并按阈值提交；永不返回拒绝原因 |
| `PreCompact` | 压缩前等待完成；含 `trigger`、`token_count` | 否 | 补齐稳定记录并提交 |
| `SessionEnd` | 会话关闭前等待完成，`reason === "reclaim"` 时跳过；发生在 Agent drain 之前 | 否 | 写结束标记、补齐并提交；崩溃或强杀不能保证触发 |

补充约束：

- `Stop` 是**轮次边界**，不是会话结束。
- `StopFailure`、`Interrupt` 是 fire-and-forget，不能作为可靠写入点；失败和取消从 wire 的终态记录恢复。
- `PostToolUse` 的普通输出被截为 2,000 字符，且同样是 fire-and-forget，因此不是 capture 数据源。
- 子代理 hook 没有可直接映射到 wire 目录的 agent ID；子代理捕获统一枚举 `agents/*/wire.jsonl`，不从 hook payload 猜测身份。
- fork 或 reclaimed 会话可以合法地没有 `SessionStart`，所有入口都必须能惰性初始化。

### 2.3 通用输入、匹配与输出

stdin 是 JSON。公共字段包括 `hook_event_name`、`session_id`、`cwd`、`client_type`；agent 事件还包含 `session_title`。字段由 camelCase 转为 snake_case。

匹配与并发：

- `matcher` 对工具名、session source、关闭原因、压缩触发器或拼接后的文本做 JavaScript 正则测试。
- 空 matcher 匹配全部；非法正则不匹配。
- 同一事件按 `cwd + "\0" + command` 去重，不同命令并行执行。
- 本插件每个事件只有一个命令，避免同一状态被两个并行入口竞争。

退出码和 stdout：

| 情况 | 宿主行为 |
| --- | --- |
| exit 0、空 stdout | 合法空结果 |
| exit 2 | 阻断，trim 后的 stderr 是原因 |
| 其他非零退出 | 执行错误，按允许处理并报告 |
| exit 0、普通文本 stdout | `UserPromptSubmit` 可把它作为注入文本 |
| exit 0、合法 JSON | 读取 `message` 或 `hookSpecificOutput` |
| 超时或中止 | 丢弃注入，按允许处理 |

`hookSpecificOutput` 被消费的字段只有 `message`、`permissionDecision`、`permissionDecisionReason`。`permissionDecision: "deny"` 可拒绝 `PreToolUse`；`additionalContext` 不会成为本宿主的有效注入通道。

`UserPromptSubmit` 的普通 stdout 或结构化 `message` 会被宿主包成 `<hook_result hook_event="UserPromptSubmit">`，加入**同一个用户消息**的 hook parts，而不是另造一条系统消息。`SessionStart`、`PreCompact`、`SessionEnd` 不消费 stdout；stdout 也绝不用于日志。

### 2.4 进程与时间限制

- hook 以 `shell: true` 启动，插件 manifest 中的相对命令以插件根为 cwd。
- 环境变量继承宿主进程，并覆盖插件注入值；因此 hook 与 MCP 必须通过同一配置解析链取得 OpenViking 连接。
- Windows 默认 `detached: false`、`windowsHide: true`；超时通过 `taskkill /T /F /PID <pid>` 杀进程树。
- POSIX 默认 `detached: true`；`kill()` 使用负 PID 向**进程组**发送 SIGTERM，随后 SIGKILL。
- 每个网络步骤使用所属阶段预算的剩余时间，不能为每一步重新获得完整 timeout；`user-prompt` 的 capture 与 recall 各受所在窗口约束（§4.1）。

结论：首版所有写入都在 hook 内同步完成；不能依赖 detach 逃避宿主回收。

### 2.5 会话目录与定位

```text
${KIMI_CODE_HOME:-~/.kimi-code}/
├── plugins/
│   ├── installed.json
│   └── managed/openviking-memory/
├── session_index.jsonl
└── sessions/<workspace-id>/<session-id>/
    ├── state.json
    └── agents/<agent-id>/
        ├── wire.jsonl
        ├── tasks/<task-id>/output.log
        └── tool-results/*.txt
```

`session_index.jsonl` 是 append-only：

- 普通记录包含 `sessionId`、`sessionDir`、`workDir`。
- 删除会追加 `{sessionId, deleted: true}`。
- 同一 session 可出现多次，Windows 路径分隔符也可能混用。

定位器必须：

1. 从尾部寻找该 session 的**最新记录**；若它是 `deleted: true` 则返回 `missing`，不能复活更早的普通条目。
2. 规范化路径，在 Windows 上大小写不敏感地比较。
3. 对 `sessions/` 根与候选目录分别做 realpath，确保解析后仍位于根内并拒绝 symlink 逃逸，再验证 `state.json` 的 session ID。
4. 无法区分多个候选时返回 `ambiguous`，不能任选一个。
5. 仅当索引不可用时有界扫描 `<workspace>/<session-id>`；`missing`、`unreadable`、`ambiguous` 与“没有新消息”必须分开。

`state.json` 当前为版本 2。适配器只读取必要字段：`id`、`cwd`、`forkedFrom`、`environmentId`、`agents`，以及每个 agent 的 `type`、`homedir`、`labels`。agent ID 不能假定为 `agent-N`；父子关系优先读 `labels.parentAgentId`，兼容顶层 `parentAgentId`，profile 读 `labels.profileName`。

session 的 `environmentId` 缺省时按宿主逻辑视为 local；非本地环境跳过 capture 并给 doctor 留下明确诊断。wire 优先使用 agent `homedir`，缺省才回退到 `agents/<agent-id>/`；realpath 后必须仍位于该 session 的受控 agents 根内，避免 metadata 把 reader 引向任意本机文件。

### 2.6 Wire 协议与记录

首条 `metadata` 在 2.6.2 写入 `protocol_version: "1.5"`、`min_protocol_version: "1.5"`。reader 在解码前检查 `min_protocol_version`，按数字段比较；不兼容时保留游标、停止该文件 capture 并报告，不能强行解析。首条不是 metadata 的文件按 legacy 处理，不能自行赋予 1.5 语义，首版只允许显式迁移或专用兼容 decoder。协议兼容但未知的记录类型按非对话控制记录跳过并计数，不能凭名称相似猜成用户或助手消息。

捕获只依赖以下记录：

| 记录 | 含义与使用方式 |
| --- | --- |
| `turn.prompt` | 打开逻辑轮次；读取 `turnId`、`promptId`、`input`、`origin`、`trigger`、`time` |
| `turn.steer` | steer 输入。按宿主 normalization 关闭当前逻辑段并开始新段；`messageId` 或首个 `promptId` 用作稳定身份 |
| `context.append_loop_event` | 读取 `step.begin/end`、`content.part`、`tool.call`、`tool.result` |
| `turn.ended` | 轮次终态；`completed` 正常闭合，`failed`、`cancelled` 及其他异常终态按 partial 捕获已有内容 |
| `turn.cancel` | 取消请求标记，不单独伪造完整终态 |
| `agent.switched` | 活动分支边；含 `branch`、`base.{branch,line}`、`legacyUndoLine` |
| `context.undo` | 旧式 undo 计数；被 `legacyUndoLine` 配对时不能重复应用 |
| `context.undone` | 诊断及丢弃本地未交接轮次的辅助信息；`fromTurnId` 不是全局游标 |
| `context.clear` | 清除尚未交接的本地投影，保持同一个 OpenViking session |
| `context.apply_compaction` | 游标与 undo 锚点控制；摘要不是新对话 |
| `forked` | 标识 session fork 的继承边界；用于关联父会话并区分继承历史与 fork 后的新内容 |

`prompt.steered` 属于 loop journal/事件流，不代替 wire 中的 `turn.steer`。`prompt.completed`、`prompt.aborted` 只作诊断，不作为 capture 的主要闭合条件；尤其不能要求 `turn.cancel` 与 `prompt.aborted` 同时出现才允许恢复。

`context.append_message` 不作为独立 capture 来源。协议 1.5 的对话由 `turn.prompt`、`turn.steer` 和 loop events 重建；该通道还含 injection、skill activation、hook result 等重复或非用户内容。旧式只有 context 的历史会话属于显式迁移工作，不在首版自动 capture 范围内。

loop event 规则：

- `step.begin`、`step.end` 通过 `uuid` 配对；文本和工具调用通过 `stepUuid` 归属，按 `block` 排序。
- `content.part` 只保留 `text`，丢弃 `think`。
- `tool.call` 使用 `toolCallId`、`name`、`args`。
- `tool.result` 没有自己的 turn/step，通过 `toolCallId` 关联调用；缺失结果不伪造。
- 文件尾半行、开放 step、缺少 `turn.ended` 都是可重读状态，不是“无数据”或损坏。

## 3. 接入形态与模块边界

### 3.1 为什么是独立分发插件

按开发指南 §2.1，本任务采用“独立插件目录，入口调用共享运行时”。决定性原因是**独立分发与 Windows 可用性**：插件要作为单独 ZIP 或本地目录通过宿主的 `/plugins install` 安装，不能要求 Windows 用户先执行 OpenViking 的 `install.sh`，也不能先安装整个 `agent-hook-plugin`。

“独立分发”指分发形态，不是插件形态。本插件与旧 `hosts/kimicode` **都是 Kimi 原生插件**：Kimi 都从 managed 插件目录按 `kimi.plugin.json` 加载。旧 manifest 的入口写成 `./agent-integrations/kimicode/...`，该路径只有 OpenViking 安装器组装后才存在，因此旧源码目录本身不可安装；本目录自包含，检出即产物。文档与命名据此使用“独立分发/standalone”，不使用“原生/native”作区分词，避免把旧适配器误读为非原生。

独立插件不意味着复制公共行为。以下宿主差异仍留在适配器中：

- Kimi 要求 manifest、托管目录和内联 hooks/MCP。
- `SessionStart` 不消费注入结果，profile 必须移到首次用户提交。
- transcript 是每 agent 一份、带分支语义的 wire，而不是通用单文件 rollout。
- 输出 envelope 与 matcher 规则不能照抄 Claude Code 或 Codex。

新目录取代旧 `examples/agent-hook-plugin/hosts/kimicode*` 的接入方式，但不改变其他宿主。旧 host 对应 Kimi Code CLI 0.43.1，新插件对应 2.6.2；两者使用同一个安装 ID（`openviking-memory`），宿主注册表同名只保留一个条目，不要在同一宿主同时启用，避免双重捕获。两者都是社区实现，不是 OpenViking 官方一方集成。

本设计的边界仅覆盖 `examples/kimi-code-memory-plugin/`：不修改、不复用旧 adapter 的代码或文档，只共享 `examples/memory-plugin-shared/lib/` 公共运行时；旧 adapter 的宿主契约仍以其自身 DESIGN 为准。

### 3.2 共享能力的唯一入口

| 责任 | 必须复用的共享实现 |
| --- | --- |
| 配置与身份 | `buildPluginConfig("kimicode", ...)`、`resolveConnection()` |
| Hook 初始化 | `runHookStage()`，负责 stdin、按 payload cwd 重载配置、enabled/bypass、单次输出 |
| 状态与锁 | `readHookStateDetailed()`、`quarantineHookState()`、`writeHookState()`、`withAgentHookLock()` |
| session 身份 | `deriveHarnessSessionId()` |
| HTTP | `createOvHttp()` / `makeAgentFetchJSON()`，共享 header、超时与 trace ID |
| 召回与 profile | `recallForPrompt()`、`buildAgentProfile()` |
| 清洗与过滤 | `shapeCaptureParts()` / `shapeCapturePayload()` |
| 发送与确认 | `addAgentMessages()`、`sendSessionMessages()` |
| 离线与提交 | `enqueueAgentPending()`、`replayAgentPendingDetailed()`、`listPending()`、`commitAgentSession()`、`getAgentSession()` |
| MCP | `toMcpProxyConfig()`、`createOpenVikingMcpProxy()`、`resolveMcpActorPeerId()` |
| URI guard | `evaluateUriGuard()`、`denyHookSpecificOutput()` |
| doctor | `runDoctor(host)` |
| 安装与启用 | Kimi 原生 PluginManager 及 `/plugins` 命令；不实现私有安装器 |

原共享 API 有两处信息不足，本轮已在共享层补齐并纳入测试，适配器不得绕过：

- `readHookStateDetailed(clientId, nativeSessionId)`：返回 `missing | ok | corrupt | unreadable`、状态值和错误；`missing` 仅指 ENOENT，JSON/结构无法解析为 `corrupt`，权限及其他 I/O 错误为 `unreadable`。现有 `readHookState()` 把这些情况全部折叠成 `{}`。另提供显式 `quarantineHookState()`，供确认 corrupt 后以时间戳备份原文件，读取函数本身不写文件。
- `replayAgentPendingDetailed()`：保留现有计数，同时返回每个 pending 操作的 `type`、`sessionId`、`dedupKey`、`sent | failed | skipped | deferred` 和错误类别，并接受 `shouldReplay` 谓词。谓词必须在 claim 前求值，暂缓的条目返回 `deferred`，不消耗重试次数。现有 `replayed` 聚合计数无法区分 addMessage 与 commitSession，也无法区分成功和因 TTL/非重试错误被删除，无法安全清除 `commitPending` 或结束标记。

适配器只拥有：Kimi 事件与 envelope、session locator、wire 解码和活动分支投影、Kimi 消息映射、宿主 doctor 检查以及安装/发布登记。

### 3.3 目录与职责

```text
examples/kimi-code-memory-plugin/
├── DESIGN.md
├── kimi.plugin.json
├── package.json
├── README.md
├── README_CN.md
├── scripts/
│   ├── session-start.mjs
│   ├── user-prompt.mjs
│   ├── stop.mjs
│   ├── pre-compact.mjs
│   ├── session-end.mjs
│   ├── uri-guard.mjs
│   ├── envelope.mjs
│   ├── config.mjs
│   ├── session-locator.mjs
│   ├── wire-reader.mjs
│   ├── wire-projector.mjs
│   ├── kimi-messages.mjs
│   ├── session-state.mjs
│   ├── capture-session.mjs
│   ├── turn-ledger.mjs          # 逐轮次交接/送达对账，不保存消息正文
│   ├── hook-stage.mjs
│   ├── sweep.mjs
│   ├── backfill.mjs            # 显式历史导入，不是 hook
│   ├── recall.mjs
│   ├── pack.mjs
│   ├── doctor.mjs
│   ├── recover.mjs             # 显式失败恢复，不是 hook
│   └── shared/                 # sync.mjs 生成，不手改
├── servers/mcp-proxy.mjs
├── skills/                     # SKILL_TARGETS 生成，不手改
└── tests/
    ├── fixtures/
    ├── fake-ov/
    ├── unit/
    ├── contract/
    └── e2e/
```

入口约束：

- 六个事件入口只调用 `runHookStage()` 和对应阶段函数，不互相 import，不自行读取 stdin。
- `envelope.mjs` 是唯一写 stdout 的适配器模块；只有 `user-prompt` 和 `uri-guard` 会产生非空 stdout。
- 普通错误写 stderr/共享日志并 exit 0；URI guard 的明确拒绝通过合法 JSON envelope 表达，也 exit 0。
- 模块 import 时不读 stdin、不联网、不启动进程、不退出。
- `wire-reader`、`wire-projector`、`kimi-messages` 尽量保持纯函数或可独立重放，网络只存在于 `capture-session`、`recall` 和共享层。

## 4. 生命周期调度与预算

### 4.1 各入口工作

| 入口 | 工作 | 非空 stdout | 内部总预算 |
| --- | --- | --- | --- |
| `session-start` | 所有来源按 §6.3 的规则过滤并有界 replay；startup/clear 再扫描结束标记，resume 不做补偿提交 | 无 | 3 s |
| `user-prompt` | 补齐可交付记录，注入首次 profile，逐事件召回；允许复用短期检索结果但不吞事件 | 一段文本 | 15 s，其中 capture ≤ 7 s、recall ≤ 8 s |
| `stop` | 读取所有 agent 的稳定活动轮次，发送/入队，按阈值提交 | 无 | 6 s |
| `pre-compact` | 补齐稳定前缀，只要有已送达且未提交的终态轮次就提交，不受 token 阈值阻断 | 无 | 10 s |
| `session-end` | 先写结束标记，再补齐并提交所有可闭合 agent | 无 | 5 s |
| `uri-guard` | 本地检查文件工具参数 | deny JSON 或无 | 2 s |

`user-prompt` 的 15 s 是入口总预算，不是 capture 与 recall 相加：capture 必须在最后 8 s 的召回窗口开始前结束，recall 的 deadline 从它实际开始时刻计算且不超过入口 deadline。capture 超时后剩余不足 1.5 s 时直接跳过召回，不发起注定失败的请求，也不计入熔断。

所有写入口在取得 session 锁后，先在剩余预算内有界重放相关 pending，再处理新 wire 和 commit；不是只有 `SessionStart` 才重放。重放成功才允许推进交接状态，剩余项留给下一边界，不能因为本轮预算耗尽就删除。

这些预算是初始设计值，不是实测性能数据。manifest timeout 是外层硬限制，内部 deadline 必须更小。预算耗尽时保留可恢复游标，不能靠超时杀进程当作正常提交路径。

### 4.2 锁与并发

- 同一原生 session 的所有入口使用 `withAgentHookLock("kimicode", nativeSessionId, callback)`。
- 共享实现最多等待 5 s，60 s 后回收陈旧锁，超时返回 `null`；本入口此时安静退出，等待下一边界。
- 不同 session 使用不同状态文件，可并行。
- 状态只用 `writeHookState()` 的临时文件 + rename 持久化；不得另加互不排斥的锁或第二个 version 字段。
- MCP proxy 是长生命周期进程，但绝不是 capture/commit worker。

## 5. Capture 设计

### 5.1 流水线

```text
session_id
  → session locator + state.json 校验
  → agents/*/wire.jsonl 增量读取
  → 协议、完整行、turn/step 状态机
  → activeChain 活动分支投影
  → Kimi → OpenViking 消息映射
  → 共享清洗、过滤、截断
  → 共享批量发送 / pending 持久化
  → 连续确认游标
  → commit guard
```

`Stop` 直接发送已闭合的活动轮次，不把完整消息体塞进 hook state。未闭合轮次通过保留其起始游标重建；离线消息由共享 pending queue 持久化。

### 5.2 初始化与增量游标

首次看到 session：

- `SessionStart(source=startup)` 的新会话从 wire 起点开始。
- 插件中途安装、普通 resume 或无法证明已有游标时，从当前文件尾开始，避免把历史当作新记忆批量回放。唯一例外是显式 `backfill.mjs`：它只处理没有插件状态文件的会话，从首条记录开始；已有状态的会话只能沿记录游标追平，不能重置。
- `state.json.forkedFrom` 指向父会话时，保存父子关联，并以 wire 中 `forked` 标记之后的首个完整新轮次为 capture 起点。继承前缀默认不重复采集；缺少标记时从当前文件尾开始并记录诊断，不能猜测分叉位置。完整继承历史目前**尚未提供导入路径**：`backfill.mjs` 仍按 `forked` 边界跳过继承前缀；若未来增加前缀导入，必须同时定义与父会话的去重策略。
- 使用 `readHookStateDetailed()` 区分状态：`missing` 按上述规则初始化；`unreadable` 保留所有游标并暂停该 session capture，绝不能重置进度；`corrupt` 先由共享 `quarantineHookState()` 备份，再从当前文件尾恢复，fork 则从 `forked` 后的新轮次恢复。普通旧会话不得因状态损坏从 0 重放。

每个 agent 持久化：

- `wirePath`、协议版本、文件身份与前缀指纹。
- `scanOffset`：已完整解码并作出本地处理的位置。
- `ackOffset`：已发送、已持久化 pending，或明确记为有界丢弃的连续前缀。
- `pendingTurnOffset`：最早需要重建的开放/未确认轮次；持久化游标不得越过它。
- 分支边、当前活动分支及必要的轮次到 wire line 映射。
- 有界稳定消息 ID 集合与有界 turn ledger：逐轮记录完整原生轮次的 `messageCount`、每条消息的 `sent | queued`、queued 对应的 `dedupKey`、turn 类型和 wire 范围，不保存消息正文。只有全部消息均已送达的 complete 轮次才计入普通提交阈值；溢出必须阻止提交并进入显式恢复，不能当作已结算。
- `commitPending`：`{ovSessionId, agentId, dedupKey, createdAt, endedAt, body}`，用于和详细重放结果精确匹配；`body` 保留 `{keep_recent_count: 0}`。
- `capture_failed` / `pending_lost`：按 `{agentId, ovSessionId, dedupKey, type, wireStart, wireEnd, stableIds, errorKind, at}` 记录，非重试失败另存不含秘密的 `configFingerprint`；没有 wire 范围的 commit 失败必须能据此区分，不能伪造起止行。
- `lastError`、session 级 `endedMarker`、`endedResult`、`hasCaptureGaps`、`resumedAt`、`lastActivity`、`profileInjected`、`recallCache`、`recallFailures`、`recallFailureUntil` 和 `diagnostics`：有界计数器，以及 `nonLocalEnvironment: {environmentId, at}` 这类跳过原因记录。

`lastActivity` 只在真实 prompt、终态轮次送达或显式恢复成功时更新，sweep、锁等待和失败重试不得刷新。`recallCache` 仅保存 `{promptHash, block, at}`，500 ms 内可复用但每个事件仍正常输出；block 超过 8 KB 时只保留当次结果，不写入状态。

reader 规则：

- 只消费以换行结束的完整 JSONL；尾部半行下次从同一字节位置重读。
- 文件缩短、替换或前缀变化时重新扫描，并依靠稳定 ID 抑制重复；不能只按文本 hash 去重。
- 每次扫描默认最多 32 MB，达到预算后保留下次续读位置；不能因为 main 较大就跳过尾部。
- 所有身份和游标按 agent 隔离；turn ID 只在该 agent 内解释。

### 5.3 Turn 与 step 状态机

| 当前状态 | 记录 | 动作 |
| --- | --- | --- |
| 无 | `turn.prompt` | 打开 deferred 轮次，记录稳定 ID 与起始 line |
| deferred | loop events | 累积文本与工具证据，不发送 |
| deferred | `turn.steer` | 按宿主 normalization 闭合当前逻辑轮次、分配新的逻辑 turn ID 并开启 steer 轮次；已有内容按实际完整度处理，终态按映射后的 turn ID 归属 |
| deferred | `turn.ended=completed` | 若记录完整则成为 complete，可发送并作为 commit 边界 |
| deferred | `turn.ended=failed/cancelled/其他异常` | 成为 terminal partial，只发送真实存在的部分；不作为普通阈值提交的 complete 边界 |
| deferred | `turn.cancel` | 记录取消意图，继续等待终态；不合成结果 |
| deferred | EOF | 保留起始游标，下次重读 |
| deferred | ended sweep 满足 §6.3 的 marker、锁与文件静止条件 | 成为 recovery partial，只发送已有内容，不合成终态记录或工具结果 |
| 未交接 | undo、clear、branch switch | 仅丢弃仍在本地且不属于活动分支的投影 |
| 已发送或已入 pending | undo、clear、branch switch | 保留并记录不可撤回限制，不伪造修正消息 |

`turn.ended` 到达时仍未闭合的 step 可以按 partial 输出已有内容；没有轮次终态时不能这样做。`step.end` 只关闭 step，不关闭 turn。缺少工具结果时保留 `running` 状态并标记 partial，不能写成成功。

### 5.4 活动分支、undo 与 clear

必须按宿主 `tree.ts` 的 `parseTree` + `activeChain` 语义投影，而不是只按最后一个 `base.line` 截断文件：

1. 每个 `agent.switched` 形成一条从 `base.{branch,line}` 到新 `branch` 的边。
2. 从当前活动分支递归向 base 分支走，保留 base 中截至 `base.line` 的继承段。
3. 回到当前边时，只保留该边之后的新段；被放弃的中间段不进入活动链。
4. 跳过 `agent.switched` 本身，检测未知分支、重复分支和环；异常时停止推进相关游标并报告。
5. `base.line` 是 base 分支中最后保留的 line，不等于“第一个撤销轮次前一行”的全局简化，更不是游标目标。

因此，单次 undo 的“保留前缀、删除中段、接续新段”只是递归算法的特例；多次 switch 必须沿 base 链计算。

`context.undo` 只影响本地 undo 锚点；若其 line 被某个 `legacyUndoLine` 配对，则该 switch 已表达这次撤销，不能应用两次。`context.undone.fromTurnId` 只辅助定位待丢弃的本地轮次。`context.clear` 清空未交接投影但继续使用同一 OpenViking session，不生成 `-c<n>` 新身份。

已发送或已 queued 的内容没有单消息撤回事务，允许留在服务端；这是明确的 at-least-once 与有限撤回边界。

### 5.5 用户输入筛选

| 输入来源 | Capture 决策 |
| --- | --- |
| `origin.kind=user` 且 `trigger !== "continue"` | 捕获模型实际看到的用户文本，`message_kind=user_query` |
| `skill_activation` 或 `plugin_command` 且 `origin.trigger === "user-slash"` | 按宿主可见形式重建命令：Skill 为 `/<skillName> <skillArgs>`，插件命令为 `/<pluginId>:<commandName> <commandArgs>`；字段名以宿主 2.6.2 为准，不把整份 Skill 内文当作用户问题 |
| `shell_command` 且 `phase === "input"` | 记录用户可见命令输入；其他 phase 不冒充用户提问 |
| `task`、`system_trigger`、`injection`、`hook_result` | 不生成 `user_query`；同一轮真实助手/工具工作仍可捕获 |
| `trigger === "continue"` | 不作为用户输入 |
| `context.append_message` | 不作为独立输入来源，避免重复捕获注入与 hook 结果 |

steer 以 `messageId`/`promptId` 去重，不能假定一条 prompt 只有一个 steer 表示。相同文本可以是两个合法轮次，不能永久按文本去重。

### 5.6 OpenViking 消息映射

| Kimi 内容 | OpenViking 消息 |
| --- | --- |
| 用户输入 | `role=user`、TextPart、`message_kind=user_query` |
| 助手文本 | `role=assistant`、有序 TextPart、`message_kind=assistant_step` |
| 工具调用 | 助手消息的 ToolPart；`tool_id=toolCallId`、`tool_name=name`、`tool_input=args`、初始 `tool_status=running` |
| 工具结果 | `role=user`、`message_kind=tool_transport`、同一 `tool_id`；文本 `tool_output`，按 `isError` 设置 `error` 或 `completed`；仅有真实 `durationMs` 时设置 `duration_ms`，缺省不伪造 |
| compaction、undo、branch、goal、plan、permission、usage 等控制记录 | 不生成对话消息 |

字段约束：

- `turn_id` 使用字符串形式的逻辑 turn ID；steer 分段后使用宿主 normalization 对应的逻辑身份。
- `created_at` 从 wire 的 epoch 毫秒转为 ISO。
- `tool_input` 必须为对象，标量需包装；`tool_output` 必须为字符串，Part 数组只拼接文本。
- 不自行设置消息 `id` 或 `peer_id`；后者由 `addAgentMessages(..., peerId)` 处理。
- read/search/find 等仍保留为 ToolPart，不能只留最终助手文本。
- 只含 ToolPart 的消息在共享过滤中保留；若文本命中共享 `captureFilters` 的 drop 规则，则按共享语义整轮（含工具部分）丢弃，适配器不另写一套规则。
- 所有 parts 经 `shapeCaptureParts()` / `shapeCapturePayload()` 处理，复用 `captureMaxLength`、`captureToolMaxChars` 和注入内容清洗。
- 共享 sender 每批最多 100 条，批量接口 404/405 时回退串行，不自行分批或重写状态码表。

### 5.7 大工具结果、媒体与 Experience 名称

- 只发送模型实际看到的工具输出。Kimi 默认在 50,000 字符后外置结果时，wire 中的说明和预览就是 capture 内容。
- 不读取 `output_path`、`tasks/*/output.log` 或 `tool-results/*.txt`，也不把这些外置文件的本地路径作为 `tool_output_ref` 或额外内容发送。普通工具参数中模型已经看到的路径仍属于对话证据，按共享过滤和脱敏规则处理，不能粗暴删除所有路径而破坏工具语义。
- 服务端负责大输出外置；客户端 `captureToolMaxChars` 只防异常载荷。
- `image_url`、`audio_url`、`video_url` 不转换为本机引用；文本与媒体并存时只保留可映射文本。
- Experience lineage 的首选修复放在服务端：让 `_MCP_OPENVIKING_READ_RE` 同时接受 Kimi 的 `plugin-...` 形式与既有 `plugin_...` 形式，并补真实限定名测试，避免每个宿主分别改写工具名。对尚未包含该修复的旧服务端，适配器可仅把本插件 `openviking` server 的 `read` / `multi_read` 降级映射为 `openviking_read` / `openviking_multi_read`；其他工具名不变。该映射是版本兼容层，在最低服务端版本包含修复后删除，不作为永久协议。

## 6. 确认、Commit 与恢复

### 6.1 发送确认

共享发送结果必须逐项解释：

| 结果 | 游标与后续责任 |
| --- | --- |
| `sent` | 服务端已确认，可推进连续 ack 前缀 |
| `queued` | 消息已落 pending，可推进交接游标；仍须重放，不能当作已送达 |
| `enqueueFailed` | 停在首个未持久化位置，下一边界重建，不得越过 |
| 非 retryable `failed` | 不进入 pending、不在每个事件上盲目重试；保留失败游标并设置持久 `capture_failed`，阻止该 agent 自动 commit |

非重试失败不是默认的数据丢弃理由。只有连接/凭据来源或 capture 配置的有效指纹变化后才允许重试，指纹不得包含秘密本体；成功后按连续前缀推进。若错误属于永久参数问题，必须由用户调整过滤配置或显式放弃该段后才能继续，不能由插件静默越过并归档一个看似完整的会话。

`replayAgentPendingDetailed()` 的每个结果按类型处理：`addMessage=sent` 记为已送达但未提交；`commitSession=sent` 才能清除对应 `commitPending`、重置未提交计数并参与结束标记结算；`failed`、`deferred` 保留原状态；`skipped` 只表示 queue 因 TTL、重试耗尽或非重试错误有界丢弃，必须记录 `pending_lost`，不能当成发送成功或清除 commitPending。若进程在结果持久化前退出，恢复状态保持 unknown，宁可保守重试并记录可能重复，也不能假定成功。

发送成功但状态保存失败时允许重放并产生少量重复，契约是 **at-least-once**，不是 exactly-once。去重优先使用 prompt/message/step/tool 稳定 ID；有界集合被淘汰后允许重复，不能用文本 hash 冒充服务端幂等。

`autoCapture=false` 禁止新 capture 及其触发的新 commit，但历史 pending 仍重放。`autoCommitOnCompact=false` 只跳过 PreCompact 的 commit，不跳过已有的发送和恢复。

### 6.2 Commit 触发与保护

普通 `Stop`/prompt 补齐后：

1. 仅当本轮直接发送成功，或详细重放出现 `addMessage=sent`，才查询 `getAgentSession()`；没有新送达消息的空闲事件不增加 GET。
2. `pending_tokens >= commitTokenThreshold` 时提交；只有成功取得 session 对象但对象缺少该字段时才按 0 处理并允许轮次兜底。`getAgentSession()` 返回 `null` 表示元数据不可用，本轮跳过 commit 并保留下次重试，不能把网络/鉴权失败伪装成旧服务端缺字段。
3. turn ledger 中全部消息均已送达的 complete 原生轮次数 `>= commitTurnThreshold`，作为低 token 会话的新鲜度兜底。这里计数的是**完整原生轮次**，不是 user/assistant/tool 消息条数；queued、terminal partial 和只送达一部分消息的轮次均不计入。

`PreCompact` 与 `SessionEnd` 是强制边界：只要有已送达且未提交的**终态轮次**就提交，不要求达到上述阈值，避免短会话永远不提取。complete 可以提交；failed/cancelled 形成的 terminal partial 必须等到 `turn.ended` 等终态已经到达才可提交；recovery partial 只能由 §6.3 的 ended sweep 产生。仍开放的 deferred 轮次不能提交。若压缩发生在开放轮次中间，选择该轮次之前的最后终态边界，开放尾部保留到后续事件，不能因此阻塞更早的稳定前缀。

每次 commit 前按顺序检查：

1. 当前持有该 session 锁。
2. 存在已送达且未提交的终态轮次，且提交边界内没有 deferred 开放轮次。普通阈值只由 complete 触发；PreCompact/SessionEnd 可以包含 terminal partial。
3. 当前 session 没有未重放的 `addMessage` pending，`queued === 0`。
4. 没有 `enqueueFailed`、未解决的 `capture_failed`、`pending_lost` 或已有 `commitPending`；任何失败都不得被后续完整轮次掩盖。
5. 当前边界允许提交：达到阈值，或为 PreCompact/SessionEnd 强制边界。

全部通过才调用：

```js
commitAgentSession(fetchJSON, ovSessionId, log, { keep_recent_count: 0 })
```

Kimi 自己持有会话上下文，因此本设计固定 `keep_recent_count: 0`，不把 `commitKeepRecentCount` 宣传为本插件可用开关。pending 必须保留这一原始 body。

commit 成功只表示归档/提取请求被接受；HTTP 成功、任务受理、归档完成和长期记忆提取完成必须在日志与 doctor 中分开。

### 6.3 SessionEnd 与启动补偿

`SessionEnd`：

1. 若已有插件状态，先写 `{at, reason}` 形式的 `endedMarker`。
2. 重读 wire，发送已稳定记录；不能因 Agent 尚未 drain 就强制编造开放轮次。
3. 提交所有已闭合 agent；每个 agent 有独立 OpenViking session 和提交状态。
4. 只有所有 agent 均完成、无未确认消息/失败且 commit 已被接受时才清除 marker。commit 入 pending 时保留 marker 与 `commitPending`；后续只有直接 `result.ok` 或详细重放的 `commitSession=sent` 才能结算，queue 条目消失本身不是成功证据。
5. 若从未有状态文件，不做历史回放，也不创建空 sweep 目标；历史导入只由显式 `backfill.mjs` 完成（§6.5），sweep 不承担该职责。

`SessionEnd` 本身不强制闭合开放轮次。后续 sweep 仅在同时满足以下条件时，才可把遗留 deferred 轮次标为 **recovery partial**：存在对应 `endedMarker`、取得 session 锁、目标文件在两次间隔读取中大小和 mtime 均未变化。此时只发送已经存在的内容并记录 `interrupted_before_drain`，不合成 assistant 文本或工具结果；该终态可参与强制边界提交。没有 marker 的崩溃尾部不能这样闭合，只能等待宿主补写终态或显式恢复操作。

`SessionStart(source=resume)` 先记录 `resumedAt`，通过 `shouldReplay` 暂缓当前 session 的 `commitSession`，只重放消息和其他不受影响的任务。`resumedAt > endedMarker.at` 的状态不进入 idle/ended sweep，旧 `commitPending` 保持 deferred。只有在当前恢复代际中产生并送达新的完整轮次后，才允许重放该 commit；此前不发起新 commit，之后仍按详细 `sent` 结果结算。这样不会让旧结束标记或旧 commit 在刚恢复时封存活动会话，也不会让 queue 条目永久滞留。

下一次 `SessionStart(source=startup|clear)`：

1. 有界执行 `replayAgentPendingDetailed()`，逐操作更新送达、`commitPending` 和 `pending_lost`；resume 按上一段的代际规则过滤，不直接执行当前 session 的旧 commit。
2. 通过 `listPending()` 检查目标 session 是否仍有消息或 commit；有则留给后续重放，不能重复提交。条目不存在时只能依据详细结果结算，不能猜测其已成功。
3. 扫描有结束标记或长时间空闲的状态，最旧优先；默认最多 50 个文件、最多一次补偿 commit。
4. resume 不做这类补偿提交，防止旧结束标记影响已经恢复的活动会话。
5. sweep 不刷新 `lastActivity`；30 天状态保留期只是本地清理策略，不是服务端数据保留承诺。

pending queue 有最大重试次数、TTL 和批量上限，属于有界恢复。超过限制被丢弃时必须留下诊断，不能宣称离线数据永久可靠。

若进程消失且此后再没有 `SessionStart` 或用户 prompt，sweep 没有运行机会，残留消息和 commit 只能等待下一次启动。首版接受并公开这一限制，不让 MCP proxy 暗中充当 worker，也不把“已写结束标记”宣称为“已完成恢复”。

### 6.4 显式失败恢复

`capture_failed` 或 `pending_lost` 不能永久把用户锁死，也不能由插件自动忽略。除配置变化后的自动重试外，在插件根目录提供非 hook 入口 `scripts/recover.mjs`，所有动作都取得同一 session 锁、记录操作者和原因，且本身不发起网络请求：

```text
node ./scripts/recover.mjs retry-commit --session <nativeSessionId> --agent <agentId>
node ./scripts/recover.mjs accept-loss --session <nativeSessionId> --agent <agentId> --yes
node ./scripts/recover.mjs accept-loss --session <nativeSessionId> --all-agents --yes
```

- `--agent` 选择具体的 OpenViking agent session；只有 main 时可省略。存在多个 agent 时，省略必须报错，`--all-agents` 也只能与明确的 `--yes` 一起使用，避免误扩大损失范围。
- `retry-commit` 只处理所选 agent 的 commitSession 丢失/未知结果：该 agent 必须没有 addMessage pending、`capture_failed` 或其他 commitPending，才清除该 commit 的阻塞并保留 endedMarker，让下一边界重试。它不能用于跳过未送达消息，并需提示重复 commit 的可能。
- `accept-loss` 按已记录的失败范围推进所选 agent 的 ackOffset、写入 tombstone 与 `hasCaptureGaps`，再清除对应的 `capture_failed` / 消息型 `pending_lost`。只有所有其他 agent 也已送达、已提交或明确接受损失后，才写入 `endedResult=partial_loss` 并移出活动 sweep；否则 marker 保留，不能提前结算整个会话。
- 两个操作都不删除 wire、pending 之外的宿主文件或既有 OpenViking 记忆。doctor 必须展示这些真实命令、对应 session/agent 和失败类别；普通诊断模式保持只读。

### 6.5 显式历史导入（backfill）

hook 只从首次看到 session 的时刻开始记忆，安装前的历史默认不迁移。需要导入时，在插件根目录运行非 hook 入口 `scripts/backfill.mjs`：

```text
node ./scripts/backfill.mjs --dry-run
node ./scripts/backfill.mjs [--session <id>[,<id>]] [--limit <n>] [--timeout <seconds>] [--json]
```

- 对没有插件状态的会话从头导入；已有状态的会话只沿记录游标追平（见下一条）。只处理 `~/.kimi-code/sessions/<workspace>/<session>/state.json` 能解析、`environmentId` 为 local 的会话。首次导入以 `startSource=startup` 从首条记录开始，走与 hook 相同的 `captureSession` 管线；每轮结束立即 `saveState()`，后续 hook 从同一游标继续。
- 已有状态的会话只沿记录游标追平，绝不把 `ackOffset`/`scanOffset` 重置到 0。OpenViking 没有服务端消息去重，重放已捕获前缀会产生第二份记忆。
- 与 hook 一样遵守 `autoCapture`、`bypassSession` 和 `bypassSessionPatterns`；被排除的会话直接跳过。首次写入前探测 `/health`，服务不可达时以退出码 2 失败，不把整段历史灌入离线队列。
- `corrupt`/`unreadable` 状态跳过并留给 `doctor`/`recover`，不覆盖、不隔离。非本地环境、legacy/不兼容 wire、缺少 fork 标记的会话按既有规则跳过并报告。
- fork 只导入 `forked` 标记之后的新轮次，继承前缀不复制；父会话可能已经拥有它，复制会造成重复提取。
- 每轮读取上限由内部 `scanLimitBytes`（1 MB 起，遇到超大开放轮次自适应放大到 32 MB）约束，普通轮遵守提交阈值，最后一次强制边界提交，保证短会话也会触发一次提取。进程中断时已落盘的轮次不会重发。
- 活动会话的开放尾部不发送；在它闭合前，该会话的 commit 会被与普通边界相同的 `open_turn` 守卫阻止，消息保持未提交，需等下一个 hook 或再次运行 backfill。此时结果为 `open_tail_pending`，不是失败。
- 插件状态文件是唯一的去重依据；删除后重跑会再次导入整段 transcript。
- `--dry-run` 不取锁、不发请求、不写状态；`--session` 只导入指定原生 session，`--limit` 限制本次处理数量，`--timeout` 是单个会话的软预算。

## 7. Recall、MCP、URI Guard 与 Skills

### 7.1 用户提交与召回

`user-prompt.mjs` 通过 `runHookStage()`：

1. 从 ContentPart 数组提取真实用户文本，不能把整个数组 JSON 序列化为查询。
2. 先补齐上一轮可交付 capture，再执行召回。
3. `enabled` 或 bypass 关闭时整个入口不做自动注入或召回。`noAutoInject=true` 只关闭 profile 与 skill 清单，`autoRecall=false` 只关闭逐轮检索；两者与 CC、Codex 保持相同语义，显式 MCP 调用不受影响。`minQueryLength` 只限制逐轮检索，由适配器在调用前执行，因为共享 recall 没有消费它。
4. `noAutoInject=false` 时，在首个成功 prompt 调用 `buildAgentProfile(fetchJSON, cfg, cwd)`；其中包含 profile 与由 `skillCatalog` 控制的服务端 `<available-skills>`。确认空结果也算完成，失败最多重试一次；成功后不得重复注入，失败也不能让每次提问都无限重试。
5. `autoRecall=true` 且查询达到最小长度时，调 `recallForPrompt(..., { sessionId: kcSessionId })`，让共享层处理 peer、查询扩展、跨轮去重、预算和服务端兼容。`recallQueryFilters` 在长度判定前执行：`s/` 规则改写查询文本，`d/`/`k/` 命中则本次不召回并记录 `query_filtered`，与 CC、Codex 的 `auto-recall` 同序。
6. 无结果保持为空；网络失败 fail-open，不注入错误文本，不阻断用户。

宿主没有 prompt ID，因此**不做基于文本 hash 的事件去重**：相同文本可能是用户有意再次提交，不能在 500 ms 窗口内吞掉这次提问。可以按 §5.2 的 `recallCache` 在 500 ms 内复用相同查询的结果以减少请求，但每个真实 hook 事件仍独立产生一次合法输出。profile 只由 `profileInjected` 防重。召回熔断采用设计值：连续两次失败暂停 60 s，再次失败暂停 300 s，成功即复位。

输出只打印一次普通文本，exit 0。打印前移除召回内容中伪造的 `<hook_result ...>` / `</hook_result>` 和 `<openviking-context ...>` / `</openviking-context>` 边界标签；插件不自行打印宿主的 `<hook_result>` 包装。这只是包装卫生，不是提示注入安全边界，召回文本仍按不可信参考资料处理。

### 7.2 MCP proxy

`servers/mcp-proxy.mjs` 只做：

```text
loadConfig(cwd, { env })
  → toMcpProxyConfig(cfg)
  → createOpenVikingMcpProxy(...)
```

并导出可供共享 parity 测试调用的 `readProxyConfig(env)`。

约束：

- `tools/list` 是唯一工具目录；不维护本地 schema 副本、REST client、SSE parser 或重试状态机。
- proxy 启动 cwd 是插件根，不能据此推断用户项目。actor peer 由 `resolveMcpActorPeerId()` 决定；未显式设置时采用共享宽范围行为并给出警告。
- hook 与 proxy 必须经同一配置链解析 URL、凭据与身份，并加入 `mcp-hook-parity.test.mjs`。
- runtime server 名为 `plugin-openviking-memory:openviking`；工具名经宿主 sanitizer 后形如 `mcp__plugin-openviking-memory_openviking__read`。
- 宿主工具名最长 64 字符，超出时截断并加 8 位 hash。上述前缀为 42 字符，工具名余量为 22；Skill 不硬编码可能被截断的长名称。
- `mcpEnabled` 首版没有真实消费者，不出现在用户可用开关文档中。

### 7.3 URI guard

`PreToolUse` 对 `Read`、`Glob`、`Grep`、`Edit`、`Write`、`ReadMediaFile` 调用：

```js
evaluateUriGuard(payload.tool_name, payload.tool_input)
```

非空结果通过 `denyHookSpecificOutput(reason)` 输出 `permissionDecision: "deny"`，exit 0。普通本地路径不受影响。`ReadMediaFile` 与 `Read` 一样接受本机 path，实施时在共享 `DEFAULT_TOOL_HINTS` 增加规范化名 `readmediafile`，复用 OpenViking `read` 的替代提示；不支持的媒体读取能力不改变“`viking://` 不是本地路径”这一事实。

不调用共享 `preToolUseOutput()` 的 shell notice 分支：Kimi 不消费 `additionalContext`，而 PreToolUse 的 `message` 也不会成为模型可见提示。因此 Bash 中的 `viking://` 不拦截、不承诺附加提示，由 Skill 引导模型使用 MCP。

### 7.4 Skills

通过 `SKILL_TARGETS` 从 `examples/skills/` 生成四份，不手改副本：

| Skill | 作用 |
| --- | --- |
| `openviking-memory` | 显式检索与沉淀；自动 hook 已完成的 capture/commit 不要求模型重复执行 |
| `openviking-skills` | 查找、读取、创建、安装和共享服务端 Skills；使用真实 `add_skill` 工具，不直接写 skill URI |
| `openviking-rule-search` | 使用服务端实际提供的 `rule_search`；参数以 `tools/list` 为准 |
| `ov-experience-memory` | 检索并应用 Experience；新版服务端直接识别 Kimi read 名称，旧版走 §5.7 的临时兼容映射 |

Kimi 显示限定名，如 `openviking-memory:openviking-memory`。Skill 必须说明目标服务端缺少 `rule_search` 或 `add_skill` 时的降级，不能假定所有版本都有同一工具。

## 8. 配置、身份与隐私

### 8.1 配置执行链

所有 hook 与 MCP 使用 `buildPluginConfig("kimicode", ...)`，并按 payload 的真实 `cwd` 重载 workspace 层。`kimicode` 与 `kimi-code` 的别名由共享 resolver 处理，适配器不再实现一张映射表。

配置优先级沿用共享实现：`OPENVIKING_*`、workspace registry、`.openviking/config.local.json`、`.openviking/config.json`、`ovcli.conf` 的 `plugin.kimicode`、通用 `plugin`、旧 `ov.conf`、schema 默认值。凭据仍由 `resolveConnection()` 负责，不能套用普通行为配置的简化优先级。

本设计不新增 harness 默认值，并明确以下边界：

| 配置 | 本插件行为 |
| --- | --- |
| `enabled`、`autoCapture`、bypass | 由共享入口和共享能力最终执行 |
| `autoRecall` | 只控制逐轮检索，由 `user-prompt` 与共享 recall 共同执行 |
| `noAutoInject` | 只控制 profile 与 skill 清单，由 `user-prompt` 执行；与 CC、Codex 一致 |
| `minQueryLength` | `user-prompt` 在召回前执行，不影响 profile |
| `commitTokenThreshold`、`commitTurnThreshold`、`autoCommitOnCompact` | 按第 6 章消费 |
| `captureMaxLength`、`captureToolMaxChars`、`captureFilters` | 共享 capture 清洗与过滤消费 |
| `profileTokenBudget`、`skillCatalog`、`skillCatalogTokenBudget` | 共享 profile builder 消费 |
| `writePathAsync` | 不调用 `maybeDetach()`，首版不产生作用 |
| `commitKeepRecentCount` | 固定传 0，不作为本插件公开开关 |
| `captureToolResults` | 共享路径无消费者；Kimi 始终映射工具证据，不虚设 harness 默认值 |
| `mcpEnabled` | 共享配置在本插件无消费者，不宣传；MCP 开关使用宿主 `/plugins mcp enable` 或 `/plugins mcp disable` |
| `sessionStartMaxBytes`、resume/takeover/repoContext 等 | 无对应宿主路径，不宣传 |
| `recallCompress*` | 保持共享 `off`，首版无本地压缩器 |

共享 `enabled` 只关闭 hook 行为，不替代宿主 `/plugins disable`；要同时停用 hooks、Skills 和 MCP，使用宿主整插件开关。bypass 只约束自动 hook，不妨碍用户显式调用 MCP。

不得使用 `value || default` 合并允许 `false`、`0` 或空字符串的字段。

### 8.2 Session 与 peer

- 主 agent：`deriveHarnessSessionId("kc-", nativeSessionId)`。
- 子 agent：`deriveHarnessSessionId("kc-", nativeSessionId, "subagent-" + agentId)`。
- 每个 agent 独立游标、独立 commit；父子关系只保留在本地元数据，OpenViking 不增加 parent 字段。
- peer 由共享实现按 payload cwd 解析，不能从插件安装目录推导。
- 多个窗口、相同 cwd 的不同会话不能共享写游标。

### 8.3 隐私与日志

- 状态遵守 `OPENVIKING_HOOK_STATE_DIR`，默认是 `~/.openviking/hook-state/kimicode/`。
- 日志经共享 logger，包含阶段、session 关联、耗时、trace ID；不写 API key、完整 Authorization 或整份 transcript。
- 不发送工具外置文件路径和其他不必要的本机绝对路径。
- 记忆保存的是模型可见内容，不是秘密保险库；处理凭据的会话应使用 bypass。
- 召回内容是不可信参考资料，当前真实对话优先，不能把召回文本提升为系统指令。

## 9. 安装、诊断与发布

### 9.1 宿主安装、启用与移除

安装完全使用 Kimi 的 `/plugins`，不提供私有安装器，也不改写 `installed.json`。Kimi 2.6.2 的插件管理器支持本地目录、ZIP URL 和 GitHub 来源；GitHub 来源只取 `owner/repo/ref`（tree URL 的 path 会被吞进 ref，codeload 404），且整仓下载后只认根目录或唯一一层子目录的 manifest，本插件在仓库中的嵌套深度不满足，因此实际可用形态是本地目录与 ZIP URL：

```text
/plugins install <本地插件目录>
```

`pack.mjs` 可打出 ZIP 归档，根目录直接是插件根，可由 `/plugins install <zip-url>` 从任意静态托管安装。发布走主线的 `<包名>@<版本>` tag 惯例：推送 `kimi-code-memory-plugin@<version>` tag 会触发 `.github/workflows/kimi-code-plugin-release.yml` 构建 ZIP 并挂到该 release，用户即可一条命令安装 `releases/download/kimi-code-memory-plugin@<version>/kimi-code-memory-plugin-<version>.zip`。Kimi 的 GitHub 来源只取 `owner/repo/ref`（tree URL 的 path 会被吞进 ref，codeload 404），且整仓下载后只认根目录或唯一一层子目录的 manifest，本插件在仓库中的嵌套深度不满足，因此不用仓库 URL。Kimi 的插件市场条目要求 ZIP URL 加 SHA-256（`expectedSha256` 强制校验）；需要 marketplace 产物时由 `.github/scripts/generate-kimi-marketplace-json.sh` 按宿主 schema（catalog `version: "2"`、`distribution.category: "third_party"`）生成，契约测试校验摘要一致。

ZIP 解包后根目录直接包含 `kimi.plugin.json`、`scripts/`（含 generated shared）、`servers/`、`skills/` 和 `locales/`；安装机不运行生成器或 Bash。宿主复制到 managed 目录、解析 manifest、保留 `enabled`、替换同一 ID，失败时回滚。hook 仍要求 PATH 中有 Node.js 22.19+，MCP 可用宿主 Node 回退。

常用操作也使用宿主命令：

| 操作 | 命令 |
| --- | --- |
| 查看列表 / 详情 | `/plugins list`、`/plugins info openviking-memory` |
| 启用 / 禁用整个插件 | `/plugins enable openviking-memory`、`/plugins disable openviking-memory` |
| 启用 / 禁用 MCP | `/plugins mcp enable openviking-memory openviking`、`/plugins mcp disable openviking-memory openviking` |
| 重新加载 | `/plugins reload`；MCP 变更按宿主提示使用 `/reload` 或 `/new` |
| 移除 | `/plugins remove openviking-memory` |

`kimi plugin install` / `kimi plugins install` 在 2.6.2 CLI 中不是公开子命令。

宿主 `remove` 只删除注册记录、保留插件文件；OpenViking 状态、pending 和其他插件不受影响。从旧集成升级时以同一 ID 替换 managed 副本并保留启用状态；旧 `config.toml` 若残留手工 Kimi hook，doctor 会提示，清理需用户确认以免双重执行。

### 9.2 Doctor

`doctor.mjs` 只向 `runDoctor(host)` 提供宿主 spec，检查：

- manifest 与安装 ID、root、启用状态、实际版本。
- 六个 hook、timeout、引用的入口和 Node 版本。
- MCP 声明为内联 stdio 且入口存在；在线时通过 `POST /mcp` 的 `tools/list` 探测服务端目录。doctor 不 spawn 代理进程；hook 与 MCP 的配置/身份 parity 由 `examples/memory-plugin-shared/mcp-hook-parity.test.mjs` 在测试层保证，不是 doctor 的职责。
- 会话索引、workspace 目录数与状态文件里的 agent 枚举。doctor 不调用 session locator，也不解析 wire 或读取宿主的 `environmentId`；非本地环境的跳过按状态里已记录的 `diagnostics.nonLocalEnvironment` 报告，协议不兼容只按状态里已记录的 `incompatibleWire`/`minProtocolVersion` 报告。
- 状态、pending、`endedMarker`、`capture_failed`、`pending_lost`、commit 失败和召回熔断；明确提示需要配置变更或用户决定的阻塞状态。锁与 `enqueueFailed` 只存在于运行时与提交判定（`session-state.mjs`），doctor 不单独探测。
- 在线时的服务端健康与版本；离线模式不做任何网络请求，包括 `POST /mcp` 探针。

诊断必须区分 disabled、queued、非重试失败和 commit 失败。bypass 与「无结果」在本插件由 hook 运行时判定（`agent-hook-runtime.mjs`），doctor 不重复报告。建议命令必须适用于当前安装形态。

### 9.3 生成、版本与发布

实现时同步完成：

- 在 `sync.mjs` 登记本插件 `TARGETS`，`committed: true`；四个 Skill 加入 `SKILL_TARGETS`。
- 共享行为只改 `examples/memory-plugin-shared/lib/`，再运行 `node examples/memory-plugin-shared/sync.mjs`；不手改生成副本。新增的详细状态/重放 API 保持现有入口兼容，其他宿主不被迫迁移，但其测试与版本检查必须覆盖共享变化。
- `kimi.plugin.json`、`package.json`、安装产物和 User-Agent 版本一致；目标 `0.6.0` 高于旧 Kimi 集成。
- 更新 `.github/scripts/check-plugin-version-bumps.sh` 的登记和 paired manifest。
- 登记独立 Kimi ZIP 的构建与发布 workflow（tag 惯例 `kimi-code-memory-plugin@<version>`）；ZIP 能被 `/plugins install` 直接接受，不只登记到现有 installer bundle 或 npm 矩阵。
- 新 proxy 在同一提交加入 `mcp-hook-parity.test.mjs`；其目录枚举测试会强制要求这一行。
- 发布归档排除 `node_modules`、`.git`、测试和秘密文件；从最终 ZIP 解包后通过宿主 `/plugins install` 执行冷安装、重复安装、reload、remove 和入口 smoke test。
- 后续补齐 `README.md`、`README_CN.md` 以及 `docs/{en,zh}/agent-integrations/` 的成对宿主页面；共享配置链接到共享 README，不复制整份说明。

## 10. 子代理、Fork 与能力边界

### 10.1 子代理

枚举 `agents/*/wire.jsonl`，不能仅读取 main，也不能假设 ID 格式。父 wire 中的 Agent 工具调用保留为父子链接，子 wire 只捕获自己的助手和工具活动。`system_trigger`、`task` 等子代理启动 prompt 不标记为 `user_query`。

主、子 agent 的 OpenViking session 分离，避免 turn ID、retention 和 commit 相互覆盖。若未来证明主 wire 已包含同一子代理记录，必须先建立稳定 ID 去重，不能双份导入。

### 10.2 Session fork

session fork 可复制父 wire 并追加 `forked`，也可按 turn 切片。无论哪种形式：

- 子 session 使用新的 `kc-<child>`，并持久化原生父子 session 及对应 OpenViking session 的关联。
- 默认只捕获 `forked` 之后的新轮次。继承前缀已经可能由父会话提取，重复导入会造成额外提取任务以及重复或竞争的记忆。
- 父会话关联用于诊断和精确回溯；`backfill.mjs` 只导入 fork 后的新轮次，不复制父归档，也不假定 OpenViking 会自动去重。
- 同一子 session 内仍按稳定 ID 去重；父、子游标完全独立。
- fork 不触发 `SessionStart`，首次其他事件要惰性定位；profile 是否注入由 `noAutoInject` 和 `profileInjected` 决定。
- turn-sliced fork 必须以真实 fixture 验收：继承前缀不重复捕获，标记后的新轮次不丢失。

### 10.3 能力状态

| 能力 | 当前证据 | 交付前仍需证明（待实测） |
| --- | --- | --- |
| 六类 hook、manifest、输出与进程模型 | 2.6.2 内嵌源码已核对 | 最终安装包中的真实 payload 和输出 |
| 宿主安装与管理 | `/plugins` 及 PluginManager 源码已核对；公开 CLI 无对应子命令。本机已用 `/plugins install <本地目录>` 装入宿主注册表（`installed.json` 的 `source: local-path`、managed 副本、doctor 无 failure） | ZIP URL 安装、`/plugins reload`、`/plugins remove` 的实测 |
| Wire 协议、turn.steer、活动分支、fork | 2.6.2 内嵌源码已核对 | fixture 重放与真实会话 E2E |
| 配置、召回、发送、pending、MCP | 详细状态/重放 API 已在共享层实现；Kimi 接线与 hook/MCP parity 已通过隔离测试；真实 OV 0.5.5 上 hook 捕获、提交、提取与直接召回注入已成功；`Stop` 已在真实轮次边界送达并提交，`PreCompact` 已触发并提交压缩前的关闭轮次，`SessionEnd` 已写入结束标记并提交全部已结算 agent | 故障恢复、提取质量与延迟 |
| Windows 本地会话 | 宿主路径已核对 | Windows 实机安装和完整会话 |
| POSIX | 宿主源码分支已核对 | 本插件入口、路径和进程回收实测 |
| 远程/Web environment | 本设计不支持 | 另行确认 wire 的可访问位置 |
| 媒体 | 不支持二进制导入 | 若未来支持，另定隐私与预算契约 |
| 已发送/已 queued 内容撤回 | 不支持 | 需要服务端事务能力，不能由客户端伪装 |

## 11. 测试、实施顺序与验收

### 11.1 必留 fixtures

- 普通用户、多个 assistant step、工具调用与成功/失败结果。
- `turn.steer` 关闭当前段并开始新段；重复 messageId/promptId。
- `turn.ended failed/cancelled`、开放 step、缺失工具结果、缺 `turn.ended`、文件尾半行。
- 相同文本的不同 prompt，user-slash Skill/插件命令，task/system-trigger 轮次。
- 单次与多次 `agent.switched`，含跨分支 base、成对 `legacyUndoLine`、异常环。
- 独立 `context.undo`、`context.clear`、compaction。
- 全量与 turn-sliced fork，验证继承前缀不重复采集、标记后的新轮次不丢失，以及显式 backfill 不会与普通 capture 混用。
- backfill 的 dry-run、已有状态追平、`autoCapture`/bypass 排除、服务不可达预检、fork 边界和非本地跳过。
- main、多个自定义子 agent ID、父子 metadata。
- 大工具输出与外置 preview、媒体 Part、注入包装文本。
- 文件缩短/替换、协议不兼容、状态 missing/unreadable/corrupt 及 quarantine。
- sent、queued、enqueue failed、非 retryable failed 的完整序列；配置不变不重试，配置变化后成功，未解决失败前禁止 commit。
- pending 中 addMessage/commitSession 各自的 sent/failed/skipped/deferred；TTL 或非重试删除不得清除 commitPending。
- endedMarker 下 wire 两次静止后的 recovery partial，以及无 marker、文件仍增长时不得闭合。
- 同一文本在短时间内再次提交，两个事件都有合法输出；ReadMediaFile 指向 `viking://` 时被拒绝。

### 11.2 测试层次

- **单元**：locator、reader、turn/step 状态机、递归 activeChain、消息映射、游标和 commit guard。
- **Hook contract**：用真实形态 stdin 启动每个入口，断言退出码、stdout 字节、stderr；服务故障不得阻断 prompt，Stop 不得输出 continuation。
- **Manifest contract**：六个事件、无 matcher、合法 timeout、内联 MCP、入口存在且无 per-hook `env/cwd`。
- **共享契约**：复用共享测试；新增 hook/MCP parity 行，不复制 recall、pending、MCP 全套测试。新增共享测试覆盖详细状态读取、corrupt quarantine，以及逐操作重放结果和原计数兼容性。
- **MCP**：initialize、通知无普通响应、JSON/SSE、并发 ID、stdout 协议洁净、冷启动 cwd 与凭据。
- **状态与恢复**：并发 Stop/End、pending 顺序、结束标记、resume 暂缓旧 commit 直到新完整轮次送达、非重试失败的配置指纹、详细 replay 后 commit、commitPending 结算和 recovery partial；`recover.mjs` 的 agent 选择、多 agent 歧义拒绝、retry-commit 前置条件、accept-loss 确认、tombstone、partial_loss 和锁也必须覆盖。
- **安装产物**：在隔离 `KIMI_CODE_HOME` 中通过真实 `/plugins install` 安装最终目录，以及通过 HTTP(S) 提供最终 ZIP，覆盖重复安装、损坏注册表不覆盖、保留第三方插件、reload、remove 后仅移除注册记录，以及 `/plugins info` 的健康状态。
- **E2E**：从最终归档通过宿主原生流程安装到隔离环境，运行真实 Kimi 2.6.2 与隔离 OpenViking，检查服务端实际收到的尾部消息、commit 和召回。Windows 必须由真实宿主进程触发 hook，确认宿主继承的 PATH 能找到 Node；TUI 与 Desktop 共用同一 hook 路径，不需要分别验证两套契约，但只在开发者终端直接运行入口脚本不等于宿主触发验证。交互测试使用 `/plugins`，不得为自动化虚构 `kimi plugin install` CLI。

所有测试使用独立临时目录、配置、pending 路径和动态端口，不接触开发者真实会话或凭据。

### 11.3 实施顺序

1. **共享基础与独立包**：先实现并测试详细状态/重放 API，再固定 manifest、真实形态 fixtures、配置 loader、MCP parity、sync 和独立 ZIP 构建；先用 `/plugins install` 验证 Windows 本地目录可加载。
2. **主会话最小闭环**：完成 main agent 的普通 turn、profile/recall、共享 sender、pending、PreCompact/SessionEnd commit、显式 recover 与恢复；`autoRecall`、`noAutoInject` 分别验收。
3. **复杂历史**：依次实现并验证 `turn.steer`、递归 activeChain、undo/clear、子代理和 fork。
4. **辅助能力**：接入 URI guard、四个生成 Skills、doctor 和 Experience；服务端 lineage 修复与旧版降级分别测试。
5. **发布验收**：完成版本检查、独立 ZIP、Windows 宿主安装和真实会话 E2E，再补 POSIX；同步中英文用户文档。

阶段的内部预算和扫描上限必须根据代表性大结果、多 agent fixture 实测调整，不把初始秒数当作性能承诺。每阶段保持可回退的小交付，但最终对外支持矩阵只包含已经完成验收的能力。

### 11.4 合入验收单

- [x] manifest 在 Kimi 2.6.2 实际加载，六个 hook 无非法字段，hook 与 MCP 均从插件根启动。
- [x] 每个真实 prompt 事件最多注入一次；重复文本不会吞掉新事件，`noAutoInject` 只关闭 profile，`autoRecall` 只关闭逐轮检索，空结果和故障不阻断。
- [x] 普通轮次的用户、助手、工具调用和结果按顺序到达服务端，无重复注入旧记忆。
- [x] `turn.steer` 不被漏采或错当成 `prompt.steered`；异常终态不伪造完整消息。
- [x] 多次 branch switch 与宿主 activeChain 一致；fork 继承历史不重复提取、新轮次不丢失，undo/clear 规则一致。
- [x] 子 agent 使用独立 session，不污染 main 游标。
- [x] sent/queued/enqueueFailed 各自推进正确；非重试失败保留游标，配置不变不盲试、变化后重试，未解决失败前不 commit；没有 exactly-once 虚假宣传。
- [x] queued 或未送达消息之前不 commit；PreCompact/SessionEnd 的小会话也能提交。
- [x] commit pending 保留 `{keep_recent_count: 0}`；只有详细 `commitSession=sent` 才结算，skipped/TTL 不会误清除；resume 时旧 commit 暂缓到新完整轮次送达后，结束标记经有界恢复处理。
- [x] 状态 unreadable 不丢游标，corrupt 先隔离再安全恢复；ended sweep 只在 marker、锁和文件静止条件齐全时生成 recovery partial；retry-commit 不越过消息，accept-loss 只在明确确认后推进并留下 partial_loss。
- [x] URI guard 覆盖 ReadMediaFile 且不影响普通路径，Bash 限制已写明；Skills 不调用不存在的固定工具名。
- [x] hook/MCP 配置与身份 parity 通过，MCP stdout 无日志污染。
- [x] 最终目录通过 `/plugins install` 装入宿主注册表，`/plugins info` 正常，依赖闭包完整；版本高于旧版且各处一致。
- [ ] ZIP URL 安装、重复安装、`/plugins reload` 与 `/plugins remove` 尚未实测。
- [x] Windows 真实会话完成 capture → commit → extraction → recall；POSIX 和未测场景明确标注，不用测试总数代替范围。

**当前状态（2026-11-16）：** 验收证据与未验证边界见附录 B。活动会话的主会话因开放尾部保持未提交，这是 `open_turn` 守卫的预期行为。

## 附录 A：复核宿主内嵌源码的方法

`kimi.exe` 是 PE32+，本机 2.6.2 的 `.rdata` 中直接包含带 `// ../../packages/.../file.ts` 标记的 JavaScript，不需要 SEA 解包或反编译。以下命令在 Git Bash 中按模块名提取只读源码窗口，不写提取文件：

```bash
export KIMI_EXE="${KIMI_CODE_HOME:-$HOME/.kimi-code}/bin/kimi.exe"
python - "$KIMI_EXE" \
  "agentExternalHooksService.ts" \
  "externalHooks/internal/runHook.ts" \
  "wire/tree/tree.ts" <<'PY'
from pathlib import Path
import sys

data = Path(sys.argv[1]).read_bytes()
for module in sys.argv[2:]:
    anchor = module.encode("utf-8")
    pos = data.find(anchor)
    print(f"\n===== {module} @ {pos} =====")
    if pos < 0:
        continue
    start = data.rfind(b"// ../../", 0, pos)
    end = data.find(b"// ../../", pos + len(anchor))
    print(data[start:end].decode("utf-8", "replace"))
PY
```

模块名应使用足够独特的路径片段；`-1` 表示未找到，不能据此推断行为。offset 只用于当次定位，不作为长期文档引用，也不提交提取出的源码。

本设计使用的主要证据模块：

| 宿主事实 | 2.6.2 内嵌模块 / 符号 |
| --- | --- |
| manifest、hooks、MCP、Skill | `app/plugin/manifest.ts`、`app/plugin/manager.ts` |
| hook 事件与输入输出 | `features/externalHooks/internal/{types,matchHooks,runHook,userPrompt}.ts` |
| Agent 生命周期 | `features/externalHooks/agent/agentExternalHooksService.ts` |
| Session 生命周期 | `features/externalHooks/session/sessionExternalHooksService.ts` |
| 进程创建与回收 | `os/backends/node-local/hostProcessService.ts` |
| wire metadata 与记录集 | `wire/record.ts`、`wire/human.ts` |
| steer 与 undo | `agent/undo/turnUndo.ts`、`agent/loop/turnOps.ts` |
| 活动分支与 fork | `wire/tree/{tree,fork}.ts`、`workspace/sessionLifecycle/` |
| session index / state | `workspace/sessionLifecycle/sessionLifecycleService.ts`、`session/sessionMetadata/`、`session/agentLifecycle/subagentMetadata.ts` |
| MCP 工具命名 | `mcpCore/tool-naming.ts` |

共享层关键证据：`agent-hook-runtime.mjs`、`config-schema.mjs`、`capture-utils.mjs`、`batch-send.mjs`、`pending-queue.mjs`、`mcp-proxy-config.mjs`、`mcp-proxy-core.mjs`，以及 `openviking/session/memory/experience_lineage.py`。宿主安装证据来自 `app/plugin/manager.ts` 的 `installInternal()` / `remove()` 和 TUI `src/tui/commands/plugins.ts`。

## 附录 B：实现验证与未验证边界

后续实现已完成 Windows Node.js 24 下的插件行为测试、共享核心回归、hook contract、fake OpenViking 端到端、Experience lineage 测试，以及最终 ZIP 解包后的六个入口和 MCP 握手 smoke。真实 OpenViking 0.5.5 上另完成了宿主注册表安装、宿主触发的 `UserPromptSubmit` 注入、hook 路径的捕获/提交/提取（主会话与三个 subagent 均完成提取）、MCP `health`/`search` 与直接召回注入；真实轮次结束时 `Stop` 送达并提交已关闭轮次，真实 compact 触发了 `PreCompact` 并提交压缩前的关闭轮次，TUI 退出时 `SessionEnd` 写入结束标记并提交全部已结算 agent；真实 backfill 导入主会话 1530 条消息。Windows 无 symlink 权限的用例明确跳过，不以测试总数替代覆盖范围。

仍未完成以下验收：

- 提取质量与延迟测量，以及真实服务端的故障恢复；
- POSIX、远程/Web 环境和真实 turn-sliced fork 的宿主交互；
- fork 继承前缀的显式导入（当前 backfill 仍按 `forked` 边界跳过）；
- 媒体二进制导入（本设计不支持）。

上述剩余项的宿主契约已从 **Kimi Code 2.6.2** 内嵌源码核对：`hostProcessService.ts` 的 POSIX detached 启动与 `process.kill(-pid, SIGTERM)`、Windows `taskkill /T`；`externalHooks/internal/types.ts` 的 `Stop`/`PreCompact`/`SessionEnd` 事件；`wire/tree/{fork,tree}.ts` 的 `base.{branch,line}` 与 `legacyUndoLine`；`sessionLifecycleService.ts` 的 `environmentId`/cwd。源码验证只证明宿主契约，不等同于 POSIX、远程环境或真实 fork 的运行时验收。

生成物一致性由 `sync.test.mjs` 持续校验；本插件的生成副本须随源码一并纳入版本控制。上述未验证项目仍以第 11 章的最终产物验收为准。
