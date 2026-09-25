## 步骤1：安装

1. 在终端执行如下安装命令：

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
   ```

2. 安装器会依次询问以下信息：语言（English / 中文）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 启动 Codex。首次启动会显示类似下面的 hook 审阅提示（具体界面随版本变化），选 **Trust all and continue**：

   ```text
   Hooks need review
   6 hooks are new or changed.
   Hooks can run outside the sandbox after you trust them.

     1. Review hooks
   > 2. Trust all and continue
     3. Continue without trusting (hooks won't run)
   ```

   如果跳过了提示或选了第 3 项，打开 `/hooks`，检查 OpenViking 的命令，再信任并开启需要使用的 hooks。在 `/plugins` 中确认 `openviking-memory` 已启用。新增或变更的 hooks 需要重新检查。
2. 确认 OpenViking MCP 工具可用，让 Codex 调用 `health` 和 `list`。这一步验证工具连接；自动召回还需开启 `UserPromptSubmit`，对话采集需开启 `Stop`。
3. 在已有记忆的工作目录中询问之前保存的信息，检查 hook 输出或调试日志。召回内容类似：

   ```text
   • UserPromptSubmit hook (completed)
     hook context: <openviking-context source="auto-recall" format="digest">
       OpenViking memory digest:
   ```

   新账号可能还没有 profile 或相关记忆可注入。会话提交时机和完整验证步骤见下方文档。

## 故障排查

| 问题 | 处理 |
|---|---|
| 鉴权失败 | 检查 `~/.openviking/ovcli.conf` 的 `api_key`，重启 Codex |
| 连接失败 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| `6 hooks need review`，或 Hook 不生效 | `/hooks` 里信任并开启，`/plugins` 里确认插件已启用 |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/codex-hooks.log` |

## 参考

- 手动配置文档：[Codex](https://docs.openviking.net/zh/agent-integrations/04-codex)
- 原理博客：[OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- 源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)
