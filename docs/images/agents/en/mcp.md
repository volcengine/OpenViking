### Step 1: MCP configuration

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

### Step 2: Test MCP tool connectivity

Enter `ov health` to check the OpenViking version and connection status.
```bash
ov health
```
