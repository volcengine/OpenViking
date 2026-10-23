## 步骤1：安装

1. 在终端执行以下安装命令：

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
   ```

2. 安装器会依次询问以下信息：要安装的 harness（勾选 **pi**）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 启动 `pi`，确认底部状态栏显示 `OV ✓`。
2. 输入 `/viking` 命令，确认显示当前 session 映射和已注册的 `openviking_*` 工具数量。
3. 新开会话，询问之前提到过的信息，确认召回生效。

## 步骤3：开始使用

在 pi 中新开一个会话，发送：

```text
按 https://docs.openviking.net/agents/zh/onboarding.md 带我完成 OpenViking 首次使用。
```

Agent 会检查连接，并带你完成首次使用。

## 故障排查

| 问题 | 处理 |
|---|---|
| 扩展没加载 | 检查 `~/.pi/agent/extensions/openviking/index.ts` 是否存在；不存在就重新执行安装。不要再用 `pi install` 注册，否则会加载两遍 |
| `/viking` 命令出现两个 | 运行 `pi remove ~/.pi/agent/extensions/openviking`，删掉多余的 `packages` 条目 |
| 状态栏显示 `OV ✗` / 401 | 检查 `~/.openviking/ovcli.conf` 和 API Key |
| 状态栏显示 `tools ✗` | 运行 `/viking` 查看握手错误；`/mcp` 拒绝 root API Key，请改用 user key |

## 参考

- 手动配置文档：[pi](https://docs.openviking.net/zh/agent-integrations/11-pi)
- 源码：[examples/pi-coding-agent-extension](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension)
