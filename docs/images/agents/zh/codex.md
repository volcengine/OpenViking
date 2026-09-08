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

1. 启动 Codex。
2. 审批 Hooks：输入 `/hooks`，系统将提示类似 `4 hooks need review` 的信息，逐一审批通过。其中 OpenViking 相关的 4 个 Hook 为：

   ```text
   SessionStart
   UserPromptSubmit
   Stop
   PreCompact
   ```

3. 验证 Profile 加载：审批完成后，提交第一条 Prompt（内容随意即可）。此时插件应自动加载 Profile——若对话开头出现记忆召回内容，则表明接入成功：

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
| `4 hooks need review` | `/hooks` 里批准 |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/codex-hooks.log` |

## 参考

- 手动配置文档：[Codex](https://docs.openviking.net/zh/agent-integrations/04-codex)
- 原理博客：[OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- 源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)
