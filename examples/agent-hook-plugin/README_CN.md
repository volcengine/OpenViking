# OpenViking 记忆：轻量 Hook 宿主

Cursor、TRAE、TRAE CN 与 ZCode 使用宿主配置文件；Kimi Code 使用原生托管插件目录，CodeBuddy Code 与 Qoder CLI 使用本地插件 marketplace，Antigravity 使用 `~/.gemini/config/plugins/` 下的插件目录。它们共用同一个 dispatcher 和记忆运行时，安装脚本在安装时组装依赖，不在仓库里为每个宿主提交共享代码副本。

```bash
curl -fsSL https://openviking.ai/install | bash
```

安装脚本会询问要安装哪些宿主以及 OpenViking 连接信息。要从仓库 checkout 安装，在仓库根目录运行 `bash examples/memory-plugin-shared/install.sh --harness <cursor|trae|trae-cn|zcode|kimicode|codebuddy|qoder|antigravity>`。

> **需要支持 `viking://~` home alias 的 OpenViking 服务端。** 召回通过 `viking://~/memories` 与 `viking://~/skills` 指向调用者自己的上下文空间；不带 uid 的 `viking://user/memories` 简写会被较新的服务端拒绝。

## Hook 做什么

- **会话开始** — 注入用户画像与偏好，外加 `<available-skills>` 清单，列出用户自己的和账号内共享的 OpenViking skill；同时重放离线会话排队的写入。Kimi 启动时只重放一个有界小批次，并在第一次成功的 prompt hook 注入画像。
- **提交 prompt** — 搜索与 prompt 相关的记忆和 skill 并注入，按事件 id 与 500ms 窗口去重。
- **工具调用前** — 拦截本地文件工具对 `viking://` 虚拟路径的访问，引导回 OpenViking MCP 工具。在 TRAE 和 Qoder CLI 上，带 `viking://` URI 的 shell 命令照常执行，并附加一条指向同一组工具的提示。
- **Stop** — 捕获完成的回合并按宿主策略提交 OpenViking 会话：TRAE、ZCode 和 Antigravity 每次捕获到内容的 Stop 都提交，Cursor 捕获满 `commitTurnThreshold` 条消息时提交，Kimi Code、CodeBuddy Code 和 Qoder CLI 在会话待提交 token 达到 `commitTokenThreshold`（100,000）时提交，并在会话结束和压缩前提交所有尚未提交的内容。Qoder、CodeBuddy、Kimi Code 和 Antigravity 把工具调用及其结果作为 tool part 放进该回合的 assistant 消息；Cursor 上报 OpenViking MCP 结果；TRAE 和 ZCode 只发送文本。Kimi 还使用 PreCompact、SessionEnd 与同步 Interrupt 信号。
- **压缩之后** — 追加一段简短的 `<openviking-context source="session-history">` 说明，给出会话的 OpenViking id，以及如何用服务端的 `session_history` MCP 工具查回确切细节。Qoder 在压缩或恢复后的 SessionStart 中返回，TRAE 在 PostCompact 中返回；Cursor、CodeBuddy 和 Kimi Code 在 PreCompact 记下压缩（CodeBuddy 也在压缩后的 SessionStart 记下，CodeBuddy 和 Kimi Code 也记下恢复），在下一次 prompt 时注入。恢复只给还没收到过说明的会话注入，因为恢复后的 transcript 里仍有之前那段说明。ZCode 没有压缩信号，Antigravity 没有 OpenViking MCP 服务，两者都不注入。模型没传 `session` 时，Cursor 的 `preToolUse` 与 Qoder 的 `PreToolUse` hook 会改写调用补上它，CodeBuddy 和 TRAE 由 MCP 代理从调用的 `_meta` 补上；Kimi Code 和 TRAE 的说明会要求模型自己传。设置 `OPENVIKING_SESSION_HISTORY_POINTER=0` 或 `sessionHistoryPointer: false` 关闭这段说明。

## 目录结构

`scripts/hook.mjs` 是所有 hook 命令的唯一入口，持有五家共用的状态机——防抖、prompt 去重、召回缓存、跨进程锁——差异部分向 `hosts/` 下的适配器索取：事件词汇、响应信封、如何从 payload 读出 prompt、如何采集完成的回合。`scripts/uri-guard.mjs` 与 `servers/mcp-proxy.mjs` 同样各只有一份，按安装器传入的 client id 选择宿主。

根目录的 `plugin.json` 是宿主无关的包元数据，只用于版本检查和诊断。Kimi 的原生 manifest 位于 `hosts/kimicode/`，安装组装时复制到插件根目录。

`hosts/<host>/` 只放宿主配置或原生 manifest；可执行适配器放在上一层。`../../memory-plugin-shared/lib` 这条相对路径在源码树、配置型安装和 Kimi 组装后的原生插件里都成立。

`examples/memory-plugin-shared/sync.mjs` 把 `examples/skills` 下的共享 skill（包括 `ov-memory-doctor`）复制到各个 `hosts/<host>/skills`。

记忆逻辑本身不在这里：召回、批量写入、待处理队列、凭据解析与 MCP 代理都来自 `examples/memory-plugin-shared/lib`，由安装脚本复制到 `~/.openviking/agent-integrations/memory-plugin-shared/lib`。

## 各宿主差异

- **Cursor** — 八个事件。`afterMCPExecution` 将 OpenViking 工具结果追加到本地 JSONL 日志，Stop 时与文本 transcript 一起上报；文本去重与工具上传位置分别管理，成功后推进捕获位置，失败由原记录重试。`capturedSinceCommit` 达到阈值才 commit，压缩前在消息完整上报后 commit。会话前缀 `cu-`。见 [Cursor 接入文档](../../docs/zh/agent-integrations/12-cursor.md)。
- **TRAE / TRAE CN** — 采集直接读 Stop 事件的 `prompt`、`text_content`、`last_assistant_message`，不解析 transcript；每次带内容的 Stop 都 commit。安装脚本把五个 skill（`openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search`、`ov-memory-doctor`）复制到 `~/.trae/skills` 或 `~/.trae-cn/skills`，用户改过的 skill 或同名的非托管目录在升级和卸载时保留；TraeCode CLI 也读取 `~/.trae/skills`，同时装了它的插件时，CLI 会把每个 skill 列两次。会话前缀 `tr-` 与 `trcn-`。见 [TRAE 接入文档](../../docs/zh/agent-integrations/13-trae.md)。
- **ZCode** — rollout 文件是权威增量对话源：稳定的 host `turnId` 用于去重，也让后续 Stop 能补回漏掉的回合，hook stdin 只是兜底。ZCode 不支持 `PreCompact` 与 `SessionEnd`，因此每次 Stop 都 commit 来补足这两个信号。它的输出 schema 是严格的，所以放行时不写任何内容。五个 skill 安装到 `~/.zcode/skills`，与 TRAE 一样记录归属。会话前缀 `zc-`。已验证的扩展面记录在 [DESIGN.md](./DESIGN.md)。
- **Kimi Code** — `wire.jsonl` 是权威 transcript；UserPromptSubmit 输出原始上下文文本。Stop、PreCompact、SessionEnd 可后台写入，Interrupt 保持同步并共享 2 秒的 OpenViking 请求预算。安装脚本生成自包含原生插件，不修改旧的 `config.toml` 或 `mcp.json`，并在插件的 `skills/` 下附带五个 skill，由 `kimi.plugin.json` 声明。会话前缀 `kc-`。
- **Antigravity** — 桌面 app 与 `agy` CLI 读取 `~/.gemini/config/plugins/` 下的同一个插件。Antigravity 在每次模型调用前都运行 PreInvocation，所以只在一轮的第一次调用（`invocationNum` 为 0）召回，上下文以 `injectSteps` 系统消息返回。Stop 不携带文本：捕获在后台进程中读取 `transcriptPath` 里上次之后已完成的步骤，然后 commit。PreToolUse 拒绝位置参数（名称以 `Path`、`File` 或 `Directory` 结尾）为 `viking://` URI 的文件工具，`run_command` 不检查。插件目录里还有五个 skill（每个都可以用 `/<name>` 调用），以及注册共享 MCP 代理的 `mcp_config.json`；`~/.gemini/config/mcp_config.json` 已定义 `openviking` 服务时，安装脚本不写这个文件。会话前缀为 `ag-`。用 `--harness antigravity` 安装，脚本会写入这个插件目录，不是它写入的同名目录会保留；见[社区插件文档](../../docs/zh/agent-integrations/08-community-plugins.md#antigravity-记忆集成)。

Cursor 工具采集依据 [Hooks 契约](https://cursor.com/docs/hooks) 和 [`@cursor/sdk` 1.0.38](https://registry.npmjs.org/@cursor/sdk/-/sdk-1.0.38.tgz) 的 MCP 执行器实现，后者在 `afterMCPExecution` 中提供 `tool_use_id`。旧事件若没有原生调用 ID，只保留文本采集；适配器不生成调用 ID 或推断完成状态。

## 体检

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

`<client>` 为 `cursor`、`trae`、`trae-cn`、`zcode` 或 `antigravity`。Kimi Code、CodeBuddy Code 和 Qoder CLI 运行已安装插件里的副本：

```bash
node "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/openviking-memory/agent-integrations/kimicode/scripts/ov-memory-doctor.mjs" --offline
node ~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/agent-integrations/codebuddy/scripts/ov-memory-doctor.mjs --offline
node ~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/agent-integrations/qoder/scripts/ov-memory-doctor.mjs --offline
```

每个副本检查它安装时对应的宿主；传 `cursor`、`trae`、`trae-cn`、`zcode`、`antigravity`、`kimicode`、`codebuddy` 或 `qoder` 可以覆盖。对 Kimi Code、CodeBuddy Code 和 Qoder CLI，还会检查插件自己的 manifest、hooks 和 MCP 文件，以及宿主是否已加载该插件。去掉 `--offline` 会连带探测服务端，加 `--json` 输出机器可读报告。在会话中，`ov-memory-doctor` skill 会为当前宿主运行同一个体检脚本。

## 测试

```bash
node --test examples/agent-hook-plugin/tests/*.test.mjs
```
