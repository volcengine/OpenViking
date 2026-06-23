
OpenViking 现在只保留一个面向 OpenCode 的统一插件，同时覆盖仓库上下文与长期记忆场景。

## `opencode-plugin`

源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

这个插件通过 OpenCode plugin hooks 组合已索引仓库上下文、OpenViking 记忆工具、session 同步、生命周期 commit 与自动 recall。

## 步骤 1：准备 OpenViking

先安装 OpenCode、Node.js/npm，并准备一个 OpenViking HTTP server。启动 OpenCode 前先启动服务：

```bash
openviking-server --config ~/.openviking/ov.conf
```

在另一个终端检查服务：

```bash
curl http://localhost:1933/health
```

远程或多租户部署需要提前准备 OpenViking API key。

## 步骤 2：安装插件

如果使用已发布的包，把插件加入 `~/.config/opencode/opencode.json`。如果当前环境还不能通过 package 安装，请使用下面的源码安装路径。

```json
{
  "plugin": ["openviking-opencode-plugin"]
}
```

开发、调试或 PR 测试时，可以从 OpenViking 仓库复制插件：

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

源码安装会形成如下结构：

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

## 步骤 3：配置 OpenViking 连接

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

环境变量优先级高于配置文件。

## 步骤 4：验证

重启 OpenCode。插件应暴露 `memsearch`、`memread`、`membrowse`、`memgrep`、`memglob`、`memadd`、`memremove`、`memqueue` 和 `memcommit`。

可以让 OpenCode 浏览 OpenViking 或 commit 当前 session。异常时查看运行时日志：

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-map.json
```

## 故障排查

| 现象 | 修复 |
|------|------|
| 插件没有加载 | package 安装检查 `~/.config/opencode/opencode.json`；源码安装检查 `~/.config/opencode/plugins/openviking.mjs` |
| Tools 连到了错误的 server | 检查 `endpoint`，或用 `OPENVIKING_PLUGIN_CONFIG` 指向正确配置文件 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还需要 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking 中已有 memories/resources，并且 `autoRecall.enabled` 为 `true` |

## 参考文档

- [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin) - 完整 tools、配置字段和运行时说明
- [部署指南](https://www.openviking.ai/zh/guides/03-deployment) - OpenViking server 与 CLI 配置
