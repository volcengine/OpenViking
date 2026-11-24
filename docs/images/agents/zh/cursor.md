## 步骤1：安装

1. 在终端执行如下安装命令：

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
   ```

2. 安装器会依次询问以下信息：要安装的 harness（勾选 **Cursor**）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 点击「Customize → MCPs」，确认可以看到「openviking User」和「openviking Plugin」两项。
2. 点击「Customize → Hooks」，确认可以看到「openviking-memory」条目。

## 步骤3：开始使用

在 Cursor 中新开一个会话，发送：

```text
按 https://docs.openviking.net/agents/zh/onboarding.md 带我完成 OpenViking 首次使用。
```

Agent 会检查连接，并带你完成首次使用。

## 故障排查

| 问题 | 处理 |
|---|---|
| Hook 没跑 | 完全退出 Cursor，重启，再建会话 |
| 连接 / 鉴权失败 | 检查 `~/.openviking/ovcli.conf`，重启 Cursor |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/cursor-hooks.log` |

## 参考

- 手动配置文档：[Cursor](https://docs.openviking.net/zh/agent-integrations/12-cursor)
- 源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
