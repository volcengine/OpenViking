为 [OpenCode](https://opencode.ai/) 提供跨会话长期记忆和已索引仓库上下文。安装一次后，插件会在每次 prompt 前自动召回记忆、把对话逐轮捕获进 OpenViking session，并在 compact 前自动 commit。模型可调用工具来自 Claude Code / Codex 插件同款的 OpenViking MCP proxy。

源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

## 步骤 1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

OpenCode 与 Claude Code、Codex 共用这一个安装器。它会询问要安装的工具、下载源（GitHub 或 TOS 镜像）、语言（English/中文）和 OpenViking 凭据；每一步都是幂等的，重复运行完全安全。TOS 渠道以本地文件方式安装插件——更新时重跑安装器即可。

<details>
<summary><b>手动安装</b></summary>

前置条件：OpenCode、Node.js 18+，以及可达的 OpenViking server（`curl http://localhost:1933/health`）。

1. **配置连接**——写入 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或安装后运行内置向导 `node <plugin-dir>/scripts/setup.mjs`。

2. **注册 npm 插件**（需要 npm registry 可达）——把 `"@openviking/opencode-plugin"` 合并进 `~/.config/opencode/opencode.json` 的 `plugin` 数组：

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "plugin": ["@openviking/opencode-plugin"]
   }
   ```

   OpenCode 启动时会自动下载该包，插件会自动注册它的 `openviking` MCP server。

</details>

## 步骤 2：验证

重启 OpenCode。插件会暴露带 `openviking_` 前缀的 MCP 工具，例如 `openviking_search`、`openviking_read`、`openviking_remember`、`openviking_health`。可以让 OpenCode 搜索或浏览 OpenViking memory。

行为旋钮（召回上限、commit 阈值等）在 `~/.config/opencode/openviking-config.json`；凭据来自 `~/.openviking/ovcli.conf` 或 `OPENVIKING_*` 环境变量。运行时日志：

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-state.json
```

## 故障排查

| 现象 | 修复 |
|------|------|
| 插件没有加载 | 确认 `~/.config/opencode/opencode.json` 引用了 `@openviking/opencode-plugin`；文件安装时确认 `~/.config/opencode/plugins/openviking.js` 存在 |
| MCP tools 连到了错误的 server | 检查 `~/.openviking/ovcli.conf`，或设置 `OPENVIKING_*` 环境变量 / `OPENVIKING_PLUGIN_CONFIG` |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY`；trusted-mode 部署还需要 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |
| recall 为空 | 确认 OpenViking 中已有 memories/resources，并且 `autoRecall.enabled` 为 `true` |

## 参考文档

- [插件 README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin) - 完整 tools、配置字段和运行时说明
- [部署指南](https://www.openviking.ai/zh/guides/03-deployment) - OpenViking server 与 CLI 配置
