# Cursor MCP Integration

## Step 1: Get an API Key

OpenViking MCP clients use the API Key from the OpenViking console as the authorization token. Copy it before configuring Cursor.

## Step 2: Open Cursor MCP settings

In Cursor, open Settings and go to **Tools & MCPs**.

## Step 3: Add a custom MCP server

Click **Add Custom MCP**, then add the following configuration to `mcp.json`:

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

The `Authorization` value must include the `Bearer` prefix.

## Step 4: Enable and verify

Save `mcp.json`. Cursor should load `ov-mcp-server` and show the enabled tools.

Run these queries in Cursor:

```text
ov ls
ov health
```

`ov ls` should return OpenViking root directories such as `agent`, `resources`, `session`, and `user`. `ov health` should return service status and the current identity.

## Troubleshooting

| Problem | Fix |
|---|---|
| `401 Unauthorized` | Check that `Authorization` includes `Bearer <API Key>` and that the API Key is valid. |
| Network timeout | Confirm the network can reach `api.vikingdb.cn-beijing.volces.com`. |
| Agent cannot see tools | Confirm the MCP server is enabled. Some clients need a restart after config changes. |
