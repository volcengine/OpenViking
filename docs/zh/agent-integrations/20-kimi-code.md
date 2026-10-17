# Kimi Code

为 [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) 提供跨会话记忆。原生 managed 插件通过 hooks 自动召回、捕获并提交对话，通过 MCP 提供检索、读取和记忆管理工具。

源码：[examples/kimi-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/kimi-code-memory-plugin) | 宿主契约：[DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/DESIGN.md)

## 安装

前置条件：Kimi Code CLI ≥ 2.1.1（已在 2.2.0 上验证）、Node.js ≥ 18、一个可达的 OpenViking 服务（本地 `127.0.0.1:1933` 无需凭据）。还需要一个正在运行的 OpenViking 服务，见[所有集成的共同前置](./01-overview.md)。

在 Kimi TUI 里执行：

```text
/plugins install <本地目录的绝对路径 | zip URL>
```

安装来源可以是本仓库的 `examples/kimi-code-memory-plugin` 目录（绝对路径），或把该目录打包成的 zip（manifest 必须在压缩包根目录）。**不需要 curl 安装器**。安装完成后 `/reload` 或开新会话即可生效。

注意：kimi-code 的 GitHub URL 安装只支持 manifest 位于仓库根目录的仓库（不支持子目录），而本插件位于 monorepo 的 `examples/` 子目录，所以 GitHub URL 方式不适用——用本地目录或 zip。

CLI 会托管插件副本，并在首次提问时注入 profile 与 skill 清单、之后逐 prompt 召回——没有需要信任的 hook 审阅步骤。

## 升级与卸载

CLI 始终运行托管副本 `$KIMI_CODE_HOME/plugins/managed/openviking-memory/`（Kimi home 默认为 `~/.kimi-code/`），而不是你的源码目录——直接改源码在重装之前不生效。

```text
/plugins install <同样的安装来源>   # 升级：替换托管副本
/plugins remove openviking-memory   # 卸载：hook、MCP 与 skill 一并移除
```

也可以在 `/plugins` 管理器中查看、启用或移除插件。重装与卸载都只改动插件文件及其在 Kimi 中的注册，不触碰 OpenViking 服务端的记忆、凭据和 `~/.openviking/kimicode-plugin-state` 状态文件。

## 验证

插件目录自带诊断脚本：

```bash
node scripts/ov-memory-doctor.mjs --offline
```

它会检查安装记录、配置与连接、近期活动。设置 `OPENVIKING_DEBUG=1` 后，相关事件日志写入 `~/.openviking/logs/kimicode-hooks.log`。

行为验证：新开会话，询问之前提到过的信息，确认召回生效；多聊几轮（默认 8 轮）后对话会按阈值提交进 `kc-<session-id>` 会话。

## 工作原理

插件按以下 Kimi Code 生命周期事件处理记忆：

| Kimi Code 事件 | 插件行为 |
| --- | --- |
| `UserPromptSubmit` | 检索并注入与当前输入相关的记忆。profile 与 `<available-skills>` 清单也在首次提问时经此事件注入——`SessionStart` 的 stdout 会被 Kimi 丢弃（issue #2873），裸 stdout 文本是 Kimi 唯一可靠追加到上下文的通道。 |
| `Stop` | 增量解码会话的 `wire.jsonl`，把新的用户/助手/工具消息追加到 `kc-<session-id>` 会话；自上次提交达到阈值（默认 8 轮）时提交。 |
| `PreCompact` | 同上增量捕获；默认在压缩前提交。 |
| `SessionEnd` / `Interrupt` | 同上增量捕获，只要捕获到新消息总是提交。 |
| `SessionStart` | 有界重放此前发送失败的 pending 消息（每次 2 条）。 |
| `PreToolUse`（`Read` / `Glob` / `Grep`） | 拒绝直接访问 `viking://` URI，并引导模型使用 MCP 工具。 |

MCP 工具（`find`、`search`、`grep`、`read`、`remember`、`add_resource`、`add_skill` 等）经插件自带的 stdio 代理暴露；捆绑的 `openviking-memory`、`openviking-skills`、`ov-experience-memory` 三个 skill 教模型何时使用它们。所有 hook 同步执行，不起 detached 后台进程，Windows/macOS/Linux 行为一致。

> **已知局限**：subagent 对话（`agents/<subagent>/wire.jsonl`）尚未捕获（v2）。

## 配置

配置链：`OPENVIKING_*` 环境变量 → 工作区 `.openviking/config*.json` → 本机 registry → `ovcli.conf` 的 `plugin.kimicode` → `plugin` → 默认值。连接与凭据来自环境变量或当前 `ovcli.conf`（`ov config switch` 会同时切换 hook 与 MCP 代理）。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整的服务器 URL |
| `OPENVIKING_API_KEY` | — | API 密钥（将通过 `Authorization: Bearer` 标头发送） |
| `OPENVIKING_AUTO_RECALL` | 开 | 每次 prompt 时召回并注入 profile/记忆 |
| `OPENVIKING_AUTO_CAPTURE` | 开 | 在 `Stop`/`PreCompact`/`SessionEnd`/`Interrupt` 上增量捕获 |
| `OPENVIKING_COMMIT_TURN_THRESHOLD` | `8` | 阈值提交之间捕获的轮数 |
| `OPENVIKING_AUTO_COMMIT_ON_COMPACT` | 开 | 压缩前提交 |
| `OPENVIKING_RECALL_LIMIT` / `OPENVIKING_SCORE_THRESHOLD` | `10` / `0.35` | 召回条数与分数阈值 |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | `actor` 时只检索本工作区 peer 的记忆 |
| `OPENVIKING_PENDING_REPLAY_LIMIT` | `2` | 每次 `SessionStart` 重放的 pending 条数 |
| `OPENVIKING_DEBUG` | `false` | 是否将日志写入 `~/.openviking/logs/kimicode-hooks.log` |

这些开关大多也可以写在 `ovcli.conf` 的 `plugin` 段下——见[插件配置](../configuration/02-client.md#插件配置)。完整开关清单见共享插件的 [`config-schema.mjs`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared)。Kimi 插件的状态文件在 `~/.openviking/kimicode-plugin-state`，可用 `OPENVIKING_KIMICODE_STATE_DIR` 迁移。

## 故障排查

| 现象 | 可能原因 | 修复方法 |
|------|------|------|
| 插件已装但没有自动召回/捕获 | 插件被禁用，或会话未重载 | 在 `/plugins` 确认 `openviking-memory` 已启用，然后 `/reload` 或开新会话 |
| 改了插件源码不生效 | CLI 始终运行托管副本，不看源码目录 | `/plugins install <同样的安装来源>` 重装后再验证 |
| MCP 工具调用报认证错误 | 凭据缺失或过期 | 修正 `~/.openviking/ovcli.conf` 或 `OPENVIKING_*` 凭据环境变量后重开会话 |
| MCP 工具调用报连接错误 | 服务器不可达或 URL 配置错误 | 执行 `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` 检查服务器状态 |
| 不确定哪里出问题 | — | 运行 `node scripts/ov-memory-doctor.mjs --offline`；设置 `OPENVIKING_DEBUG=1` 查看 `~/.openviking/logs/kimicode-hooks.log` |

## 参见

- [集成能力参考](./16-capability-reference.md)
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/README_CN.md) — 安装、行为与配置速览
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/DESIGN.md) — 宿主契约与设计记录
- [MCP 客户端](./06-mcp-clients.md) — MCP 协议、工具列表及其他客户端
- [社区插件 → Kimi Code](./08-community-plugins.md#kimi-code-记忆集成) — 旧版 curl 安装器路径（legacy）
