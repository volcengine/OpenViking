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

输入 `ov health` 检查 ov 的版本和连接状态
```bash
ov health
```
