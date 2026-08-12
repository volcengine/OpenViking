DeerFlow can connect to OpenViking through an MCP Server. MCP integration lets DeerFlow agents actively search, read, and use memories and knowledge from OpenViking while executing tasks.

## Step 1: Configure OpenViking credentials

Set the OpenViking MCP endpoint and API key in the environment that starts DeerFlow:

```bash
export OPENVIKING_MCP_URL="https://api.vikingdb.cn-beijing.volces.com/openviking/mcp"
export OPENVIKING_API_KEY="[TODO]your-api-key"
```

If DeerFlow uses a `.env` file, add the same variables there and confirm that the startup command loads it.

## Step 2: Create an MCP configuration file

Create an MCP configuration file in the DeerFlow project, for example `mcp.json`:

```json
{
  "mcpServers": {
    "openviking": {
      "url": "${OPENVIKING_MCP_URL}",
      "headers": {
        "Authorization": "Bearer ${OPENVIKING_API_KEY}"
      }
    }
  }
}
```

If DeerFlow already has a standard MCP config path, merge this server into the existing file.

## Step 3: Configure the OpenViking MCP Server

Enable this MCP Server in the DeerFlow agent or tool configuration, and keep the server name aligned with `openviking` in the MCP config file.

Allow DeerFlow to use OpenViking search, read, browse, and health-check tools to cover common memory retrieval and knowledge lookup scenarios.

## Step 4: Restart DeerFlow

Save the MCP configuration and restart DeerFlow:

```bash
pnpm dev
```

After restart, check the logs and confirm that the `openviking` MCP Server is loaded and its tools are available.

## Step 5: Verify MCP tools

Ask DeerFlow to check the OpenViking service status:

```text
Use the OpenViking MCP tools to check whether the current service is available.
```

You can also ask DeerFlow to search for an existing memory or resource:

```text
Search OpenViking for memories related to the current project.
```

If the agent can call OpenViking MCP tools and return results, MCP integration is working.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP Server is not loaded | Check whether DeerFlow reads the MCP config path and whether the server name is configured consistently |
| Connection fails or times out | Confirm the runtime can access `api.vikingdb.cn-beijing.volces.com`; configure proxy or network allowlists if needed |
| OpenViking returns 401 / 403 | Verify `OPENVIKING_API_KEY` and make sure the request header is `Authorization: Bearer <API Key>` |
| Agent does not call tools | Explicitly allow OpenViking MCP tool usage in the DeerFlow system prompt or tool policy |
| Search results are empty | Confirm OpenViking already has related memories or knowledge resources, and try broader search keywords |
