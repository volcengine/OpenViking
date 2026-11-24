## 步骤1：安装

1. 在终端执行以下安装命令：

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
   ```

2. 安装器会依次询问以下信息：要安装的 harness（勾选 **ZCode**）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 重启 ZCode。
2. 确认 `~/.zcode/cli/config.json` 包含 `hooks.enabled: true`、`hooks.events` 下的 OpenViking 条目，以及 `mcp.servers.openviking`。
3. 运行 `node ~/.openviking/agent-integrations/zcode/scripts/ov-memory-doctor.mjs`，检查安装状态和服务端连接。

## 步骤3：开始使用

在 ZCode 中新开一个会话，发送：

```text
按 https://docs.openviking.net/agents/zh/onboarding.md 带我完成 OpenViking 首次使用。
```

Agent 会检查连接，并带你完成首次使用。

## 故障排查

| 问题 | 处理 |
|---|---|
| Hook 未执行 | 重新执行安装并重启 ZCode |
| MCP 工具未出现 | 检查 `~/.zcode/cli/config.json` 中 `mcp.servers.openviking` 的绝对路径命令 |
| 重复捕获 | 先运行 `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness zcode`，再重新安装 |
| 需要日志 | 设置 `OPENVIKING_DEBUG=1`，查看 `~/.openviking/logs/zcode-hooks.log` |

## 参考

- 手动配置文档：[ZCode](https://docs.openviking.net/zh/agent-integrations/08-community-plugins)
- 源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
