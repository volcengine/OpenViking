# MCP Integration

## Step 1: Configure MCP

Add the OpenViking MCP server to your MCP client configuration:

```json
{
  "mcpServers": {
    "ov-mcp-server": {
      "url": "https://api.vikingdb.cn-beijing.volces.com/openviking/mcp",
      "headers": {
        "Authorization": "Bearer <API Key>"
      }
    }
  }
}
```

The `Authorization` value must include the `Bearer` prefix and one space before the API key.

## Step 2: Verify the connection

Ask your agent to run:

```text
ov health
```

If the tool returns the OpenViking version and service status, the MCP connection is ready.
