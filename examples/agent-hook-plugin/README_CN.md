# OpenViking 记忆：配置驱动的 Hook 宿主

Cursor、TRAE、TRAE CN 与 ZCode 的安装方式相同：共享安装脚本把生命周期 hook 与 MCP server 条目写进宿主自己的配置文件，并在集成目录旁装配 OpenViking 运行时。四家都不需要注册 marketplace，也不需要单独配置 MCP。

```bash
bash examples/memory-plugin-shared/install.sh --harness cursor
bash examples/memory-plugin-shared/install.sh --harness trae,trae-cn
bash examples/memory-plugin-shared/install.sh --harness zcode
```

> **需要支持 `viking://~` home alias 的 OpenViking 服务端。** 召回通过 `viking://~/memories` 与 `viking://~/skills` 指向调用者自己的上下文空间；不带 uid 的 `viking://user/memories` 简写会被较新的服务端拒绝。

## Hook 做什么

- **会话开始** — 注入用户画像与偏好，并重放离线会话排队的写入。
- **提交 prompt** — 搜索与 prompt 相关的记忆并注入，按事件 id 与 500ms 窗口去重。
- **工具调用前** — 拦截本地文件与 shell 工具对 `viking://` 虚拟路径的访问，引导回 OpenViking MCP 工具。
- **Stop** — 捕获完成的回合并提交 OpenViking 会话。Cursor 另外在压缩前与会话结束时执行；ZCode 先应答，再在 detached worker 里完成写入。

## 目录结构

`scripts/hook.mjs` 是所有 hook 命令的唯一入口，持有四家共用的状态机——防抖、prompt 去重、召回缓存、跨进程锁——差异部分向 `hosts/` 下的适配器索取：事件词汇、响应信封、如何从 payload 读出 prompt、如何采集完成的回合。`scripts/uri-guard.mjs` 与 `servers/mcp-proxy.mjs` 同样各只有一份，按安装器传入的 client id 选择宿主。

`hosts/<host>/` 只放宿主当作配置读取的东西——`hooks.json`、`.mcp.json`、`openviking.integration.json`，以及 Cursor 的 rule 与 skill。可执行文件一律放在上一层，因为 `../../memory-plugin-shared/lib` 这条相对路径要在本仓库和安装后的 `~/.openviking/agent-integrations/<client>/` 两处同时成立。

记忆逻辑本身不在这里：召回、批量写入、待处理队列、凭据解析与 MCP 代理都来自 `examples/memory-plugin-shared/lib`，由安装脚本复制到 `~/.openviking/agent-integrations/memory-plugin-shared/lib`。

## 各宿主差异

- **Cursor** — 七个事件，其中 `preCompact` 与 `sessionEnd` 是本插件里独有的。Stop 时 `capturedSinceCommit` 达到阈值才 commit，压缩前无条件 commit。会话前缀 `cu-`。见 [Cursor 接入文档](../../docs/zh/agent-integrations/12-cursor.md)。
- **TRAE / TRAE CN** — 采集直接读 Stop 事件的 `prompt`、`text_content`、`last_assistant_message`，不解析 transcript；每次带内容的 Stop 都 commit。会话前缀 `tr-` 与 `trcn-`。见 [TRAE 接入文档](../../docs/zh/agent-integrations/13-trae.md)。
- **ZCode** — rollout 文件是权威增量对话源：稳定的 host `turnId` 用于去重，也让后续 Stop 能补回漏掉的回合，hook stdin 只是兜底。ZCode 不支持 `PreCompact` 与 `SessionEnd`，因此每次 Stop 都 commit 来补足这两个信号。它的输出 schema 是严格的，所以放行时不写任何内容。会话前缀 `zc-`。已验证的扩展面记录在 [DESIGN.md](./DESIGN.md)。

## 体检

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

client 默认取这份副本安装时对应的那个；传 `cursor`、`trae`、`trae-cn` 或 `zcode` 可以覆盖，去掉 `--offline` 会连带探测服务端，加 `--json` 输出机器可读的报告。

## 测试

```bash
node --test examples/agent-hook-plugin/tests/*.test.mjs
```
