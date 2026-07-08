# 社区插件

社区维护的各运行时集成。各插件在目标平台、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## AstrBot 插件

[AstrBot](https://github.com/AstrBotDevs/AstrBot) 是一个多平台 IM Bot 框架，支持 QQ、Telegram、Discord、飞书等 20+ 平台。

源码：[astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

为 AstrBot 提供群聊/私聊的自动捕获、LLM 请求前的语义召回，以及可配置的 venue 记忆隔离。

**安装**：在 AstrBot WebUI → 插件市场搜索 **OpenViking Memory** 并安装；或从链接安装：`https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

**主要特性**：

- 基于 hooks 的自动召回与捕获，模型不需要主动调用工具
- 三档隔离模式：`venue_user`（群/私聊各自独立）、`venue_user_fanout`（跨群共享）、`global_user`（全局共享）
- 四触发器自动 commit：消息计数、token 阈值、空闲超时、进程退出 flush
- 首次接入群聊时自动拉取平台历史消息入库

## OpenCode 插件

OpenViking 现在只保留一个面向 OpenCode 的统一插件，同时覆盖仓库上下文与长期记忆场景。

源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

这个插件通过 OpenCode plugin hooks 组合已索引仓库上下文、session 同步、生命周期 commit 与自动 recall。模型可调用工具来自 Claude Code / Codex 记忆插件同款的 OpenViking stdio MCP proxy。

### 前置条件

- [OpenCode](https://opencode.ai/)
- Node.js 18+
- OpenViking HTTP server
- 如果服务端启用了鉴权，需要一个可用的 OpenViking API key

先启动 OpenViking server：

```bash
openviking-server --config ~/.openviking/ov.conf
```

在另一个终端检查服务：

```bash
curl http://localhost:1933/health
```

### 安装

已发布的 npm 包是 `@openviking/opencode-plugin`。首次配置 OpenCode 时：

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@openviking/opencode-plugin"]
}
JSON
opencode
```

已有 `~/.config/opencode/opencode.json` 时，不要覆盖原文件；只把 `"@openviking/opencode-plugin"` 合并到已有的 `plugin` 数组。OpenCode 启动时会自动下载这个 npm 包。

如果当前环境不能通过 package 安装，请使用下面的源码安装路径。

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

源码安装后，OpenCode 能发现的目录结构应类似：

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

顶层 `openviking.js` 只是一个 wrapper，用来把 OpenCode 可发现的一级插件入口转发到实际安装目录。
源码安装请使用 `.js` wrapper；OpenCode 的本地插件扫描器会发现 JavaScript/TypeScript 插件文件。

### 配置

凭据与 Claude Code / Codex 记忆插件共用。可以先运行 setup 向导，或使用 `OPENVIKING_*` 环境变量：

```bash
node examples/opencode-plugin/scripts/setup.mjs
```

`~/.config/opencode/openviking-config.json` 现在只放行为旋钮：

```json
{
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.35,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000,
    "minQueryLength": 3
  },
  "commitTokenThreshold": 20000,
  "commitKeepRecentCount": 10,
  "profileTokenBudget": 10000,
  "resumeContextBudget": 32000
}
```

环境变量优先级高于 `ovcli.conf`：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
export OPENVIKING_ACCOUNT="default"   # 可选，仅 trusted-mode 部署需要
export OPENVIKING_USER="opencode"     # 可选，仅 trusted-mode 部署需要
export OPENVIKING_PEER_ID="opencode"  # 可选，peer 维度记忆路由需要
```

API key 会由 hooks 和 MCP proxy 作为 `Authorization: Bearer ...` 发送；`account` 和 `user` 是 trusted-mode headers；`peerId` 会作为 `X-OpenViking-Actor-Peer` 和捕获 session message 的 `peer_id` 使用。旧版 `openviking-config.json` 里的凭据字段仍会作为迁移 fallback 读取，但新安装建议使用 `ovcli.conf` 或环境变量。

### 验证

安装后重启 OpenCode。进入 OpenCode session 后，插件应暴露 `openviking` MCP server。OpenCode 会给 MCP 工具加 `openviking_` 前缀，例如：

- `openviking_recall`、`openviking_search`、`openviking_find`
- `openviking_read`、`openviking_list`、`openviking_grep`、`openviking_glob`
- `openviking_remember`、`openviking_add_resource`、`openviking_forget`、`openviking_health`
- `openviking_code_search`、`openviking_code_outline`、`openviking_code_expand`

可以让 OpenCode 搜索或浏览 OpenViking memory。运行时状态和错误日志会写入：

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-state.json
```

### 故障排查

| 问题 | 排查方向 |
|------|----------|
| 插件没有加载 | 确认 `~/.config/opencode/opencode.json` 引用了 `@openviking/opencode-plugin`；源码安装时确认 `~/.config/opencode/plugins/openviking.js` 存在 |
| MCP tools 连到了错误的 server | 检查 `~/.openviking/ovcli.conf`，或用 `OPENVIKING_*` 环境变量 / `OPENVIKING_PLUGIN_CONFIG` 指向正确配置 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还要检查 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking server 中已有 memories/resources，并且 `autoRecall.enabled` 为 `true` |
| 本地 `openviking_add_resource` 失败 | 传入文件路径而不是目录；目前还不支持自动上传本地目录 |

完整 tools、配置字段和运行时文件说明见 [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)。

## pi coding agent 扩展

OpenViking 也提供原生 pi 扩展。

源码：[examples/pi-coding-agent-extension](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension)

扩展使用 pi 生命周期事件完成 session-start profile 注入、当前 prompt recall、turn capture、阈值 commit、compact 前 commit 和 shutdown commit。它保留 pi 原生工具面（`viking_search`、`viking_read`、`viking_browse`、`viking_remember`、`viking_forget`、`viking_add_resource`、`viking_archive_expand`），不走 MCP。

通过统一安装器安装：

```bash
bash examples/memory-plugin-shared/install.sh --harness pi
```

凭据解析顺序是环境变量、`~/.openviking/ovcli.conf`、`~/.openviking/ov.conf`。扩展目录内的 `config.json` 只保留行为旋钮，例如 `recallTokenBudget`、`scoreThreshold`、`profileTokenBudget`、`resumeContextBudget` 和 `commitTokenThreshold`。
