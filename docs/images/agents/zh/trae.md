## 步骤1：安装

1. 根据所使用的版本，在终端执行对应的安装命令：

   **Trae 国际版**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos
   ```

   **Trae 中国版**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos
   ```

2. 安装器会依次询问以下信息：语言（English / 中文）、OpenViking 凭据。在 OpenViking 凭据配置中，选择连接至「火山引擎 OpenViking 云服务 [api.vikingdb.cn-beijing.volces.com]」，并填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## 步骤2：验证

在「设置 → MCP → 已配置的 MCP Servers」中确认能够看到 `openviking` 条目即表示接入成功。

## 故障排查

| 问题 | 处理 |
|---|---|
| 没有自动召回 | 完全退出 TRAE，重启，再建会话 |
| 连接 / 鉴权失败 | 检查 `~/.openviking/ovcli.conf`，重启 TRAE |
| 需要日志 | `~/.openviking/logs/trae-hooks.log` 或 `trae-cn-hooks.log` |

## 参考

- 手动配置文档：[TRAE](https://docs.openviking.net/zh/agent-integrations/13-trae)
- 源码：[examples/trae-memory-hooks](https://github.com/volcengine/OpenViking/tree/main/examples/trae-memory-hooks)
