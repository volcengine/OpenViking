# Trae MCP Integration

# 1. Use cases

Use OpenViking to:

- Remember technology stack preferences across sessions, including language versions, frameworks, package managers, and build systems.

- Persist coding style preferences, including naming conventions, comment style, whether to write tests, and TDD/BDD habits.

- Remember common project context, such as monorepo structure, build commands, deployment flow, and environment differences.

- Store historical decisions and troubleshooting notes, such as why option X was avoided or what failed last time with option Y.

- Persist long-term personal goals, OKRs, or roadmaps so the agent can align with them while planning tasks.

---

# 2. Prerequisite: Get an API Key

All MCP clients use the same **Authorization Token**, which is the API Key from the OpenViking console. Get it first and keep it secure.

## 2.1 Where to find it

1. In the left menu, choose **User Management**.

2. Find the target user in the user list. For personal editions, the default user is usually `default` / `admin`. Click the **copy** icon in the API Key column.

3. Save the copied `ZGV...hiMg` string. It will be used as the `Authorization` value for agent integration.

**Security note**: The API Key is equivalent to an account secret. Do not commit it to Git or publish it anywhere. Prefer environment variables or encrypted configuration.

---

# 3. Trae Integration Guide

**Trae** is an AI IDE from ByteDance. It natively supports loading external tools and context services through MCP. This is the standard OpenViking integration flow.

## 3.1 Integration steps

### Step 1 - Open settings

In the Trae main window, click **Settings** in the upper-right corner to open the settings panel.

### Step 2 - Open the MCP configuration page

In the left menu, select **MCP** to open the MCP Servers page.

### Step 3 - Add an MCP Server

Click the **+ Add** button on the right, then choose **Manual configuration** from the dropdown.

### Step 4 - Paste the JSON configuration

In the configuration dialog, paste the following JSON and replace `Authorization` with the API Key copied in section 2:

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

### Step 5 - Confirm and enable

Click **Confirm**. Trae automatically connects to the MCP server and loads the tools. When the connection succeeds, `ov-mcp-server` appears in the configured MCP Servers list. The switch on the right should be green, which means the server is loaded and enabled.

### Step 6 - Check MCP connectivity

After connecting, run two simple queries in Trae to verify the MCP server:

**1.** **`ov ls`** - List OpenViking root directories and confirm the connection returns the expected structure.

**2.** **`ov health`** - Call the health tool to confirm server status and current identity.

**Acceptance criteria**: `ov ls` returns directories such as `agent / resources / session / user`; `ov health` returns `service initialized` and the current username.

## 3.2 Configuration fields

| Field | Required | Description |
|---|---|---|
| `mcpServers` | Yes | Root node for MCP server configuration |
| `ov-mcp-server` | Yes | Service alias. It can be customized, but keeping this name helps contextual recognition |
| `url` | Yes | OpenViking MCP endpoint. For CN, use `https://api.vikingdb.cn-beijing.volces.com/openviking/mcp` |
| `headers.Authorization` | Yes | Format: `Bearer <API Key>`. Source: section 2 |

---

# 4. FAQ

| Problem | Suggested fix |
|---|---|
| Connection failed / 401 Unauthorized | Check that `Authorization` includes the `Bearer` prefix and that the API Key is valid |
| Connection failed / network timeout | Confirm the network can reach `api.vikingdb.cn-beijing.volces.com`; add an allowlist entry for corporate networks if needed |
| Agent cannot see tools | Confirm the MCP server is enabled. Some clients need a process restart before loading new config |
| MCP tool reports argument schema incompatibility with the current model, or asks you to switch/fix the MCP server or model (4027) | Try switching models or upgrading Trae to the latest version |
