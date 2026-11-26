## 步骤1：安装

1. 在终端执行以下安装命令：

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
   ```

2. 安装器会依次询问以下信息：要安装的 harness（勾选 **Kimi Code**）、OpenViking 凭据。连接配置中，选择 **OpenViking 托管服务**，再选择 **Volcengine** 或 **BytePlus**。

   - **Volcengine**：输入服务 API Key，安装器会提供服务地址。
   - **BytePlus**：先输入实例的完整 HTTPS 服务地址，再输入 API Key。详见 [BytePlus 接入指南](https://docs.openviking.ai/zh/guides/27-byteplus-setup)。

   旧版安装器连接 BytePlus 时使用**自定义 URL**。请使用所选实例的 API Key：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 在 Kimi Code 中运行 `/reload`，或新建会话。
2. 确认 `~/.kimi-code/plugins/installed.json` 中有 `openviking-memory` 条目。
3. 新开会话，询问之前提到过的信息，确认召回生效。

## 步骤3：开始使用

在 Kimi Code 中新开一个会话，发送：

```text
按 https://docs.openviking.net/agents/zh/onboarding.md 带我完成 OpenViking 首次使用。
```

Agent 会检查连接，并带你完成首次使用。

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
