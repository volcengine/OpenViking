# MCP Integration

Step 1 MCP configuration

```json
{
  "mcpServers": {
    "ov-mcp-server": {
      "url": "https://api.vikingdb.cn-beijing.volces.com/openviking/mcp",
      "headers": {
        "Authorization": "Bearer ZGVmYXV********YzdlZjhiMg"
      }
    }
  }
}
```

**Important**: The `Authorization` value must include the `Bearer` prefix and a space. The full format is `Bearer <API Key>`.

Step 2 Test MCP tool connectivity

Enter `ov health` to check the OpenViking version and connection status.
