DeerFlow 支持通过 MCP Server 接入 OpenViking。MCP 接入的核心价值是打通知识检索能力，让 DeerFlow Agent 能够在任务执行过程中主动搜索、读取和使用 OpenViking 中的记忆与知识。

## 步骤 1：配置 OpenViking 鉴权信息

在启动 DeerFlow 的环境中配置 OpenViking MCP 服务地址和 API Key：

```bash
export OPENVIKING_MCP_URL="https://api.vikingdb.cn-beijing.volces.com/openviking/mcp"
export OPENVIKING_API_KEY="[TODO]your-api-key"
```

如果 DeerFlow 使用 `.env` 文件，请写入同名变量，并确认启动命令会加载该文件。

## 步骤 2：创建 MCP 配置文件

在 DeerFlow 项目中创建 MCP 配置文件，例如 `mcp.json`：

```json
{
  "mcpServers": {
    "openviking": {
      "url": "${OPENVIKING_MCP_URL}",
      "headers": {
        "Authorization": "Bearer ${OPENVIKING_API_KEY}"
      }
    }
  }
}
```

如果 DeerFlow 的 MCP 配置文件路径已有约定，请将以上配置合并到现有文件中。

## 步骤 3：配置 OpenViking MCP Server

在 DeerFlow 的 Agent 或工具配置中启用该 MCP Server，并确保 server 名称与 MCP 配置文件中的 `openviking` 保持一致。

建议允许 DeerFlow 使用 OpenViking 的搜索、读取、目录浏览和健康检查工具，以覆盖常见的记忆检索与知识读取场景。

## 步骤 4：重启 DeerFlow

保存 MCP 配置后重启 DeerFlow：

```bash
pnpm dev
```

重启后检查日志，确认 `openviking` MCP Server 已成功加载，且工具列表可以正常获取。

## 步骤 5：验证 MCP 工具是否可用

在 DeerFlow 中发起任务，让 Agent 主动检查 OpenViking 连接状态：

```text
请使用 OpenViking MCP 工具检查当前服务是否可用。
```

也可以让 DeerFlow 搜索一条已存在的记忆或资源：

```text
请从 OpenViking 中搜索和当前项目相关的记忆。
```

如果 Agent 能够调用 OpenViking MCP 工具并返回结果，说明 MCP 接入成功。

## 故障排查

| 现象 | 处理方式 |
|------|----------|
| MCP Server 未加载 | 检查 MCP 配置文件路径是否被 DeerFlow 读取，server 名称是否配置一致 |
| 连接失败或超时 | 确认网络可访问 `api.vikingdb.cn-beijing.volces.com`，必要时配置代理或网络白名单 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY` 是否正确、是否过期，以及请求头是否为 `Authorization: Bearer <API Key>` |
| Agent 不主动调用工具 | 在 DeerFlow 的系统提示词或工具策略中明确允许使用 OpenViking MCP 工具 |
| 搜索结果为空 | 确认 OpenViking 中已有相关记忆或知识资源，并尝试放宽搜索关键词 |
