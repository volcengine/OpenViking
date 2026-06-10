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

这个插件通过 OpenCode plugin hooks 组合已索引仓库上下文、OpenViking 记忆工具、session 同步、生命周期 commit 与自动 recall。原来的显式工具和上下文注入两类用法都应使用这个统一插件。

### 前置条件

- [OpenCode](https://opencode.ai/)
- Node.js 和 npm
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

如果使用已发布的包，把插件加入 `~/.config/opencode/opencode.json`。如果当前环境还不能通过 package 安装，请使用下面的源码安装路径。

```json
{
  "plugin": ["openviking-opencode-plugin"]
}
```

开发、调试或 PR 测试时，可以从本仓库源码安装：

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.mjs ~/.config/opencode/plugins/openviking.mjs
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cd ~/.config/opencode/plugins/openviking
npm install
```

源码安装后，OpenCode 能发现的目录结构应类似：

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

顶层 `openviking.mjs` 只是一个 wrapper，用来把 OpenCode 可发现的一级插件入口转发到实际安装目录。

### 配置

创建 `~/.config/opencode/openviking-config.json`：

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "peerId": "",
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.15,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000
  }
}
```

敏感信息建议用环境变量提供：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
export OPENVIKING_ACCOUNT="default"   # 可选，仅 trusted-mode 部署需要
export OPENVIKING_USER="opencode"     # 可选，仅 trusted-mode 部署需要
export OPENVIKING_PEER_ID="opencode"  # 可选，peer 维度记忆路由需要
```

环境变量优先级高于 `openviking-config.json`。`apiKey` 会作为 `X-API-Key` 发送；`account` 和 `user` 是 trusted-mode headers；`peerId` 会作为请求级 `peer_id` 用于 recall、search 和 session message 写入。

### 验证

安装后重启 OpenCode。进入 OpenCode session 后，插件应暴露这些 tools：

- `memsearch`、`memread`、`membrowse`
- `memgrep`、`memglob`
- `memadd`、`memremove`、`memqueue`
- `memcommit`

可以让 OpenCode 搜索或浏览 OpenViking memory，也可以要求它手动 commit 当前 session。运行时状态和错误日志会写入：

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-map.json
```

### 故障排查

| 问题 | 排查方向 |
|------|----------|
| 插件没有加载 | 确认 `~/.config/opencode/opencode.json` 引用了 `openviking-opencode-plugin`；源码安装时确认 `~/.config/opencode/plugins/openviking.mjs` 存在 |
| Tools 连到了错误的 server | 检查 `~/.config/opencode/openviking-config.json` 里的 `endpoint`，或用 `OPENVIKING_PLUGIN_CONFIG` 指向正确配置文件 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还要检查 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking server 中已有 memories/resources，并且 `autoRecall.enabled` 为 `true` |
| 本地 `memadd` 失败 | 传入文件路径而不是目录；目前还不支持自动上传本地目录 |

完整 tools、配置字段和运行时文件说明见 [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)。
