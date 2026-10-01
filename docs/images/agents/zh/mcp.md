### 步骤 1：MCP 配置

```json
{
  "mcpServers": {
    "ov-mcp-server": {
      "url": "{{OPENVIKING_BASE_URL}}/mcp",
      "headers": {
        "Authorization": "Bearer {{OPENVIKING_API_KEY}}"
      }
    }
  }
}
```

### 步骤 2：测试 MCP 工具连通性

重新连接客户端中的 MCP 服务，确认工具列表可见，再让助手调用 OpenViking 的 `health` 工具。工具调用成功后，再用 `list` 查看当前账号可访问的目录。`ov health` 只验证 CLI 连接，不能证明这个客户端的 MCP 已接通。
