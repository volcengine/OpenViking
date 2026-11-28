# OpenViking 记忆：Hook 宿主插件

本目录是所有运行 hook 脚本的宿主共用的 OpenViking 记忆插件：Claude Code、Codex、Cursor、TRAE / TRAE CN、Antigravity、ZCode、Kimi Code、CodeBuddy Code 和 Qoder CLI。每个宿主都运行 `lib/flow/` 里的同一套流程：每次 prompt 召回，会话开始注入画像和 skill 清单，回合结束捕获，压缩时和会话结束时 commit，服务端没接收的写入进入离线队列。宿主只提供一个小模块，描述自己的事件、payload 和输出格式。共享行为与配置见[能力参考](../../docs/zh/agent-integrations/16-capability-reference.md)。

```bash
curl -fsSL https://openviking.ai/install | bash
```

安装脚本会询问要安装哪些宿主以及 OpenViking 连接信息。要从仓库 checkout 安装，在仓库根目录运行 `bash examples/memory-plugin-shared/install.sh --harness <cursor|trae|trae-cn|zcode|kimicode|codebuddy|qoder|antigravity>`。

## 目录结构

```text
agent-hook-plugin/
├── .claude-plugin/plugin.json   Claude Code manifest
├── hooks/hooks.json             Claude Code hooks
├── .mcp.json                    Claude Code MCP 服务
├── mods/  commands/             Claude Code mod 与斜杠命令
├── .codex-plugin/plugin.json    Codex manifest（指向 hosts/codex/）
├── lib/                         共享库
│   └── flow/                    所有宿主运行的流程
├── scripts/
│   ├── hook.mjs                 hook 入口：hook.mjs <event> <host>
│   ├── uri-guard.mjs            viking:// 守卫：uri-guard.mjs <host>
│   ├── statusline.mjs           Claude Code 状态栏
│   └── ov-memory-doctor.mjs     体检
├── servers/mcp-proxy.mjs        MCP 代理
├── skills/                      skill
└── hosts/<host>/                host.mjs、decode.mjs、注册文件、fixtures/
```

## 宿主

| 宿主 | 文档 |
|------|------|
| Claude Code | [02-claude-code.md](../../docs/zh/agent-integrations/02-claude-code.md) |
| Codex | [04-codex.md](../../docs/zh/agent-integrations/04-codex.md) |
| Cursor | [12-cursor.md](../../docs/zh/agent-integrations/12-cursor.md) |
| TRAE / TRAE CN | [13-trae.md](../../docs/zh/agent-integrations/13-trae.md) |
| CodeBuddy Code | [20-codebuddy.md](../../docs/zh/agent-integrations/20-codebuddy.md) |
| Qoder CLI | [21-qoder.md](../../docs/zh/agent-integrations/21-qoder.md) |
| ZCode、Kimi Code、Antigravity | [08-community-plugins.md](../../docs/zh/agent-integrations/08-community-plugins.md) |

各宿主的事件列在能力参考中；如何接入新宿主见 [18-plugin-development.md](../../docs/zh/agent-integrations/18-plugin-development.md)。

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

去掉 `--offline` 会连带探测服务端，加 `--json` 输出机器可读报告。在会话中，`ov-memory-doctor` skill 会为当前宿主运行体检。对 Claude Code 和 Codex，已安装插件里的 `scripts/ov-memory-doctor.mjs` 会转交给 `hosts/claude-code/ov-memory-doctor.mjs` 或 `hosts/codex/ov-memory-doctor.mjs`，宿主取自参数（`claude-code`、`codex`）或运行环境；见各自的文档页。

## 测试

```bash
node --test examples/memory-plugin-shared/flow.test.mjs examples/memory-plugin-shared/hosts.test.mjs examples/memory-plugin-shared/hook-entry.test.mjs
```
