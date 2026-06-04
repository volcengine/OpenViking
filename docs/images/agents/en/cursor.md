## 1. Cursor Integration Steps for OpenViking

Follow the steps below in Cursor to connect OpenViking:

### Step 1: Open Cursor settings

In the Cursor main window, click **Settings** in the upper-right corner to open the settings panel.

![Open Cursor settings](https://docs.openviking.net/agents/image/cursor/02-open-settings.png)

### Step 2: Add an MCP Server

In the left menu, select **Tools & MCPs** to open the MCP Servers page.

![Open Tools and MCPs](https://docs.openviking.net/agents/image/cursor/03-tools-and-mcps.png)

Click **Add Custom MCP**.

![Add custom MCP server](https://docs.openviking.net/agents/image/cursor/04-add-custom-mcp.png)

### Step 3: Paste the JSON configuration

In the opened **mcp.json** file, paste the following JSON. The `Authorization` and other fields are already filled with the API Key value:

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

### Step 4: Confirm and enable

After saving and closing `mcp.json`, Cursor automatically connects to the MCP server and loads the tools. When the connection succeeds, **`ov-mcp-server`** appears in the **Installed MCP Servers** list with the enabled tool count, such as "10 tools enabled". The switch next to `ov-mcp-server` should be green, which means the service is loaded and ready.

![Confirm and enable MCP Server](https://docs.openviking.net/agents/image/cursor/06-enable-server.png)

### Step 5: Check MCP connectivity

After connecting, it is recommended to run two simple queries to verify the MCP server:

**1.** **`ov ls`** - List the OpenViking root directories to confirm the connection is available and the directory structure is returned correctly:

```bash
ov ls
```

![Run ov ls](https://docs.openviking.net/agents/image/cursor/07-ov-ls.png)

**2.** **`ov health`** - Call the health tool to confirm the OpenViking service status and current identity:

```bash
ov health
```

![Run ov health](https://docs.openviking.net/agents/image/cursor/08-ov-health.png)

### Step 6: Acceptance criteria

`ov ls` returns directories such as `agent / resources / session / user`; `ov health` returns `service initialized` and the current username. This means the integration succeeded.

## 2. Configuration fields

| Field | Required | Description |
|---|---|---|
| `mcpServers` | Yes | Root node for MCP server configuration |
| `ov-mcp-server` | Yes | Service alias. It can be customized, but keeping this name helps contextual recognition |
| `url` | Yes | OpenViking MCP endpoint. For CN, use `https://api.vikingdb.cn-beijing.volces.com/openviking/mcp` |
| `headers.Authorization` | Yes | Format: `Bearer <API Key>`. Filled in during the steps above |

## 3. FAQ

| Problem | Suggested fix |
|---|---|
| Connection failed / 401 Unauthorized | Check that `Authorization` includes the `Bearer` prefix and that the API Key is valid |
| Connection failed / network timeout | Confirm the network can reach `api.vikingdb.cn-beijing.volces.com`; add an allowlist entry for corporate networks if needed |
| Agent cannot see tools | Confirm the MCP server is enabled. Some clients need a process restart before loading new config |
