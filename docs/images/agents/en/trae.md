# TRAE MCP Integration

## Step 1: Get an API Key

Copy the API Key from the OpenViking console. It will be used as the MCP `Authorization` token.

## Step 2: Open TRAE MCP settings

In TRAE, open Settings and select **MCP** to enter the MCP Servers page.

## Step 3: Add an MCP server

Choose manual configuration and paste:

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

## Step 4: Enable and verify

After saving, TRAE should load `ov-mcp-server` and enable the tools.

Run:

```text
ov ls
ov health
```

The integration is ready when `ov ls` returns OpenViking directories and `ov health` returns service status.

## Troubleshooting

| Problem | Fix |
|---|---|
| `401 Unauthorized` | Check the API Key and the `Bearer` prefix. |
| Network timeout | Confirm the network can reach the OpenViking API domain. |
| Tool schema is incompatible with the current model | Switch models or upgrade TRAE to the latest version. |
