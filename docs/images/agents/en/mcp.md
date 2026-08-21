### Step 1: MCP configuration

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

### Step 2: Test MCP tool connectivity

Enter `ov health` to check the OpenViking version and connection status.
```bash
ov health
```
