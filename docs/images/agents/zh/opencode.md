## 步骤1：安装

1. 在终端执行以下安装命令：

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
   ```

2. 安装器会依次询问以下信息：要安装的 harness（勾选 **OpenCode**）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 重启 OpenCode。
2. 输入 `/mcps` 命令，确认列表中显示 `openviking connected`。
3. 在对话中明确要求调用 `openviking_search` 和 `openviking_read`，验证工具可用。自动召回由 hook 完成，不需要模型调用这些工具；可另开会话，询问已保存的信息来检查。

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件没加载 | 检查 `~/.config/opencode/plugins/openviking.js` 是否存在；不存在就重新执行安装 |
| 连错服务 / 401 | 检查 `~/.openviking/ovcli.conf` 和 API Key |
| 召回为空 | 确认云端实例里已有记忆 |

## 参考

- 手动配置文档：[OpenCode](https://docs.openviking.net/zh/agent-integrations/10-opencode)
- 源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
