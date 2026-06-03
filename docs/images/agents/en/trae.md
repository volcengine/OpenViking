## 1. Use cases

Use OpenViking to:

- Remember technology stack preferences across sessions, including language versions, frameworks, package managers, and build systems

- Preserve coding style preferences, such as naming conventions, comment style, whether to write unit tests, and TDD/BDD habits

- Remember common project context, such as monorepo structure, build commands, deployment workflows, and environment differences

- Store historical decisions and troubleshooting notes, such as why option X was avoided or what issue happened last time with option Y

- Persist long-term personal goals, OKRs, and roadmap context so the agent can align with them while planning work

## 2. Trae Integration Steps for OpenViking

**Trae** is an AI IDE from ByteDance that natively supports loading external tools and context services through MCP. Follow the steps below in Trae.

### Step 1: Open Trae settings

In the Trae main window, click **Settings** (gear icon) in the upper-right corner to open the settings panel.

![Open Trae settings](https://docs.openviking.net/agents/image/trae/02-open-settings.jpg)

### Step 2: Open the MCP configuration page

In the left menu, select **MCP** to open the MCP Servers management page.

![Open the MCP configuration page](https://docs.openviking.net/agents/image/trae/03-mcp-settings.jpg)

### Step 3: Add an MCP Server

Click the **+ Add** button on the right, then choose **Manual configuration** from the dropdown menu.

![Add MCP Server](https://docs.openviking.net/agents/image/trae/04-add-mcp-server.jpg)

![Choose manual configuration](https://docs.openviking.net/agents/image/trae/05-manual-config.png)

### Step 4: Paste the JSON configuration

Paste the following JSON into the configuration dialog:

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

![Paste MCP JSON configuration](https://docs.openviking.net/agents/image/trae/06-paste-mcp-json.jpg)

### Step 5: Confirm and enable

Click **Confirm**. Trae automatically establishes the MCP connection and loads the tool list. When the connection succeeds, `ov-mcp-server` appears in the configured MCP Servers list. After setup, you can see that `ov-mcp-server` is loaded and enabled on the MCP management page, with the switch on the right shown in green.

![Confirm and enable MCP Server](https://docs.openviking.net/agents/image/trae/07-enable-server.jpg)

### Step 6: Check MCP connectivity

After connecting, it is recommended to run two simple queries in Trae to verify that MCP is working correctly:

**1.** **`ov ls`** - List the OpenViking root directory contents to confirm the connection is available and the directory structure is returned correctly:

```bash
ov ls
```

![Run ov ls](https://docs.openviking.net/agents/image/trae/08-ov-ls.jpg)

**2.** **`ov health`** - Call the health tool to confirm the OpenViking server status and current user identity:

```bash
ov health
```

![Run ov health](https://docs.openviking.net/agents/image/trae/09-ov-health.jpg)

**Acceptance criteria**: `ov ls` returns directories such as `agent / resources / session / user`; `ov health` returns `service initialized` and the current username, which means the integration succeeded.

## 3. Configuration fields

| Field | Required | Description |
|---|---|---|
| `mcpServers` | Yes | Root node for MCP Server configuration |
| `ov-mcp-server` | Yes | Service alias. It can be customized, but keeping this name is recommended for contextual recognition |
| `url` | Yes | OpenViking MCP endpoint. For CN, use `https://api.vikingdb.cn-beijing.volces.com/openviking/mcp` |
| `headers.Authorization` | Yes | Format: `Bearer <API Key>`. Source: see chapter 2 |

---

## 4. FAQ

| Problem | Suggested fix |
|---|---|
| Connection failed / 401 Unauthorized | Check that `Authorization` includes the `Bearer` prefix and confirm the API Key has not expired or been reset |
| Connection failed / network timeout | Confirm the network can reach `api.vikingdb.cn-beijing.volces.com`; for corporate networks, add the domain to the proxy allowlist |
| Agent cannot recognize tools | Confirm the MCP Server is enabled; some clients need a process restart before loading the new configuration |
| MCP tools are incompatible with the current model because of the argument schema; switch or fix the MCP server, or switch the model (4027) | ![Trae MCP schema compatibility error](https://docs.openviking.net/agents/image/trae/10-schema-error.png)<br>![Trae MCP schema compatibility detail](https://docs.openviking.net/agents/image/trae/11-schema-error-detail.png)<br>Try switching models or upgrading Trae to the latest version |
