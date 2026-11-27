# 开发与维护 Agent 插件

本文规定如何新增和维护通过生命周期 hook 自动读写记忆、通过 MCP 提供工具的 OpenViking Agent 插件，涵盖宿主契约、共享 flow、配置与凭据、MCP、测试、生成和发布。宿主是指承载 Agent 的客户端或运行时，代码中也称 harness。

所有 hook 宿主运行同一套 flow。新宿主只描述自己的事件、payload、transcript 格式和输出信封，不决定发送什么、会话何时 commit、模型看到什么。MCP-only 或原生工具集成可采用相关规则，不必补齐不适用的 hook 能力。

## 使用 VibeCoding 开发插件

使用 VibeCoding 或其他 AI 辅助编程方式新增、修复、重构 Agent 插件时，**必须让 coding agent 在修改前完整阅读并遵循本文**。将文档路径和具体任务一起交给 Agent，要求它先核实宿主契约，再实施，并按验收检查单报告结果。生成了代码或测试通过，都不能替代对协议、恢复和安装产物的验证。

下面的提示词可以直接交给 coding agent；把最后一行替换为具体任务：

```text
Before changing any OpenViking agent plugin, read and follow
docs/en/agent-integrations/18-plugin-development.md
(Chinese: docs/zh/agent-integrations/18-plugin-development.md).

Inspect examples/agent-hook-plugin/lib/flow/host.d.mts (the host contract),
lib/flow/index.mjs, and two existing hosts under
examples/agent-hook-plugin/hosts/. Verify the target host's events, payloads,
output schema, time limits, and installation contract.

A host module describes the host and decides nothing: no network, no queue,
no state files. Do not edit generated shared/ copies. Cover configuration,
hook/MCP identity, fixtures, installation, versioning, and bilingual
documentation as applicable.

Before finishing, use the guide's acceptance checklist and report what was
changed, what was verified, and any remaining limitations.

Task: <describe the plugin addition, fix, or maintenance change>
```

## 1. 设计原则

1. **同一行为有一个权威实现**。每个阶段做什么，只在 `lib/flow/` 中决定。宿主提供事实，不提供规则。
2. **统一必须发生在执行链上**。配置只有由同一段代码解析和消费，才能保持一致。复制文件、约定大家保持一致，都不足以防止分叉。
3. **能推导的清单不手写**。配置键来自 schema；运行时文件来自 import 闭包；能力参考页里的宿主表来自宿主模块。
4. **真实差异以数据表达**。没有会话结束事件、输出 schema 严格、退出预算很短，这些通过 `events`、`facts`、`budgets` 和 `emit` 表达，而不是在共享代码里按宿主名分支。
5. **交付方式决定生成时机**。宿主直接加载仓库目录时，目录必须完整；打包分发的插件在打包时生成副本。
6. **每次抽象都减少维护点**。扩充宿主契约前，先说明哪项宿主事实现在无法表达。不为假想中的未来 Agent 做设计。

这些原则与仓库[贡献指南的职责与设计要求](https://github.com/volcengine/OpenViking/blob/main/docs/repository/CONTRIBUTING_CN.md#职责与设计)一致。

## 2. 接入前先确定宿主契约

开发前，在宿主的 README 或设计说明中记录以下事实，并注明来源：宿主文档、带版本的源码或实测行为。不要用另一个 Agent 的经验补空白。

| 方面 | 必须确认的内容 |
| --- | --- |
| 版本与平台 | 宿主和 Node.js 最低版本、已验证的操作系统、旧版本上的行为 |
| 安装方式 | 原生插件、marketplace、配置文件 hook 或 npm 扩展；宿主实际加载的目录 |
| 事件 | 启动、提交 prompt、轮次结束、压缩前、会话结束、子代理、工具调用事件是否存在 |
| 输入 | stdin 是否为 JSON；会话、cwd、transcript、轮次字段，以及它们何时缺失 |
| 输出 | 上下文注入、放行/拒绝字段、空响应、退出码，未知键是否让整份输出失效 |
| 时间限制 | 单位、上限、宿主是否会截断，超时杀掉单个进程还是进程组 |
| 消息来源 | transcript/rollout 格式、落盘时机、稳定的消息 ID、工具调用与结果的关联 |
| MCP | 配置格式、stdio 支持、根目录变量、启动 cwd、继承的环境变量、工具命名空间 |
| 恢复 | resume、clear、异常退出、压缩、transcript 截断时保留的身份与状态 |

把每项能力标记为已验证、带降级支持或不支持，并写明验证版本。不要把 `Stop` 当作会话结束，也不要注册宿主从不发出的事件。没有可用会话结束事件的宿主，会话最后一段不会被 commit，除非服务端的空闲 commit 兜底；要如实写明（见[各集成何时提交](./16-capability-reference.md#各集成何时提交)）。

### 2.1 选择最小的接入形态

| 宿主约束 | 形态 | 参考 |
| --- | --- | --- |
| hook 以配置文件中的命令运行 | `examples/agent-hook-plugin/hosts/` 下的一个目录 | [agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) |
| 宿主 SDK 回调在宿主进程内运行 | 带自己 `host.mjs`、调用 `runStage` 的扩展包 | [OpenCode](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)、[DSH](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)、[pi](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension) |
| 宿主能注册原生工具但不支持 MCP | 用官方 MCP client，把服务端 `tools/list` 以 `openviking_` 前缀注册为宿主原生工具 | [pi](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension) |
| 有 MCP，但没有自动注入或完整会话记录 | 写明限制的 MCP-only 集成 | [Agent Plugins](https://github.com/volcengine/OpenViking/tree/main/agent-plugins) |

## 3. 插件布局与 flow

### 3.1 代码在哪里

`examples/agent-hook-plugin/` 是所有 hook 脚本宿主共用的唯一插件目录，Claude Code 和 Codex 也在其中。

```text
examples/agent-hook-plugin/
  .claude-plugin/plugin.json   Claude Code manifest；hooks/hooks.json、.mcp.json、mods/、commands/、
                               scripts/statusline.mjs 是它的默认位置
  .codex-plugin/plugin.json    Codex manifest，指向 hosts/codex/hooks.json 和 hosts/codex/.mcp.json
  lib/                         共享库（配置、凭据、HTTP、召回、队列、MCP 核心等）
  lib/flow/                    flow：宿主契约、runStage、每个阶段一个模块
  scripts/hook.mjs             唯一的 hook 入口：hook.mjs <event> <host>
  scripts/uri-guard.mjs        URI guard 入口：uri-guard.mjs <host>
  servers/mcp-proxy.mjs        唯一的 MCP proxy
  skills/                      唯一一份 skill
  hosts/index.mjs              loadHost(id)：按需导入 hosts/<id>/host.mjs
  hosts/<host>/                host.mjs、decode.mjs、注册文件、fixtures/、宿主独有的部分

examples/memory-plugin-shared/ 安装器（install.sh）、sync.mjs、发布工具及其测试
```

宿主目录：`claude-code`、`codex`、`cursor`、`trae`（同时服务 `trae-cn`）、`antigravity`、`zcode`、`kimicode`、`codebuddy`、`qoder`。Claude Code 目录还包含 statusline 状态、压缩器、doctor、seal 和 skill-experience 代码；Codex 目录包含压缩器、usage 和 doctor 代码。

`hook.mjs` 加载宿主、解析配置、判断写事件能否脱离进程（`mayDetach`）、读取 stdin、调用 `runStage(host, event, payload)`，再输出 `host.emit(...)` 的结果。没有按宿主划分的 dispatcher。

### 3.2 宿主契约

[`lib/flow/host.d.mts`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/flow/host.d.mts) 是契约。宿主模块导出一个对象，包含：

| 字段 | 含义 |
| --- | --- |
| `id`、`sessionPrefix` | 客户端 id；宿主会话写入的 OpenViking 会话 id 前缀 |
| `events` | hook 入口传入的宿主事件名到阶段的映射：`start`、`prompt`、`turnEnd`、`compactSoon`、`end`、`compacted`、`toolCall`、`toolResult` |
| `budgets` | 同一事件所有请求共享的毫秒数 |
| `canDetach`、`syncEvents`、`detachEvents` | 写事件能否先应答、在脱离的进程里干活；哪些必须留在 hook 内完成；哪些因宿主给的时间太短而总是脱离 |
| `facts` | `profileAt`（`"start"` 或 `"firstPrompt"`）、`compactionDropsProfile`、`compactionDropsTranscript`、`historySession`、`historyTool`、`transcript`（`false` 或 `"optional"`）、`replayLimit` |
| `config` | 日志文件名不是 `<id>-hooks.log` 时的 `logFile`；`rootKeyFallback` |
| `toolHints` | URI guard 给出的替代工具和示例调用 |
| `read(event, payload, io)` | 一个事件的 payload 说了什么：会话、cwd、prompt、transcript 引用、`resumed`、`defer`、`child`、`skip`、`note`。纯函数；`io.text` 是查看文件的唯一途径 |
| `locate(ref)` | transcript 的路径；同步 |
| `decode(input)` | 尚未确认的轮次和 `cursorAfter(count)`。纯函数 |
| `emit(event, output, payload)` | 针对 `{ context }`、`{ deny }`、`{ notice }`、`{ input }` 要输出的值，或不输出 |
| `tool`、`mcpSession`、`legacyCursor`、`present`、`compress` | 工具调用 payload 的形状；MCP 调用 `_meta` 中的会话 id；旧版本的 cursor；宿主特有的呈现；本地召回压缩器 |
| `observe(ctx, outcome)` | 每个事件之后被告知做了什么，用于宿主为自身界面保存的文件。不能改变应答；失败只记日志 |

[`lib/flow/index.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/flow/index.mjs) 导出 `runStage(host, event, payload, { detached, cfg, outcome })`。它运行一个事件并返回输出，由调用方交给 `host.emit`。遇到 `read.skip`、`disabled`、`unconfigured`、`bypass`、`subagent`（开启 `skipSubagentSessions` 时的子会话）时跳过并记录原因。各阶段是 `start.mjs`、`prompt.mjs`、`capture.mjs`（轮次结束、压缩、会话结束）、`commit.mjs`、`pointer.mjs` 和 `tool.mjs`；行为见[能力参考](./16-capability-reference.md#捕获与提交)。

### 3.3 宿主能做什么、不能做什么

宿主模块不发网络请求、不碰离线队列、不写状态文件：这些都归 flow。`read` 和 `decode` 是输入的纯函数，因此可以用 fixture 测试。cursor、锁、`~/.openviking/pending` 下的离线队列、commit 以及会话的自动 commit 策略都归 flow。

宿主可以通过 `observe` 保存自身界面用的文件（statusline 快照、会话绑定），可以通过 `compress` 运行本地压缩器。两者都不能改变 flow 发送或应答的内容。

共享代码不得 import 宿主目录，也不得按宿主 id 分支。宿主需要契约表达不了的东西时，在 `host.d.mts` 中加一项事实，并在 flow 中只实现一次它的效果。

### 3.4 进程内宿主

OpenCode、DSH 和 pi 运行在各自 harness 的进程内。它们各有自己的 `host.mjs`，调用库中 `flow/index.mjs` 的 `runStage(host, event, payload, { cfg, outcome })`；这份库是生成的 `shared/` 副本（OpenCode 为 `lib/shared/`）。它们把自己解析好的配置作为 `cfg` 传入，需要知道事件做了什么时再传一个 `outcome` 对象。宿主模块同样什么都不决定；扩展代码只负责把 SDK 回调变成事件和 payload，并把返回的上下文放到 harness 期望的位置。

## 4. 配置、凭据和身份

### 4.1 一次声明，统一解析

新增行为配置在 `config-schema.mjs` 中声明：规范名、类型、默认值、范围、所属能力，以及适用的环境变量、旧别名和 workspace 键。通过 `buildPluginConfig(harness, options)` 解析；hook 进程通过 `loadAgentHookConfig(host.id, cwd, host.config)` 获得配置。不要新增重复共享默认值的插件私有 `config.json`，也不要另维护 doctor/workspace 键表。

行为配置按以下顺序解析，优先级从高到低：

1. `OPENVIKING_*` 环境变量。
2. 本机 workspace registry。
3. Workspace `.openviking/config.local.json`。
4. Workspace `.openviking/config.json`。
5. `ovcli.conf` 的 `plugin.<harness>`。
6. `ovcli.conf` 的 `plugin`。
7. `ov.conf` 中旧的宿主段。
8. Schema 默认值。

下面的 `ovcli.conf` 片段提供共享召回默认值，并为 Codex 关闭自动捕获；更高优先级的环境变量或 workspace 配置仍可覆盖它：

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

在 `HARNESS_KEYS` 中注册规范宿主 ID，并确认连字符/下划线别名符合现有约定。同一层内规范名优先于别名。

区分未配置、显式 `false` 和解析结果恰好等于默认值。标记为 `sendOnlyWhenConfigured` 的字段只在真正配置时发送，避免客户端默认值覆盖服务端默认值。允许 `false`、`0` 或空字符串的字段不要写成 `value || default`。

开关必须控制真实行为。共享的 `isRecallEnabled()` 和 `isCaptureEnabled()` 是最后一道检查。`mcpEnabled` 这类配置，只在宿主确实消费时才对该宿主公开。

### 4.2 凭据不是普通 workspace 配置

连接和身份通过 `credentials.mjs` 的 `resolveConnection()` 解析，`buildPluginConfig()` 会调用它。`OPENVIKING_CREDENTIAL_SOURCE` 的 `auto`、`cli`、`env` 模式选择凭据来源，并不简单沿用上面的行为配置优先级。MCP proxy 导出 `readProxyConfig(env)`，与它的 hook 走同一个 loader 解析，再用 `toMcpProxyConfig()` 映射结果；它不手工挑字段，也不直接调用 `credentials.mjs`。如果宿主只给 MCP server 传白名单环境变量，白名单必须覆盖 `MCP_PROXY_ENV_VARS`；如果给的是封闭环境，用 `forwardConnectionEnv()` 转发解析好的连接。每个新 proxy 都要加入 `mcp-hook-parity.test.mjs`，没有对应行时该测试会失败。

Workspace 文件不得包含 URL、API key、用户凭据等禁止的连接或凭据字段，也不得插值环境变量。这条规则由 [`workspace-config.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/workspace-config.mjs) 负责，不要为每个宿主另加白名单。安装器不得把解析出的 API key 写进 `.mcp.json`，也不得替换用户选择的云端连接。

请求头用 `buildOvHeaders()` 构造。API key 以 `Authorization: Bearer` 发送，不重复发送 `X-API-Key`；只有解析后的 `sendIdentityHeaders` 为 true 时才发送 account/user 头。保留 `User-Agent` 和错误中的 `traceId`，用于识别运行版本和追踪请求。statusline 和诊断探测不得自行实现鉴权。

诊断中，`credentialSource` 表示解析模式，`apiKeySource` 表示 key 的实际来源。`rootKeyFallback`（在宿主的 `config` 中设置）是为旧安装保留的；新宿主不能因为参考宿主开启了它就照搬。

### 4.3 会话身份与 peer 分开处理

原生会话 ID 标识一段对话；peer 标识项目记忆归属。flow 由 `sessionPrefix` 加宿主会话 id（或宿主自行推导时的 `read.session`）得到 OpenViking 会话，并为子会话（`read.child`）单独分配状态、锁和 OpenViking 会话。前缀是数据兼容契约；修改时说明旧状态如何继续可读，并用 `legacyCursor` 让升级后不重复发送旧版本已捕获的内容。

使用共享 peer 解析。默认从 Git 推导身份；普通非 Git 目录没有自动 peer，可以用标记文件指定。flow 按 payload 的实际 cwd 重新加载 workspace 配置，而不是插件安装目录。

常驻 MCP proxy 无法从启动 cwd 推断当前项目。使用 `resolveMcpActorPeerId()`：宽范围读取不带 actor peer 头；按 actor 限定的读取需要显式 peer。peer 范围用于在已鉴权用户内组织记忆和检索，不要把它说成不同用户之间的授权隔离。

## 5. 新增一个宿主

1. **创建 `examples/agent-hook-plugin/hosts/<id>/`**。`hosts/index.mjs` 按需加载 `./<id>/host.mjs`；由其他目录服务的客户端 id（如 `trae` 服务 `trae-cn`）写进它的 `DIRECTORY` 映射，模块导出 `hosts: { <id>: ... }`。
2. **编写 `host.mjs`**。把宿主事件名映射到阶段，按宿主已验证的超时设置 `budgets`，按已验证的进程行为设置 `canDetach` / `syncEvents` / `detachEvents`，并声明 `facts`。`read` 从 payload 中提取字段；`emit` 只构造宿主接受的信封（ZCode 这类严格 schema 绝不能出现未知键）。
3. **编写 `decode.mjs`**。把 transcript（`transcript: false` 时则是 payload 和 `notes`）转换成尚未确认的轮次和 `cursorAfter`。保留带 id 的工具调用和结果；合适时使用 `lib/flow/turns.mjs` 的 `parseJsonl` 和 `resumeTurns`。
4. **添加注册文件**。宿主的 hook 配置对 `events` 中每个事件调用 `node <plugin root>/scripts/hook.mjs <event> <client id>`，宿主有工具调用前事件时再加 `scripts/uri-guard.mjs <client id>`；加上宿主的 manifest 或 integration 文件，以及指向 `servers/mcp-proxy.mjs` 的 MCP 配置。
5. **添加 `fixtures/`**。把真实 payload 和 transcript 记录为用例（见第 9 节）。
6. **完成登记**。把宿主加入安装器；宿主从自己目录打包 skill 时加入 `sync.mjs` 的 `SKILL_TARGETS`；加入 `host-table.mjs` 的 `HOOK_HOSTS`；更新文档。

不要为宿主没有的事件注册空处理器。

## 6. MCP 与模型可见工具

hook 自动完成生命周期行为；MCP 暴露由模型决定调用的工具。两者使用同一份配置和身份。文档要写清哪些内容自动注入、哪些操作需要模型调用、关闭自动行为后哪些 MCP 工具仍可用。

支持 stdio MCP 的宿主使用唯一的 proxy `servers/mcp-proxy.mjs`：它解析配置，用 `buildMcpProxyConfig()` 整形，再交给 `createOpenVikingMcpProxy()`。MCP 调用的 `_meta` 中带有宿主会话的宿主提供 `mcpSession`，让 `session_history` 默认指向当前会话。以服务端 `tools/list` 和 `tools/call` 为准，不另维护一份工具目录。必要的本地工具使用共享的 `localToolProvider` 接口，不得遮蔽服务端工具。

每个新宿主都要验证：

- 从宿主实际工作目录启动，包括相对路径、根目录变量和环境变量白名单。
- `initialize` 协商、无普通响应的通知、JSON/SSE 处理、并发响应 ID，以及干净的 stdout 协议输出。
- 在环境变量配置、默认/自定义文件和 profile 切换下，hook 和 MCP 的 URL 与身份符合预期。观察实际请求目标和请求头，而不只是看配置内容。
- 诊断和文档中的工具名与宿主实际暴露的一致。

## 7. URI guard、Skill 和诊断

`viking://` 是虚拟 URI。路径参数是 `viking://` URI 的本地文件工具不可能成功，所以宿主有工具调用前事件时，由 `scripts/uri-guard.mjs <host>` 拒绝。带 `viking://` URI 的 shell 命令可能只是把它当数据使用，因此放行，并通过宿主的模型可见通道附加提示。规则在 `lib/uri-guard.mjs` 和 `lib/flow/tool.mjs` 中；宿主只提供 `tool`、`toolHints` 和 `emit` 中的信封。没有所需事件时，写明限制，并通过 skill 引导模型。

共享 skill 源在 [`examples/skills/`](https://github.com/volcengine/OpenViking/tree/main/examples/skills/)，通过 `sync.mjs` 的 `SKILL_TARGETS` 分发。不要在多个副本中分别修改同一段指引。skill 只能描述实际可调用的工具。生成的 skill 文件不得在 YAML frontmatter 之前加横幅。

存放在 OpenViking 中的 skill 通过服务端的 `add_skill` MCP 工具创建、安装、共享和替换。不要在宿主中重新实现安装。URI guard（`isSkillUri()`）会把被拒绝的 skill URI 本地写入或编辑引向 `add_skill`。

诊断使用 `runDoctor(hostSpec)`。宿主提供安装路径、manifest、hook 注册和状态检查；`doctor-core.mjs` 负责通用的配置、凭据、网络和输出处理。日志保留宿主、阶段、会话关联、延迟和 trace ID，默认不得记录 API key、完整 Authorization 头或整段用户 transcript。

## 8. 生成与安装

### 8.1 `sync.mjs` 如何复制共享库

共享库在 `examples/agent-hook-plugin/lib/`。hook 宿主原地 import。其他位置的插件由 [`sync.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs) 生成副本：

- `TARGETS` 中每一项只写插件自身代码的位置、副本输出位置，以及副本是否 `committed`。文件集合是该代码 import 的传递闭包（静态 import、字面量动态 import，以及通过 `new URL("./x.json", import.meta.url)` 读取的 JSON 文件）；插件的 TypeScript import 这些副本时，还会带上 `.d.mts` 声明。
- 副本带有文件头 `// GENERATED FROM examples/agent-hook-plugin/lib. DO NOT EDIT.`；JSON 数据文件逐字节复制。
- `committed: true` 用于宿主直接指向本仓库目录安装的插件（Agent Plugins、OpenClaw），由 bot 在 main 上重新生成。`committed: false` 用于构建成 npm 包或 tarball 的插件（OpenCode、DSH、pi），在打包时生成副本。
- `DATA_TARGETS` 把 lib 数据文件复制到插件之外（gateway 的 `server-features.json`）；`SKILL_TARGETS` 原样复制 skill：`agent-hook-plugin/skills/`（每个 hook 宿主都从这一份安装）、OpenClaw 和 Agent Plugins 的副本提交到仓库，OpenCode、DSH 和 pi 的副本在打包时生成。
- 脚本会报告不再被 import 的副本（`stale`）和没有任何 target import 的 lib 模块（`unused`）。

修改共享库后运行：

```bash
node examples/memory-plugin-shared/sync.mjs
```

保持 import 可被发现，不要藏在拼接字符串后面。不要依赖开发机绝对路径、软链接或 `NODE_PATH`。PR 必须包含最新的已提交副本。

### 8.2 安装与卸载的行为要求

复用 [`install.sh`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/install.sh)。JSON/JSONC 合并逻辑放在 [`lib/install/`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared/lib/install/)。安装器应当：

1. 在修改用户配置前解析、校验并准备好所需文件。无效 JSON/JSONC 必须报错并保留原文件。
2. 幂等，不产生重复的 hook/MCP 条目。保留其他插件、用户设置和有意义的注释。
3. 处理带空格的路径、不展开变量的宿主和自定义配置路径。
4. 只移除本插件拥有的条目，包括 URI guard。保留凭据、记忆和会话数据。
5. 移除一个客户端后，共享运行时文件对其他客户端仍可用。

用 [`stage-memory-plugin-marketplace.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/stage-memory-plugin-marketplace.sh) 和 [`check-marketplace-archive.mjs`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-marketplace-archive.mjs) 校验归档。发布前解开最终归档或 tarball，冒烟测试入口和安装。

## 9. 测试

flow 只测一次，在 [`examples/memory-plugin-shared/flow.test.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/flow.test.mjs) 中针对一个最小宿主和 mock 服务端。所有宿主共有的行为（会话开始、召回、捕获、cursor、commit、队列、pointer、工具调用）放在这里，不放在单个宿主的测试里。

[`hosts.test.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/hosts.test.mjs) 检查 `agent-hook-plugin/hosts/` 下每个含 `host.mjs` 的目录：模块是否满足契约，以及 `fixtures/` 中的用例。一个用例是一个 JSON 文件，含 `what` 和以下之一：

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| `decode` | `{ transcript?, cursor?, notes?, payload?, cfg? }` | `messages`：flow 发送的会话消息；可选 `cursor`，即全部确认后的 cursor |
| `read` | `{ event, payload, transcript? }` | `expect`：列出的字段 |
| `tool` | `{ payload }` | `expect`：宿主输出的内容 |
| `emit` | `{ event, output, payload? }` | `expect`：宿主输出的内容 |
| `mcp` | `{ meta }` | `expect`：MCP 调用 `_meta` 指向的 OpenViking 会话 |

`transcript` 指用例旁边的文件。`UPDATE_FIXTURES=1` 按宿主当前行为重写每个用例的期望；提交前先看 diff。`OV_TEST_HOST=<directory>` 只运行一个宿主的用例。

```bash
OV_TEST_HOST=zcode node --test examples/memory-plugin-shared/hosts.test.mjs
```

[16-capability-reference.md](./16-capability-reference.md) 中的宿主表（位于 `<!-- host-table:start -->` 与 `<!-- host-table:end -->` 之间）由 [`host-table.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/host-table.mjs) 从宿主模块生成，任一语言页面与之不一致时 `host-table.test.mjs` 失败。修改宿主的事件或 facts 后，把 `node examples/memory-plugin-shared/host-table.mjs --lang <lang>` 的输出粘贴到标记之间。进程内宿主 import 的是生成的 `shared/` 副本，所以新 checkout 上先运行 `sync.mjs`。

共享库测试（`plugin-config`、`credentials`、`mcp-proxy-*`、`pending-queue`、`recall-core`、`uri-guard` 等）也在 `examples/memory-plugin-shared/` 中。公共 helper 放在 [`testing/support.mjs`](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/testing/support.mjs)。使用私有临时目录、隔离的配置和 pending 路径以及动态端口。安装器和 marketplace 测试会生成文件，要像 CI 一样串行运行：

```bash
node --test --test-concurrency=1 \
  examples/memory-plugin-shared/install-agent-hooks.test.mjs \
  examples/memory-plugin-shared/release-marketplace.test.mjs
```

完整命令以 [PR CI](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml) 为准。Node.js 测试通过不代表 Windows 上的脱离进程、路径引号或进程清理可用；未测试的平台要写明。验收证据应写明产物、宿主版本、场景和观察结果。

## 10. 维护、发布和文档同步

### 10.1 按改动类型完成维护

| 改动 | 必需的后续工作 |
| --- | --- |
| flow 或共享库修复 | 修改 `lib/`，扩充 `flow.test.mjs` 或库测试，运行 `sync.mjs`，升级版本 |
| 宿主 payload 或事件变化 | 更新 `host.mjs` / `decode.mjs`，用真实 payload 添加 fixture，事件或 facts 变化时重新生成宿主表 |
| 新增配置 | schema、真实消费者、已配置语义、诊断来源、用户文档 |
| 目录或包改名 | import、manifest、安装器、marketplace、CI 路径过滤、skill、链接、卸载识别 |
| 仅文档 | 准确性和链接可用 |

某个宿主暴露出的共享缺陷仍归 flow；只属于某个宿主的 payload 变化归该宿主目录。

### 10.2 版本与发布链

新插件必须加入版本检查和发布流程，而不只是新增目录。[`check-plugin-version-bumps.sh`](https://github.com/volcengine/OpenViking/blob/main/.github/scripts/check-plugin-version-bumps.sh) 会为它登记的每个插件统计共享 `lib/` 的变化；新增或修改 target 时都要检查登记。宿主 manifest、`package.json`、lockfile、安装 manifest 和安装器版本检查要保持一致。`User-Agent` 和 doctor 输出应报告实际产物版本。

npm/归档构建在打包前从干净源码生成共享文件。[`plugin-npm-release.yml`](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/plugin-npm-release.yml) 矩阵覆盖 DSH 和 OpenCode；其他包的发布路径要逐个确认。

```bash
bash .github/scripts/check-plugin-version-bumps.sh origin/main
git diff --check
git status --short --untracked-files=normal -- examples agent-plugins
```

版本检查比较已提交的 `base...HEAD`；对最终 commit 运行。

### 10.3 文档是接口的一部分

插件 README 应包含支持的能力和版本、最快的安装方式、凭据来源、自动行为与 MCP 的区别、核心开关、限制、升级/卸载和诊断。共享行为只在[能力参考](./16-capability-reference.md)中写一次；宿主页面写差异。用户指南和本规范位于 `docs/{en,zh}/agent-integrations/`，中英文页面一起维护。

## 11. 合入前检查单

- [ ] 宿主版本、输入/输出格式、时间限制、消息来源和结束语义都有依据；不支持的能力已写明。
- [ ] 宿主模块只描述宿主：不发网络请求、不碰队列、不写状态文件；`read` 和 `decode` 是纯函数。
- [ ] `events` 中每个事件都以 `scripts/hook.mjs <event> <host>` 注册，没有注册宿主从不发出的事件。
- [ ] fixture 用真实 payload 覆盖 `read`、`decode`、`emit` 和工具调用；该宿主的 `hosts.test.mjs` 通过。
- [ ] 配置只声明一次，开关影响执行，实际的 hook/MCP 连接和身份已验证。
- [ ] 能力参考中的宿主表在两种语言下都与 `host-table.mjs` 一致。
- [ ] `sync.mjs` 输出、安装、卸载、归档和发布触发都完整；已测试最终产物。
- [ ] 版本和相关 manifest 一致；未测试的平台和场景已写明。
- [ ] 中英文文档描述最终行为。

## 12. 维护者阅读入口

- 各集成共有的行为：[能力参考](./16-capability-reference.md)。
- 宿主契约与 flow：[host.d.mts](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/lib/flow/host.d.mts)、[flow/](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/lib/flow)。
- hook 宿主：[hosts/](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts)、[agent-hook-plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README_CN.md)。
- 生成与测试：[sync.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/sync.mjs)、[hosts.test.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/hosts.test.mjs)、[flow.test.mjs](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/flow.test.mjs)、[pr.yml](https://github.com/volcengine/OpenViking/blob/main/.github/workflows/pr.yml)。
