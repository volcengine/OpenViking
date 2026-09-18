# Hook + MCP Agent 插件开发与维护规范

本文规定如何新增和维护通过生命周期 hook 自动读写记忆、通过 MCP 提供工具的 OpenViking Agent 插件，涵盖模块职责、协议、状态、安装、测试和发布。宿主是指承载 Agent 的客户端或运行时，代码中也称 harness。

新增宿主时，应主要实现事件、消息格式、上下文注入和安装方式的差异。配置解析、鉴权、召回、捕获过滤、网络请求和离线重试应复用共享实现。Claude Code、Codex 和其他插件可作为参考，但仍需核对目标宿主的实际契约。MCP-only 或原生工具集成可采用相关规则，不必补齐不适用的 hook 能力。

## 使用 VibeCoding 开发插件

使用 VibeCoding 或其他 AI 辅助编程方式新增、修复、重构 Agent 插件时，**必须让 coding agent 在修改前完整阅读并遵循本文**。将文档路径和具体任务一起交给 Agent，要求它先核实宿主契约，再实施，并按验收检查单报告结果。生成了代码或测试通过，都不能替代对协议、恢复和安装产物的验证。

优先参考 [Claude Code](./02-claude-code.md) 和 [Codex](./04-codex.md) 插件，了解共享能力如何接入原生 hook 与 MCP；配置文件式宿主可参考 `agent-hook-plugin`，常驻扩展可参考 OpenCode、DSH 和其他插件。参考其职责划分和已验证行为，不要让 Agent 整目录复制，也不要要求所有宿主照搬同一套事件。

下面的提示词可以直接交给 coding agent；把最后一行替换为具体任务：

```text
Before changing any OpenViking agent plugin, read and follow
docs/en/agent-integrations/18-plugin-development.md
(Chinese: docs/zh/agent-integrations/18-plugin-development.md).

Inspect examples/memory-plugin-shared/lib/ and the Claude Code and Codex
plugins. Also inspect agent-hook-plugin, OpenCode, DSH, or another existing
integration when its host model matches the task. Verify the target host's
events, payloads, output schema, time limits, and installation contract.

Keep shared behavior in the shared modules and host differences in the
adapter. Do not copy a whole plugin or edit generated shared files. Cover
configuration, hook/MCP identity, capture acknowledgements, commit recovery,
installation, versioning, and bilingual documentation as applicable.

Before finishing, use the guide's acceptance checklist and report what was
changed, what was verified, and any remaining limitations.

Task: <describe the plugin addition, fix, or maintenance change>
```

## 1. 设计原则

共享库存在，并不代表各插件真正共享行为。配置只有在共同的执行链上解析和消费，才能保持一致；各宿主分别解释开关，会导致不同拼写、不同默认值，甚至配置能读到却不起作用。分发文件也应从依赖关系推导，避免源码通过测试，安装后却缺少 import 所需文件。

因此，本规范要求：

1. **同一行为有一个权威实现**。共享模块拥有规则，适配器提供宿主事实；不允许在适配器中复制一套“略有不同”的规则。
2. **统一必须发生在执行链上**。调用 `buildPluginConfig()`、`buildRecallBlockDetailed()` 或共享发送器，才构成复用。复制文件、导出同名函数、约定大家保持一致，都不足以防止分叉。
3. **能推导的清单不手写**。配置键、诊断键、workspace 映射来自 schema；运行时文件集合来自 import 闭包；安装包必需文件来自入口和 manifest。
4. **保留真实差异**。Claude Code 的子代理事件、Codex 的退出补偿、ZCode 的严格输出格式，各有明确原因。统一它们的公共能力，不强迫它们拥有相同的生命周期。
5. **交付方式决定生成时机**。用户直接加载仓库目录时，目录必须已经完整；用户安装构建产物时，在打包前生成依赖。不为减少 diff 破坏安装，也不为方便开发提交不需要的生成物。
6. **每次抽象都减少维护点**。新接口应让后续修复少改一个地方。若只是多了一层转发、更多布尔参数或第二套配置表，应重新考虑。

这些原则与仓库[贡献指南的职责与设计要求](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING_CN.md#职责与设计)一致。衡量改动是否合理，要看一个新维护者能否沿调用关系找到规则、解释失败、完成交付。代码行数和文件数量只是结果。

## 2. 接入前先确定宿主契约

开发前必须完成以下接入记录，放在插件 README 或必要的 DESIGN 文档中。记录应引用宿主文档、对应版本源码或实测结果，不能用另一款 Agent 的行为补全空白。

| 要确认的内容 | 必须写清的问题 |
| --- | --- |
| 版本与平台 | 最低宿主版本、Node.js 版本、已验证操作系统；较旧版本如何降级 |
| 安装方式 | 原生插件、marketplace、配置文件 hook 或 npm 扩展；宿主实际加载哪些目录 |
| 事件 | 启动、提交用户输入、轮次结束、压缩前、会话结束、子代理事件分别是否存在 |
| 输入 | stdin 是 JSON 还是其他格式；session、cwd、transcript、turn 的字段名及缺失条件 |
| 输出 | 注入字段、允许/拒绝字段、空结果形式、退出码；未知字段是否导致整份输出被丢弃 |
| 时间限制 | 单位、最大值、宿主是否截断配置值；超时后杀单进程还是整个进程组 |
| 消息来源 | transcript 或 rollout 的格式、写盘时机、稳定消息 ID、工具调用与结果如何关联 |
| 进程模型 | 是否复用 hook 进程；后台进程能否存活；多个窗口和会话是否并发 |
| MCP | 配置格式、stdio 支持、根路径变量、启动 cwd、环境变量继承、工具命名空间 |
| 恢复 | resume、clear、异常退出、压缩和 transcript 截短后，哪些身份与状态仍然有效 |

每项能力标记为“已验证”“有降级实现”或“不支持”，并说明验证版本。不能把 `Stop` 写成“会话结束”，也不能注册一个宿主不会触发的事件后宣称能力完整。最低服务端版本由实际使用的 API 和 URI 能力决定；例如当前共享召回要求服务端支持 `viking://~`，局部的旧接口回退不代表任意旧版本都兼容。

### 2.1 选择最小的接入形态

| 宿主条件 | 应采用的形态 | 参考 |
| --- | --- | --- |
| 通过配置文件安装 hook 和 MCP；公共调度足够表达生命周期 | 在 `agent-hook-plugin/hosts/` 增加适配器及宿主配置 | [agent-hook-plugin](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README.md) |
| 原生插件要求独立 manifest、目录和生命周期入口 | 独立插件目录，入口调用共享运行时 | [Claude Code](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md)、[Codex](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) |
| 以宿主 SDK 回调运行，需要常驻状态或 dispose/idle 回调 | 使用宿主扩展包，复用共享能力，明确自己的会话调度 | [OpenCode](https://github.com/volcengine/OpenViking/blob/main/examples/opencode-plugin/README.md)、[DSH](https://github.com/volcengine/OpenViking/blob/main/examples/dsh-memory-plugin/README.md) |
| 只有 MCP，没有自动注入或完整会话记录 | 交付 MCP-only 集成，明确能力范围 | [Agent Plugins](https://github.com/volcengine/OpenViking/blob/main/agent-plugins/README.md) |

只因新增宿主名称，不应复制 Claude Code 或 Codex 的整个目录。反过来，如果宿主有独立的会话状态机，也不应不断往公共 dispatcher 加 `isFoo`、`specialStop` 一类开关来容纳它。

## 3. 模块职责与依赖方向

```text
宿主事件 / transcript                    宿主 MCP 客户端
          ↓                                     ↓ stdio
事件、消息、输出适配器                    薄 MCP 入口
          ↓                                     ↓
runHookStage + 宿主生命周期调度           buildMcpProxyConfig
          ↓                              createOpenVikingMcpProxy
共享 recall / capture / session 能力             ↓
          ↓ createOvHttp                   共享 MCP transport
          └────────── buildOvHeaders ────────────┘
                               ↓
                         OpenViking Server

buildPluginConfig / credentials 为两条链提供配置和身份
sync / install / pack 负责把这张依赖图完整交付到机器上
```

共享能力的源文件位于 [`examples/memory-plugin-shared/lib/`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared/lib/)。下表是定位规则的入口，不是需要在每个插件重建的目录模板。

| 责任 | 权威模块 | 宿主可以提供的差异 |
| --- | --- | --- |
| 配置声明、默认值、别名、范围 | [config-schema.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/config-schema.mjs) | 有证据的宿主默认值差异，仍在 schema 声明 |
| 分层配置、完整配置对象 | [plugin-config.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/plugin-config.mjs) | harness ID、manifest、日志文件名、宿主原生参数 |
| 凭据和鉴权模式 | [credentials.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/credentials.mjs) | 已有兼容要求；不得再写 fallback 链 |
| workspace、peer 身份 | [workspace-peer.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/workspace-peer.mjs)、[workspace-identity.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/workspace-identity.mjs) | 当前会话的真实 cwd、宿主明确传入的 peer |
| hook 初始化、bypass、单次输出 | [agent-hook-runtime.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/agent-hook-runtime.mjs) | 输入读取、session ID 解析、启用谓词、输出 envelope |
| HTTP 头、超时、错误结果 | [ov-http.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/ov-http.mjs) | 请求路径、正文、调用预算、当前 actor peer |
| 召回和上下文构建 | [recall-core.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/recall-core.mjs)、[profile-inject.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/profile-inject.mjs) | 查询、会话身份、本地压缩器回调、宿主显示 |
| 消息清洗、角色和结构化内容 | [capture-utils.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/capture-utils.mjs)、[input-filters.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/input-filters.mjs) | transcript 格式解码、原生工具事件归一化 |
| 批量发送、离线重放、重试分类 | [batch-send.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/batch-send.mjs)、[pending-queue.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/pending-queue.mjs)、[retryable.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/retryable.mjs) | 何时调用、发送成功后如何推进宿主游标 |
| 后台写入 | [async-writer.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/async-writer.mjs) | 宿主允许的 detach 时机和恢复措施 |
| MCP 配置与协议 | [mcp-proxy-config.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/mcp-proxy-config.mjs)、[mcp-proxy-core.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/mcp-proxy-core.mjs) | 配置投影、日志工厂、确有必要的本地工具 |
| 虚拟 URI 检查、诊断 | [uri-guard.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/uri-guard.mjs)、[doctor-core.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/doctor-core.mjs) | 工具名、拒绝与提示 envelope、宿主安装与状态检查 |

依赖必须从宿主适配器指向共享能力。共享能力不能 import 某个宿主目录；需要宿主动作时，由调用者传入小而明确的回调。不要为一次文件读取引入通用插件容器、服务定位器或继承体系。共享模块也不能反向依赖安装器、测试代码或用户界面。

### 3.1 适配器应该有多薄

“薄”指它只拥有宿主差异，不设行数上限。例如 [cc-transcript.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/scripts/cc-transcript.mjs) 负责 Claude 消息块和嵌套 `tool_result` 的转换；[Codex capture-utils.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/scripts/capture-utils.mjs) 还要展开嵌套工具活动并消除同一调用的重复表示，因此可以更长。两者都应把通用内容处理交给共享代码。

新增薄宿主时，优先使用现有 [`HOSTS`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/index.mjs) 和 dispatcher 支持的 `stages`、`envelope`、`prompt`、`normalizeInput`、`capture`、`guard`。需要新增接口时，先说明哪一个宿主事实无法表达，再决定是否扩展；不要预先设计覆盖所有未来 Agent 的适配器 DSL。

现有 `hosts/cursor.mjs`、`hosts/trae.mjs` 和 `hosts/zcode.mjs` 表明，事件映射适合数据，消息转换适合纯函数，异步捕获适合显式回调。不要把这三种东西压进一个包含网络访问的“配置对象生成器”。

## 4. 配置、凭据和身份

### 4.1 一次声明，统一解析

新行为配置必须先在 `config-schema.mjs` 声明规范名称、类型、默认值、范围、所属能力，以及适用的环境变量、旧别名、workspace 键。解析入口使用 `buildPluginConfig(harness, options)`；适配器只投影宿主需要的字段。禁止新增插件私有的 `config.json` 来重复保存共享默认值，也禁止另写 doctor 已知键表或 workspace 键表。

普通行为配置从高到低按以下顺序解析：

1. `OPENVIKING_*` 环境变量。
2. 本机 workspace registry。
3. workspace 的 `.openviking/config.local.json`。
4. workspace 的 `.openviking/config.json`。
5. `ovcli.conf` 的 `plugin.<harness>`。
6. `ovcli.conf` 的 `plugin`。
7. 旧 `ov.conf` 中对应宿主的配置段。
8. schema 默认值。

宿主 SDK 直接提供参数时，通过共享 builder 的显式参数表达，记录优先级。现有 DSH 的宿主配置参与兼容层，`hostInput` 还能固定连接或 peer；这些有具体含义的入口，不能被解释为适配器可以任意覆盖最终配置。

配置示例只放必要值。例如下面的 `ovcli.conf` 片段为共享 resolver 提供召回默认值，并对 Codex 关闭自动捕获；环境变量和 workspace 等更高优先级配置仍可覆盖：

```json
{
  "plugin": {
    "autoRecall": true,
    "codex": {
      "autoCapture": false
    }
  }
}
```

新增宿主应在 `HARNESS_KEYS` 注册规范 ID，并验证连字符与下划线别名是否按现有约定解析。规范名称与旧别名同时出现时，由共享 resolver 决定结果；同一层规范名称优先。不要在适配器里再做一轮互相冲突的别名转换。

必须区分“未设置”“显式设置为 false”和“最终值等于默认值”。schema 中 `sendOnlyWhenConfigured` 控制的字段，只有实际配置后才发送，避免客户端默认值覆盖服务端默认值。不要用 `value || default` 处理允许 `false`、`0` 或空字符串的字段；是否允许这些值由字段语义决定。

开关必须控制对应的真实行为：关闭 recall 后不发起自动召回，关闭 capture 后不新增本会话的自动捕获和由它触发的提交。历史 pending 的恢复是否继续，要单独定义并验证，不能混为“新采集”。共享能力中的 `isRecallEnabled()`、`isCaptureEnabled()` 是最终保护；适配器可以提前退出，但不能成为唯一检查点。`mcpEnabled` 等配置只有宿主真正消费后，才可以在该宿主文档中宣称支持。

### 4.2 凭据不是普通 workspace 配置

连接和身份必须走 `credentials.mjs` 的 `resolveConnection()`，`buildPluginConfig()` 就是调用它。`OPENVIKING_CREDENTIAL_SOURCE` 的 `auto`、`cli`、`env` 控制凭据来源，不能简单套用行为配置优先级。MCP proxy 导出 `readProxyConfig(env)`，经与 hook 相同的 loader 解析，再用 `toMcpProxyConfig()` 映射，不手工挑字段，也不直接调用 `credentials.mjs`。宿主若只把白名单里的环境变量交给 MCP 进程，白名单必须覆盖 `MCP_PROXY_ENV_VARS`；宿主若给的是封闭环境，就用 `forwardConnectionEnv()` 转发解析好的连接。新 proxy 必须加入 `mcp-hook-parity.test.mjs`，缺行时该测试会失败。

workspace 文件不得包含 URL、API key、用户凭据等禁止字段，也不做环境变量插值。规则由 [`workspace-config.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/lib/workspace-config.mjs) 执行，不在每个宿主增加自己的白名单。安装器不得把解析后的 API key 固化到 `.mcp.json`，也不得替换用户已选择的云端连接。

请求头由 `buildOvHeaders()` 构造：API key 使用 `Authorization: Bearer`，不再另发 `X-API-Key`；只有解析结果 `sendIdentityHeaders` 为真时才发送 account/user 头。保留 `User-Agent` 和错误中的 `traceId`，便于确认实际运行版本和追踪请求。不要在健康检查、状态栏或诊断探针里另写鉴权。

诊断中的 `credentialSource` 表示凭据解析模式，`apiKeySource` 表示 key 的实际来源；两者不能混用。源码中存在为旧安装保留的 `rootKeyFallback`，新宿主不得因为参考插件启用了它，就无条件复制这个选项。

### 4.3 会话身份与 peer 分开处理

原生 session ID 标识一次会话，peer 标识项目记忆归属，二者不能互换。写入使用稳定的宿主 session ID 和明确的宿主前缀；多个窗口、两个相同 cwd 的会话、主代理和子代理不能意外共用写游标。前缀和现有会话 ID 算法属于数据兼容约定，改名时必须说明旧状态如何继续读取。

peer 解析使用共享实现。现状默认从 Git 身份推导，普通非 Git 目录不自动分配 peer；标记文件可显式指定。worktree、子目录和 fork 的行为见[共享库说明](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#workspace-peers)。hook 必须以 payload 中的真实 cwd 重新解析 workspace 配置，不能把插件安装目录当作项目。

长期运行的 MCP proxy 不能从启动 cwd 推导当前项目。必须使用 `resolveMcpActorPeerId()`：默认宽范围读取不发 actor peer 头，actor 范围需要显式 peer。现有共享实现在缺少显式 peer 时发出警告并退回宽范围，文档必须如实说明。peer 是已认证用户内部的记忆归属和检索范围，不能把它宣称为不同用户之间的授权隔离。

## 5. Hook 生命周期与可靠写入

### 5.1 通用阶段和宿主事件的对应关系

| 阶段 | 通用责任 | Claude Code 现状 | Codex 现状 |
| --- | --- | --- | --- |
| 会话启动 | profile 注入、必要的恢复工作 | `SessionStart` | `SessionStart`；区分 startup、clear、resume |
| 用户提交 | 过滤查询、召回、注入上下文 | `UserPromptSubmit` | `UserPromptSubmit` |
| 轮次结束 | 从可靠来源补齐新消息，保存进度 | `Stop`；另有阈值提交 | `Stop`；通常追加，不等同会话结束 |
| 压缩前 | 确认压缩前消息已写入，再执行相应提交 | `PreCompact` 提交已有消息；本入口不补采 transcript | `PreCompact` 补齐 transcript 后提交 |
| 会话结束 | 完成尚未完成的写入与提交 | `SessionEnd` 提交已有消息；本入口不补采 transcript | `SessionEnd` 后台补齐并提交，保留启动补偿 |
| 子代理 | 保留身份和父子关系，避免重复 | `SubagentStart`、`SubagentStop` | 当前 hook manifest 没有这两个事件 |
| 本地工具检查 | 文件工具的路径是虚拟 URI 时拒绝；shell 命令带虚拟 URI 时附加提示 | `PreToolUse` URI guard，匹配 Read、Glob、Grep、Edit、Write、Bash | `PreToolUse` URI guard 只匹配 Bash，只附加提示；文件编辑走 `apply_patch`，没有路径参数 |

以两份 [Claude Code hooks.json](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/hooks/hooks.json) 和 [Codex hooks.json](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/hooks/hooks.json) 为事件注册依据。相同事件名称不保证 payload 或输出格式相同。没有结束事件的宿主必须选择并说明替代提交点，例如 ZCode 在 Stop 提交；不能假装存在一个永远不会执行的 SessionEnd。

### 5.2 Hook 入口的固定职责

入口使用 `runHookStage()` 处理 stdin、按会话 cwd 重载配置、启用和 bypass 判断，并保证只输出一次。宿主特有 session 字段通过 resolver 传入，不能为兼容一个字段名而关闭 bypass。输出必须严格符合当前事件的宿主协议：`decision: "approve"` 不是通用格式，Cursor 的 `additional_context` 与其他宿主的 `hookSpecificOutput.additionalContext` 也不能混用。

stdout 只承载宿主约定的结果，日志写 stderr 或共享日志文件。无结果、禁用、配置不可用、网络失败时，都应产生该事件合法的空结果，避免让记忆服务故障阻断用户正常交互。URI guard 的明确拒绝则应保留拒绝语义，不能被统一异常处理改成允许。

不要在普通模块 import 时读 stdin、启动子进程、联网或退出进程。这些副作用属于入口或显式调用的函数。共享函数不能为了方便调用者直接 `process.exit()`；可复用入口需要可测试的主函数和明确的启动条件。

### 5.3 Recall 与 profile 注入

profile 与逐轮召回分别使用 `buildProfileBlock()` 和 `buildRecallBlock()` / `buildRecallBlockDetailed()`。宿主可提供压缩器、显示结果和统计信息，不能重写检索目标、排序、token 预算和服务端兼容回退。状态栏需要计数时，应消费共享结果和最终注入内容，不能再跑一次召回推算。

自动召回必须携带正确会话身份和 peer，并遵守 input filter、bypass 和开关。空结果应保持为空，不把服务端的“无相关记忆”占位文本当成记忆注入。压缩失败可退回已有的未压缩结果；不得凭空补写摘要。压缩后的 `viking://` URI 必须仍可读取，原始用户问题、召回块与宿主包装也必须能在 capture 时区分，避免重复写入注入的旧记忆。

如果使用宿主 CLI 压缩内容，必须隔离这次辅助调用的自动记忆 hook，限制执行时间，并复用已有压缩接口。不得启动一个再次触发自身 recall/capture 的递归 Agent。模型选择和调用方式属于宿主适配，通用压缩结果处理属于共享层。

### 5.4 Capture 的数据与确认规则

优先读取宿主提供的完整 transcript/rollout，stdin 只在已验证缺少完整来源时作为补偿。Stop payload 不一定有用户输入，不得用最后一条助手输出伪造一轮完整对话。文件暂时不可读和“没有新消息”必须是不同结果。

宿主解析器先把外部记录转换成共享捕获模型，再调用共享清洗和发送逻辑。文本与结构化工具内容分开保留：工具名、调用 ID、输入、输出、状态以及可取得的 turn ID，应来自真实记录。不要把同一次工具调用的原生事件、MCP 结果和嵌套表示重复记成几次调用；也不要仅保留最终助手文本而丢掉工具活动。正文过滤不能意外删除仅包含工具的有效记录。

过滤与截断策略使用 `capture-utils.mjs` 和 `input-filters.mjs`。工具输出的正文摘要与结构化 `tool_output` 不是同一个字段；当前实现把大输出交给服务端外置，客户端保留防止异常载荷的上限。不要为了压缩文本摘要把结构化证据一起截掉。

写入遵守以下顺序：

```text
读取新记录 → 解码与过滤 → 按顺序发送
                          ├─ 服务端确认：推进已发送进度
                          ├─ 已持久化到 pending：可推进交接进度，仍未送达服务端
                          └─ 发送与入队均失败：保留原进度，等待恢复
```

必须保留以下不变量：

- 游标只推进到连续确认的位置。共享发送器返回 `sent` 与 `queued`；允许按两者推进的适配器，必须确认 `queued` 表示已落盘，并保留后续重放责任。不能把尝试次数当成功次数。
- 去重优先使用稳定消息 ID、turn ID 或 transcript 位置。相同文本可能是两个合法轮次，不能只按文本 hash 永久去重。
- 多进程更新同一状态时必须加会话锁；状态写入使用临时文件和原子 rename。锁等待、陈旧锁回收和持有时间必须相容。长任务需要能证明所有权与存活的锁，不能直接套用更短的 stale TTL。
- 消息发送成功但状态保存失败后的重放，应有可解释的重复处理策略。没有服务端幂等保证，就不能宣称 exactly-once。
- transcript 截短、resume 或 compaction 不能让游标永久越过新记录；必须说明如何识别并恢复。
- 子代理记录由明确的一方采集。若主 transcript 已包含子代理内容，单独的子代理 hook 不得重复导入同一记录。

### 5.5 Commit、重试和退出

Commit 表示请求服务端归档并触发处理，不代表长期记忆已经提取完毕。HTTP 成功、任务已受理、归档完成和提取完成要分别报告，不能把 `task_id` 的出现解释成所有工作已完成。

同一会话应先补齐消息，再提交。部分消息失败时，应保留活动 session 和未完成状态；采用 pending 交接的实现，还必须证明 commit 不会越过未重放的消息。**复用 pending queue 本身，不等于自动获得消息与 commit 的事务顺序**。接入时要验证完整失败序列，并按宿主实际情况选择延后 commit 或受顺序约束的重放。

共享重试分类的现状是：网络失败、408、429、5xx 可重试；409 只有明确标记 `error.details.retryable` 时才可重试。401/403 和普通参数错误不能不断入队。批量发送由 `sendSessionMessages()` 控制，每批最多 100 条，批量接口返回 404/405 时回退串行发送。宿主不得另写一套状态码列表或分批循环。

入队时必须保留原始 payload，包括 `keep_recent_count` 等提交参数；重放仍执行原操作。提交失败不能提前清除活动 ID、结束标记或待补消息。pending 有重试次数、重放批量和 TTL 限制，属于有界恢复能力，不能向用户承诺离线数据永久保留。

退出预算按宿主实测设置。当前 Codex SessionEnd 的上限为 3 秒，入口先写 `.ended` 标记，再启动 worker；下次 SessionStart 扫描未完成或过期活动会话。resume 不等于结束，旧 worker 也不能提交已经恢复的新会话，因此标记与清理需要对应同一次结束事件。详细依据见 [Codex commit design](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md)。

后台写入使用 `maybeDetach()` 和 `readHookStdin()`，必须正确转交已经读取的 stdin，防止父子进程各读一次后丢失 payload。detach 成功只表示 worker 已启动；还要验证 worker 在宿主退出后能否存活，失败时如何通过 transcript、pending 或结束标记恢复。不能把返回合法空结果当成写入成功。

同步收尾要给锁等待、消息补齐和 commit 一个总体时间预算，各步骤使用剩余时间。不得让每一步重新获得整个 hook timeout。后台 worker 也必须有界，不能让卡住的网络请求永久持有锁。

服务端字段、配置回显或 API 存在，不足以证明自动提交会在实际消息路径触发。若要把提交职责从插件交给服务端，必须验证运行行为、旧服务端降级和不会重复提交；不能仅凭一个 `auto_commit` 配置删掉插件的可靠提交点。

## 6. MCP 与模型可见工具

Hook 提供自动生命周期行为，MCP 提供模型主动调用的工具。两者通过同一套配置和身份连接 OpenViking，但职责独立。插件启动时应能回答：哪些内容自动注入，哪些操作必须由模型调用，哪些行为禁用后仍可使用 MCP。

支持 stdio MCP 的宿主，应采用 Claude Code、Codex 已使用的共享 proxy：入口解析配置，经 `buildMcpProxyConfig()` 整理，再交给 `createOpenVikingMcpProxy()`。HTTP transport 有 MCP 自身的 session、SSE 和协议协商，不能简单用 REST 的 JSON helper 替换；公共鉴权头仍由 `buildOvHeaders()` 生成。

proxy 入口不得拥有自己的工具 schema、副本 API client、SSE parser 或重试状态机。`tools/list` 和 `tools/call` 应以服务端为准，不能为了改工具描述而维护第二套内存工具目录。确需本地工具时，使用共享 `localToolProvider` 接口并限定范围，工具名不得意外覆盖服务端工具。

新增宿主必须验证：

- 从宿主实际工作目录启动，所有相对路径、根路径变量和环境变量白名单都有效。Codex `.mcp.json` 的 `env_vars` 是具体宿主要求，不能推断其他宿主会同样继承。
- `initialize` 的协议协商、通知无普通响应、JSON 与 SSE 响应、并发响应 ID 均正确，stdout 无日志污染。
- MCP session 过期和凭据文件变更使用共享恢复逻辑；未经限定的工具调用不得在网络错误后盲目重试，尤其是有写副作用的操作。
- hooks 与 MCP 在环境变量、默认文件、自定义文件和 profile 切换后仍使用预期的 URL 和身份。用实际请求头和请求目标验证，不能只看配置文件内容。
- 诊断和文档列出的工具名与宿主实际显示一致；`remember` 不能随意写成 `store`，工具命名空间也不能照抄另一宿主。

直接连接远程 MCP 只有在宿主确有合适的凭据和配置能力时才采用，并记录与 hook 保持一致的办法。不要增加 wrapper、环境文件或安装时重写来绕过已经能解决问题的共享 proxy。

## 7. URI guard、Skill 和诊断

`viking://` 是虚拟 URI。本地文件工具的路径参数是 `viking://` URI 时必然失败，所以宿主支持工具执行前检查时，用 `evaluateUriGuard()` 拒绝这次调用。shell 命令里的 `viking://` URI 可能只是数据（`ov` 命令参数、HTTP 请求体、搜索模式），所以命令照常执行，再通过宿主的模型可见上下文通道附上 `evaluateUriNotice()` 生成的提示；`PreToolUse` 类宿主直接用 `preToolUseOutput()`，它返回拒绝或提示 envelope。适配器中只定义替代工具提示和 envelope。检查器不能扩展成一般命令拦截器；普通文件路径应保持原有行为。宿主不支持该事件时，明确限制并通过 Skill 指引模型使用 MCP，不得宣称具备等效拦截。

共享 Skill 的源文件放在 [`examples/skills/`](https://github.com/volcengine/OpenViking/tree/main/examples/skills/)，通过 `SKILL_TARGETS` 交付，禁止在多个插件副本里分别修改同一段指导。Skill 只描述真实可调用工具和实际能力；自动 hook 已处理的捕获、提交不应再要求模型每轮手动重复执行。不同工具集确有不同操作语义时，可以保留独立 Skill，并说明理由。生成 Skill 时不能在 YAML frontmatter 前插入生成标记。

doctor 使用 `runDoctor(hostSpec)`，宿主只补充安装位置、manifest、hook 注册、状态文件等检查。公共配置、凭据、网络和输出格式由 `doctor-core.mjs` 负责。必须能够检查安装版本、配置来源、生效值、peer、MCP 入口、hook 时间限制和 pending/会话状态。优先提供离线模式和 JSON 输出，离线检查不应偷偷发起网络请求。

排障信息至少能区分禁用、bypass、无结果、超时、鉴权失败、已入队、入队失败与提交失败。日志保留宿主、阶段、会话关联、耗时和 trace ID；默认不写 API key、完整 Authorization 或整份用户 transcript。doctor 的建议必须是适用于当前安装形态的真实命令，不能指向已经删除的 debug 脚本。

## 8. 文件布局、生成与安装

### 8.1 推荐布局

以下是位置约定，尖括号表示接入时填写的名称，不要求创建所有文件：

```text
examples/memory-plugin-shared/
  lib/<capability>.mjs          共享行为源文件
  lib/<capability>.d.mts        需要时提供，与实现一同维护
  lib/install/                 安装器专用逻辑，不进入 hook 依赖闭包
  testing/support.mjs          测试辅助，不随运行时交付
  sync.mjs                     分发目标与闭包生成

examples/agent-hook-plugin/
  hosts/<host>.mjs             宿主适配器
  hosts/<host>/                宿主声明、hook/MCP 配置和必要资源
  scripts/hook.mjs             共用调度入口
  servers/mcp-proxy.mjs        共用 MCP 入口

examples/<host>-memory-plugin/ 独立原生插件需要时才创建
  <宿主 manifest 目录>/
  hooks/
  scripts/                    宿主入口、状态与 transcript 适配
  scripts/shared/             生成物，不手改
  servers/
  skills/
```

模块通过明确的相对 import 连接。公共模块有 TypeScript 消费者时，`.d.mts` 与 `.mjs` 放在同一个权威目录并一起生成。不要在每个产物目录手写一份类型声明。需要显式 re-export 时列出名称，避免 `export *` 与适配器的同名实现冲突。

### 8.2 按交付方式选择生成策略

| 交付方式 | 当前例子 | 生成要求 |
| --- | --- | --- |
| 宿主直接加载 Git 中的插件目录 | Claude Code、Codex、`agent-plugins`、OpenClaw（`ov-install` 的 GitHub 源） | 共享副本提交到 Git，checkout 后即可加载 |
| npm 包或安装归档 | OpenCode、DSH、Pi | 在 prepack 或 staging 时生成；运行时副本不提交 Git |
| 安装器组装相邻运行时目录 | Cursor、TRAE、TRAE CN、ZCode | 使用 `ASSEMBLED_ROOTS` 推导 `lib/MANIFEST`，按 manifest 复制共享运行时 |

[`sync.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs) 是上述目标的登记处。新增独立插件登记 `TARGETS` 的 source root、目标目录和 `committed`；新增组装根才扩展 `ASSEMBLED_ROOTS`，普通薄宿主通常已被现有根覆盖。是否交付 Skill 另外登记 `SKILL_TARGETS`。不要维护一份“需要复制的 20 个模块”清单。

修改共享源码后运行：

```bash
node examples/memory-plugin-shared/sync.mjs
```

生成器分析静态 import 和字面量动态 import；依赖路径必须可分析，不能用字符串拼接隐藏必需模块。组装后的相对目录关系必须与源码一致，让同一个 import 在仓库和安装目录都成立。不要用绝对开发路径、临时 symlink 或 `NODE_PATH` 让本机测试侥幸通过。

PR 必须包含应提交的最新生成物，并检查新出现但未跟踪的文件。主线的 [`plugin-shared-sync.yml`](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/plugin-shared-sync.yml) 是额外保障，不能代替 PR 中的完整交付。只修改文档且未改变生成源时，无需为了走流程生成无关副本。

### 8.3 安装与卸载的行为要求

安装复用 [`install.sh`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/install.sh)，JSON/JSONC 合并放在 [`lib/install/`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared/lib/install/)，不要在 shell heredoc 内继续堆放大型 JavaScript。安装器应做到：

1. 在修改用户配置前完成解析、校验和所需文件准备。坏 JSON/JSONC 必须报错并保留原文件，不能将解析失败当作空配置。
2. 重复安装幂等，不增加重复 hook/MCP 条目；保留其他插件和用户配置，保留格式中有意义的注释。
3. 路径有空格、宿主不展开变量、自定义配置路径等情况都能正确处理；只向宿主写入它实际支持的字段。
4. 卸载按本插件拥有的条目删除，包括 URI guard；保留其他集成、凭据、记忆和会话数据。卸载不应要求重新下载源码。
5. 单个客户端卸载后，其他客户端依赖的共享运行时仍可用；失败时不能留下一半指向旧目录、一半指向新目录的配置。

安装包校验使用 [`stage-memory-plugin-marketplace.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/stage-memory-plugin-marketplace.sh) 和 [`check-marketplace-archive.mjs`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-marketplace-archive.mjs)。必需入口、传递依赖、Skill 中调用的脚本都必须存在；归档排除 `node_modules`、`.git` 和本机秘密。检查工具不能只相信 staging 提供的目录列表，还必须验证预期分发目标没有整项遗漏。

发布前至少从最终归档或 tarball 解包后执行一次入口和安装 smoke test。源码 import 成功，只能证明源码树完整，不能证明用户拿到的包完整。

## 9. Code smell 与可维护性评审

评审时应沿“宿主输入 → 规范模型 → 共享能力 → 状态确认 → 安装产物”阅读。每条规则要能找到唯一实现，每个副作用要能找到明确调用者。以下问题应在本次相关修改中解决，或给出具体的兼容理由；不能用“以后统一”解释新增重复。

| 信号 | 具体问题 | 应采用的处理 |
| --- | --- | --- |
| 复制后改名的 client、recall、doctor | 后续修复必须修改多份，行为逐渐分叉 | 将规则放回现有能力模块，宿主只传参数 |
| 一个字段出现在多个默认值表 | 配置、诊断与实际执行不一致 | 在 schema 声明，其他视图推导 |
| 配置被读取但没有消费者 | 用户修改开关却没有效果 | 沿真实调用验证，缺能力就不宣称支持 |
| 共享函数中密集判断 harness 名 | 共享层正在承担宿主协议 | 将事件、格式和策略移到适配器 |
| 为消除几行重复增加一组布尔开关 | 调用者必须理解多个互斥模式，非法组合增加 | 使用小回调或保留短而直接的宿主代码 |
| `utils.mjs` 同时处理解析、联网和提交 | 规则没有归属，测试也难以隔离 | 按能力拆分，解析尽量纯函数化 |
| loader 读配置同时写文件或启动服务 | 调用次数改变系统行为 | 将动作放在显式安装或生命周期入口 |
| 返回一个含糊的 `true` 表示“处理过” | 无法区分送达、入队和跳过 | 返回明确结果，保留错误与确认数量 |
| catch 后返回成功或空数组 | 文件不可读、鉴权失败被伪装成无数据 | 保留失败类别，在宿主入口决定合法降级 |
| 用全局变量表示“当前会话” | 并发窗口与子代理相互覆盖 | 以 session 为键持有状态，传入当前身份 |
| `export *`、多层同名转发 | 无法判断实际调用的是哪份实现 | 显式导入导出，删除无兼容价值的薄壳 |
| 注释承诺可靠提交，代码只 `spawn()` | 文案掩盖未确认的副作用 | 写清恢复条件，用实际状态证明完成 |
| 手动编辑 shared 副本或类型声明 | 下次 sync 覆盖修复 | 修改权威源并生成 |
| 在发布脚本手列所有共享文件 | 新依赖遗漏只在用户机器暴露 | 从依赖闭包与 manifest 校验 |
| 旧设计、未调用脚本和测试副本长期保留 | 搜索结果误导维护者，重复测试制造假覆盖 | 删除引用后移除；必要历史留在 Git |

函数名和变量名要反映动作和状态，例如 `parseTranscript`、`sent`、`queued`、`commitAccepted`，避免 `handleEverything`、`done`、`successLike`。注释解释宿主限制、数据不变量和取舍，不复述下一行代码。只为日志或兼容保留的 wrapper 应写清理由。

相似不等于相同。源码中调用同一个共享 builder 也不自动证明 hook 与 MCP 采用完全相同的凭据投影。遇到这种差异，先验证其公开行为，再决定修复或保留；不要把当前参考实现的每一行提升为规范。

## 10. 新增一个插件的实施顺序

1. **完成接入记录**。确认第 2 节的宿主事实，定义支持矩阵、最低版本、缺失事件的替代方案，以及会话 ID 和 commit 时机。
2. **选择接入形态**。优先增加现有薄宿主适配器；只有分发或生命周期确有要求才新增独立包。把决定写在 README/设计说明中。
3. **先接通配置与 MCP**。注册 harness ID，使用共享 builder 和 proxy，验证真实启动方式下的路径、环境和鉴权，并用同一配置驱动 hook。
4. **接入自动读取**。实现启动 profile 和逐轮 recall 的事件/输出转换，验证禁用、bypass、空结果和网络失败。
5. **接入可靠写入**。实现 transcript 解码、稳定身份、增量游标、确认更新、commit 和恢复。优先覆盖掉线、重复事件和尾部消息补齐。
6. **接入宿主辅助能力**。按支持情况增加 URI guard、Skill、doctor；不要为不存在的事件放空入口。
7. **完成安装和分发**。登记生成目标、安装器、归档/marketplace、版本检查和发布工作流，确保包里的入口可直接运行。
8. **验证并更新文档**。复用现有契约测试，补足宿主差异的验证，执行最终产物 smoke test，记录已测版本、平台与限制。

每一步应有可检查的产物，不以“目录已建好”或“工具列表能返回”作为全部完成。过程可以分成独立提交，但每个提交都应能解释其行为并保持已有接入可用。

## 11. 测试与验收证据

测试验证用户可观察的契约和主要失败情况。遵循贡献指南，优先扩展现有高价值测试，不为简单转发、新文件或几行配置机械地增加单测。共享能力测一次，宿主测试只验证接线和差异；不要复制整套 recall、pending 或 MCP 测试。

公共辅助函数放在 [`testing/support.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/testing/support.mjs)，不要从另一个测试文件 import helper，也不要放进随插件交付的 `lib/`。测试使用独立临时目录、隔离的配置和 pending 路径、动态端口；安装测试不得修改开发者正在使用的 Agent 配置或与其他测试共享被生成器改写的目录。

| 验证范围 | 至少覆盖的真实行为 | 现有入口 |
| --- | --- | --- |
| 配置与开关 | 分层覆盖、别名冲突、非法值、未配置标记、关闭后无新网络副作用 | `plugin-config.test.mjs`、`plugin-known-keys.test.mjs`、宿主 config 测试 |
| 凭据与 peer | hook/MCP 的请求身份一致；自定义路径、profile 切换、多 workspace | `credentials.test.mjs`、`mcp-hook-parity.test.mjs`、`wire-headers.test.mjs`、`mcp-proxy-config.test.mjs` |
| Hook 输出 | 真实 payload、单次合法输出、空结果、未知/缺失字段、错误路径 | `agent-hook-runtime.test.mjs`、宿主事件测试 |
| Recall | 开关、bypass、空结果、服务端兼容、压缩失败、可读 URI | `recall-core.test.mjs`、宿主 recall 测试 |
| Capture | 完整文本和工具记录、重复事件、重复文本、嵌套工具、部分成功、截短恢复 | `capture-utils.test.mjs`、`batch-send.test.mjs`、宿主 transcript 测试 |
| 状态与提交 | 并发 Stop/End、worker 失败、resume 与旧结束标记、不可读尾部、入队失败、提交参数保留 | `pending-queue.test.mjs`、Codex session 测试、宿主恢复测试 |
| MCP | initialize、协议版本、通知、SSE、过期恢复、错误语义、stdout | `mcp-proxy-core.test.mjs`，另加宿主启动 smoke test |
| 安装产物 | 冷安装、重复安装、保留第三方配置、坏配置不覆盖、卸载不残留、完整依赖闭包 | `install-agent-hooks.test.mjs`、`release-marketplace.test.mjs`、`sync.test.mjs` |
| 维护约束 | 请求头不再分叉、schema 与消费者匹配、生成物跟踪策略正确 | `one-header-builder.test.mjs`、`plugin-known-keys.test.mjs`、`sync.test.mjs` |

这些文件名均相对于共享库，除非注明宿主。完整命令以 [PR CI](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml) 和各插件 package scripts 为准；不要在新插件再维护一份容易过期的全仓库测试清单。源码结构检查用于保护关键的单一实现约束，不能代替行为测试，也不应锁死无关 helper 名称或行数。

例如，修改配置与 MCP 公共契约后，可先在仓库根运行以下聚焦检查，再运行受影响宿主的测试：

```bash
node examples/memory-plugin-shared/sync.mjs
node --test \
  examples/memory-plugin-shared/plugin-config.test.mjs \
  examples/memory-plugin-shared/credentials.test.mjs \
  examples/memory-plugin-shared/mcp-proxy-config.test.mjs \
  examples/memory-plugin-shared/mcp-proxy-core.test.mjs
```

安装和 marketplace 测试会生成运行时和组装安装目录，按 CI 单独串行执行：

```bash
node --test --test-concurrency=1 \
  examples/memory-plugin-shared/install-agent-hooks.test.mjs \
  examples/memory-plugin-shared/release-marketplace.test.mjs
```

当前 CI 使用 Node.js 24，其中部分 TypeScript 测试依赖原生类型剥离；测试环境版本和插件运行时最低版本必须分别说明。Node.js 下通过不代表 Windows 上的 detach、路径引用和进程回收已经验证。缺少实测的平台要明示，不用一个全绿数字替代验证范围。

验收报告应记录“用哪个产物、在什么宿主版本、执行什么场景、观察到什么”。对于写入，至少能确认服务端收到完整尾部消息和对应提交；对于安装，至少能确认解包后的入口可以找到依赖。只有文档修改时，检查语法、相对链接、引用符号和示例即可，无需自动运行整套插件测试。

## 12. 维护、发布和文档同步

### 12.1 按改动类型完成维护

| 改动 | 必须跟进的内容 |
| --- | --- |
| 共享行为修复 | 修改权威模块、验证受影响宿主、生成副本、检查所有相关分发产物和版本 |
| 宿主 payload 或事件升级 | 更新适配器、真实格式 fixture、最低版本/降级说明；不修改其他宿主的默认行为 |
| 新增配置项 | schema、真实消费者、configured 语义、诊断来源、用户文档；不得只加解析 |
| 增加公共模块依赖 | 同目录类型声明、sync 闭包、`lib/MANIFEST`、归档和离线安装验证 |
| 目录或包名变更 | import、manifest、installer、marketplace、CI path filter、Skill、链接与卸载识别 |
| 删除兼容逻辑 | 明确支持版本与迁移说明；不能顺手删除仍承诺支持的配置别名 |
| 纯文档改动 | 校验与现状一致、相对链接有效；不为说明文字改动无关行为 |

修复应从失败行为和负责的模块出发。若共享问题在单一宿主暴露，仍应在共享源修复；若只有一个宿主 payload 变化，就保持在该适配器内。大型重构按可独立验证、可回退的步骤推进，避免把行为变更、历史清理和分发调整混成无法归因的一次替换。

### 12.2 版本与发布链

版本号决定用户是否能拿到更新。新增插件必须接入适用的版本检查和发布工作流，不能只新增目录。现有 [`check-plugin-version-bumps.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-plugin-version-bumps.sh) 会把共享 `lib/` 变化计入其登记的每个插件；它不是所有分发目标都已自动覆盖的证明。增加或改变目标时，必须检查登记范围。

宿主 manifest、`package.json`、存在的 lockfile 根包版本、安装 manifest 和安装器判定版本必须一致。修改版本时使用相应包管理流程，不为一个版本数字重写无关依赖。`User-Agent` 和 doctor 应报告实际产物版本，避免用户显示已升级而代码仍旧。

npm/归档目标的构建必须从干净源码生成共享文件，再打包。当前 [`plugin-npm-release.yml`](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/plugin-npm-release.yml) 的矩阵覆盖 DSH 和 OpenCode；其他包要核对各自发布流程，不能因使用相同 prepack 就推断已接入这个矩阵。发布触发条件必须关注共享源变化，不能依赖已经不提交的 shared 副本发生 diff 才触发。

合并前按实际 base 检查版本，例如在本地 `origin/main` 已更新且确为目标分支时：

```bash
bash .github/scripts/check-plugin-version-bumps.sh origin/main
git diff --check
git status --short --untracked-files=normal -- examples agent-plugins
```

版本检查按已提交的 `base...HEAD` 识别变更，不会把未提交工作区当作完整 PR；最后一次检查应针对最终提交。生成器运行后的未跟踪文件也要审查，不能仅依赖 `git diff`。发布后的验证应检查用户实际下载到的包及版本，不以 CI 已启动作为完成依据。

### 12.3 文档是接口的一部分

插件 README 至少包含：支持范围和版本、最快可用的安装方式、凭据来源、自动行为与 MCP 的分工、核心开关、限制、升级/卸载、诊断与测试入口。面向用户的安装章节优先写可直接使用的一键命令；GUI 步骤独立成段，不把 CLI 命令和 TOML 配置混成 GUI 操作。

用户集成文档与本规范放在 `docs/{en,zh}/agent-integrations/`，英文和中文对应页同步维护。如果该宿主还存在 `docs/images/agents/{en,zh}/` 的镜像说明，也要同步核对；不需要为不存在的镜像机械创建多份相同文本。共享配置的完整解释链接到共享 README，宿主页只说明差异和常用示例。

设计文档只保留读代码无法直接回答的约束、选择理由和恢复不变量。支持矩阵、真实命令和入口变化后及时更新，删除互相矛盾的旧说明。不要把临时排查记录、一次性验证报告或过期测试数字当作长期设计文档。

## 13. 合入前检查单

- [ ] 宿主版本、输入输出、时间限制、消息来源和结束语义均有证据；不支持的能力已注明。
- [ ] 选择了合理的接入形态，新增代码主要描述宿主差异，共享层不反向依赖宿主。
- [ ] 配置只有一处声明，开关在执行链生效，hook 与 MCP 的实际连接和身份经过验证。
- [ ] session 与 peer 不混用；多窗口、resume、子代理和 cwd 变化不会误用状态。
- [ ] capture 保留文本与工具证据；部分失败、离线入队、重复事件和尾部补齐不会错误推进游标。
- [ ] commit 顺序与恢复责任明确；退出后 worker 失败也有可验证的补偿办法。
- [ ] Hook/MCP 输出合法，URI guard、Skill 和 doctor 只承诺真实能力。
- [ ] 生成目标、安装、卸载、归档、类型声明和发布触发条件完整；最终产物已验证。
- [ ] 版本与相关 manifest 一致；运行了与改动相称的检查，并记录未验证的平台或场景。
- [ ] 文档和迁移说明与最终行为一致，没有新增重复配置、旧路径引用或无人使用的辅助脚本。

## 14. 维护者阅读入口

按问题选择入口，避免从各插件生成副本开始追踪：

- 已有集成的支持范围：[集成能力参考](./16-capability-reference.md)。
- 配置、peer、生成策略：[Memory Plugin Shared README](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md)。
- Claude Code 的事件接线：[hooks.json](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/hooks/hooks.json)；召回适配：[auto-recall.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/scripts/auto-recall.mjs)。
- Codex 的提交、异常退出与恢复：[DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md)；实现：[session-end.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/scripts/session-end.mjs)。
- 新增配置文件式宿主：[agent-hook-plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README.md)；严格协议实例：[ZCode DESIGN](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/DESIGN.md)。
- CI 和交付检查：[pr.yml](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml)、[sync.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs)、[marketplace archive checker](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-marketplace-archive.mjs)。
