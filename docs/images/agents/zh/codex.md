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

1. 启动 Codex。首次启动会停在 Hook 信任确认上，选 **Trust all and continue**：

   ```text
   Hooks need review
   6 hooks are new or changed.
   Hooks can run outside the sandbox after you trust them.

     1. Review hooks
   > 2. Trust all and continue
     3. Continue without trusting (hooks won't run)
   ```

   OpenViking 注册的 6 个 Hook 是（Codex 版本较旧时可能少几个）：

   ```text
   SessionStart
   UserPromptSubmit
   PreToolUse
   Stop
   SessionEnd
   PreCompact
   ```

2. 错过这个提示，或当时选了第 3 项，Hook 就不会运行。输入 `/hooks` 补上信任并开启条目，`/plugins` 里确认 `openviking-memory` 已启用——两个开关相互独立，都要是开着的。插件更新动了 Hook 时会再要求信任一次。

3. 验证 Profile 加载：信任完成后，提交第一条 Prompt（内容随意即可）。此时插件应自动加载 Profile——若对话开头出现记忆召回内容，则表明接入成功：

   ```text
   • UserPromptSubmit hook (completed)
     hook context: <openviking-context source="auto-recall" format="digest">
       OpenViking memory digest:
   ```

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
