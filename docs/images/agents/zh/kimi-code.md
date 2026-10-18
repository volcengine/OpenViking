## 步骤1：安装

1. 在终端执行以下安装命令：

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
   ```

2. 安装器会依次询问以下信息：要安装的 harness（勾选 **Kimi Code**）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 在 Kimi Code 中运行 `/reload`，或新建会话。
2. 确认 `~/.kimi-code/plugins/installed.json` 中有 `openviking-memory` 条目。
3. 新开会话，询问之前提到过的信息，确认召回生效。

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件没加载 | 检查 `~/.kimi-code/plugins/managed/openviking-memory/` 是否存在；不存在就重新执行安装，再运行 `/reload` |
| 连错服务 / 401 | 检查 `~/.openviking/ovcli.conf` 和 API Key |
| 需要日志 | 设置 `OPENVIKING_DEBUG=1`，查看 `~/.openviking/logs/kimicode-hooks.log` |
| 只卸载该插件 | `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness kimicode` |

## 参考

- 手动配置文档：[Kimi Code](https://docs.openviking.net/zh/agent-integrations/08-community-plugins)
- 源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
