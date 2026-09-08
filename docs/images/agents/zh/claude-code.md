## 步骤1：安装

由于 Claude Code 安全策略限制，可能无法自动完成配置，推荐在终端中手动执行以下步骤。

1. 在终端执行如下安装命令：

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness claude --dist tos
   ```

2. 安装器会依次询问以下信息：语言（English / 中文）、OpenViking 凭据、是否开启 Statusline 状态栏。
3. 在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

4. OpenViking StatusLine 是输入框下方的一行状态提示栏，用于实时展示 OpenViking 记忆插件的运行状态。可根据个人需要选择「开启」或「跳过」。状态提示栏示例如下：

   ```text
   OV ✓ │ Fable 5 · ctx 42% │ ↪ 6 mem (0.92) · 50ms │ ✎ 573/20k · 2 arch
   ```

## 步骤2：验证

1. 重启 Claude Code。
2. 执行 `/plugins` 命令，确认 installed 列表中显示 `openviking-memory` 已安装，且 `openviking` MCP 已连接：

   ```text
   User
     ❯ openviking-memory Plugin · openviking · ✔ enabled
       └ openviking MCP · ✔ connected
   ```

3. 执行 `/mcp` 命令，确认显示如下信息：

   ```text
   Built-in MCPs (always available)
     ❯ plugin:openviking-memory:openviking · ✔ connected · 10 tools
   ```

4. 执行 `/openviking-memory:ov` 命令，确认服务状态正常：

   ```text
   OpenViking Memory Status
     ✅ Status: OpenViking server is healthy and running
   ```

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件未激活 | 重跑安装，或检查 `~/.openviking/ovcli.conf` |
| 召回为空 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| 401 / 403 | 重新粘贴 API Key |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/cc-hooks.log` |

## 参考

- 手动配置文档：[Claude Code](https://docs.openviking.net/zh/agent-integrations/02-claude-code)
- 原理博客：[OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- 源码：[examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)
