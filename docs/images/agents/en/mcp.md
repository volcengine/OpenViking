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

Reconnect the MCP service in your client and confirm that its tools are listed. Ask the assistant to call OpenViking's `health` tool, then use `list` to check the directories accessible to your account. `ov health` checks the CLI connection; it does not verify this client's MCP connection.
