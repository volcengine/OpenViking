## 步骤1：安装

1. 在终端执行以下安装命令：

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness opencode --dist tos
   ```

2. 安装器会依次询问以下信息：语言（English / 中文）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

1. 重启 OpenCode。
2. 输入 `/mcps` 命令，确认列表中显示 `openviking connected`。
3. 在对话中请求 OpenCode 召回相关记忆，验证是否会自动调用 `openviking_search`、`openviking_read`、`openviking_remember` 等工具。

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件没加载 | 检查 `~/.config/opencode/opencode.json` 是否包含 `@openviking/opencode-plugin` |
| 连错服务 / 401 | 检查 `~/.openviking/ovcli.conf` 和 API Key |
| 召回为空 | 确认云端实例里已有记忆 |

## 参考

- 手动配置文档：[OpenCode](https://docs.openviking.net/zh/agent-integrations/10-opencode)
- 源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
