# OpenViking 记忆：轻量 Hook 宿主

Cursor、TRAE、TRAE CN 与 ZCode 使用宿主配置文件；Kimi Code 使用原生托管插件目录。它们共用同一个 dispatcher 和记忆运行时，安装脚本在安装时组装依赖，不在仓库里为每个宿主提交共享代码副本。

```bash
bash examples/memory-plugin-shared/install.sh --harness cursor
bash examples/memory-plugin-shared/install.sh --harness trae,trae-cn
bash examples/memory-plugin-shared/install.sh --harness zcode
bash examples/memory-plugin-shared/install.sh --harness kimicode
```

> **需要支持 `viking://~` home alias 的 OpenViking 服务端。** 召回通过 `viking://~/memories` 与 `viking://~/skills` 指向调用者自己的上下文空间；不带 uid 的 `viking://user/memories` 简写会被较新的服务端拒绝。

## Hook 做什么

- **会话开始** — 注入用户画像与偏好，外加 `<available-skills>` 清单，列出用户自己的和账号内共享的 OpenViking skill；同时重放离线会话排队的写入。Kimi 启动时只重放一个有界小批次，并在第一次成功的 prompt hook 注入画像。
- **提交 prompt** — 搜索与 prompt 相关的记忆和 skill 并注入，按事件 id 与 500ms 窗口去重。
- **工具调用前** — 拦截本地文件工具对 `viking://` 虚拟路径的访问，引导回 OpenViking MCP 工具。在 TRAE 上，带 `viking://` URI 的 shell 命令照常执行，并附加一条指向同一组工具的提示。
- **Stop** — 捕获完成的回合并按宿主策略提交 OpenViking 会话。Kimi 还使用 PreCompact、SessionEnd 与同步 Interrupt 信号。

## 目录结构

`scripts/hook.mjs` 是所有 hook 命令的唯一入口，持有五家共用的状态机——防抖、prompt 去重、召回缓存、跨进程锁——差异部分向 `hosts/` 下的适配器索取：事件词汇、响应信封、如何从 payload 读出 prompt、如何采集完成的回合。`scripts/uri-guard.mjs` 与 `servers/mcp-proxy.mjs` 同样各只有一份，按安装器传入的 client id 选择宿主。

根目录的 `plugin.json` 是宿主无关的包元数据，只用于版本检查和诊断。Kimi 的原生 manifest 位于 `hosts/kimicode/`，安装组装时复制到插件根目录。

`hosts/<host>/` 只放宿主配置或原生 manifest；可执行适配器放在上一层。`../../memory-plugin-shared/lib` 这条相对路径在源码树、配置型安装和 Kimi 组装后的原生插件里都成立。

记忆逻辑本身不在这里：召回、批量写入、待处理队列、凭据解析与 MCP 代理都来自 `examples/memory-plugin-shared/lib`，由安装脚本复制到 `~/.openviking/agent-integrations/memory-plugin-shared/lib`。

## 各宿主差异

- **Cursor** — 六个事件，其中 `preCompact` 与 `sessionEnd` 是本插件里独有的。Stop 时 `capturedSinceCommit` 达到阈值才 commit，压缩前无条件 commit。会话前缀 `cu-`。见 [Cursor 接入文档](../../docs/zh/agent-integrations/12-cursor.md)。
- **TRAE / TRAE CN** — 采集直接读 Stop 事件的 `prompt`、`text_content`、`last_assistant_message`，不解析 transcript；每次带内容的 Stop 都 commit。会话前缀 `tr-` 与 `trcn-`。见 [TRAE 接入文档](../../docs/zh/agent-integrations/13-trae.md)。
- **ZCode** — rollout 文件是权威增量对话源：稳定的 host `turnId` 用于去重，也让后续 Stop 能补回漏掉的回合，hook stdin 只是兜底。ZCode 不支持 `PreCompact` 与 `SessionEnd`，因此每次 Stop 都 commit 来补足这两个信号。它的输出 schema 是严格的，所以放行时不写任何内容。会话前缀 `zc-`。已验证的扩展面记录在 [DESIGN.md](./DESIGN.md)。
- **Kimi Code** — `wire.jsonl` 是权威 transcript；UserPromptSubmit 输出原始上下文文本。Stop、PreCompact、SessionEnd 可后台写入，Interrupt 保持同步并共享 2 秒的 OpenViking 请求预算。安装脚本生成自包含原生插件，不修改旧的 `config.toml` 或 `mcp.json`。会话前缀 `kc-`。

## 体检

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

Kimi 的 managed plugin 需要使用它的实际安装路径：

```bash
node "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/openviking-memory/agent-integrations/kimicode/scripts/ov-memory-doctor.mjs" kimicode --offline
```

对于配置型安装副本，client 默认取安装时对应的宿主；传 `cursor`、`trae`、`trae-cn` 或 `zcode` 可以覆盖。去掉 `--offline` 会连带探测服务端，加 `--json` 输出机器可读报告。

## 测试

```bash
node --test examples/agent-hook-plugin/tests/*.test.mjs
```
