# Cursor MCP Integration

# 1. Prerequisite: Get an API Key

All MCP clients use the same **Authorization Token**, which is the API Key from the OpenViking console. Get it first and keep it secure.

## 1.1 Where to find it

1. In the left menu, choose **User Management**.

2. Find the target user in the user list. For personal editions, the default user is usually `default` / `admin`. Click the **copy** icon in the API Key column.

3. Save the copied `ZGV...hiMg` string. It will be used as the `Authorization` value for agent integration.

**Security note**: The API Key is equivalent to an account secret. Do not commit it to Git or publish it anywhere. Prefer environment variables or encrypted configuration.

![Copy OpenViking API Key](https://docs.openviking.net/agents/image/cursor/01-api-key.jpg)

# 2. Cursor Integration Guide

This is the standard OpenViking flow for connecting Cursor.

## 2.1 Integration steps

### Step 1 - Open settings

In the Cursor main window, click **Settings** in the upper-right corner to open the settings panel.

![Open Cursor settings](https://docs.openviking.net/agents/image/cursor/02-open-settings.png)

### Step 2 - Add an MCP Server

In the left menu, select **Tools & MCPs** to open the MCP Servers page.

![Open Tools and MCPs](https://docs.openviking.net/agents/image/cursor/03-tools-and-mcps.png)

Click **Add Custom MCP**.

![Add custom MCP server](https://docs.openviking.net/agents/image/cursor/04-add-custom-mcp.png)

### Step 3 - Paste the JSON configuration

In the opened **mcp.json** file, paste the following JSON and replace `Authorization` with the API Key copied in section 1:

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

![Paste MCP JSON configuration](https://docs.openviking.net/agents/image/cursor/05-paste-mcp-json.jpg)

### Step 4 - Confirm and enable

After saving and closing `mcp.json`, Cursor automatically connects to the MCP server and loads the tools. When the connection succeeds, **`ov-mcp-server`** appears in the **Installed MCP Servers** list with the enabled tool count, such as "10 tools enabled". The switch next to `ov-mcp-server` should be green, which means the service is loaded and ready.

![Confirm and enable MCP Server](https://docs.openviking.net/agents/image/cursor/06-enable-server.png)

### Step 5 - Check MCP connectivity

After connecting, run two simple queries in Cursor to verify the MCP server:

**1.** **`ov ls`** - List OpenViking root directories and confirm the connection returns the expected structure.

![Run ov ls](https://docs.openviking.net/agents/image/cursor/07-ov-ls.png)

**2.** **`ov health`** - Call the health tool to confirm server status and current identity.

![Run ov health](https://docs.openviking.net/agents/image/cursor/08-ov-health.png)

**Acceptance criteria**: `ov ls` returns directories such as `agent / resources / session / user`; `ov health` returns `service initialized` and the current username.

## 2.2 Configuration fields

| Field | Required | Description |
|---|---|---|
| `mcpServers` | Yes | Root node for MCP server configuration |
| `ov-mcp-server` | Yes | Service alias. It can be customized, but keeping this name helps contextual recognition |
| `url` | Yes | OpenViking MCP endpoint. For CN, use `https://api.vikingdb.cn-beijing.volces.com/openviking/mcp` |
| `headers.Authorization` | Yes | Format: `Bearer <API Key>`. Source: section 1 |

---

# 3. FAQ

| Problem | Suggested fix |
|---|---|
| Connection failed / 401 Unauthorized | Check that `Authorization` includes the `Bearer` prefix and that the API Key is valid |
| Connection failed / network timeout | Confirm the network can reach `api.vikingdb.cn-beijing.volces.com`; add an allowlist entry for corporate networks if needed |
| Agent cannot see tools | Confirm the MCP server is enabled. Some clients need a process restart before loading new config |
