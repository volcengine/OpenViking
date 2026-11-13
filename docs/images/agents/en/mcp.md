Install or update OpenViking MCP for the current Agent, and install the latest official skills.

### Step 1: MCP configuration

Use the following connection configuration:

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

Identify the client and its supported configuration method (settings UI, configuration file, or installation command); do not assume that JSON merging is supported. If an OpenViking MCP server already exists, first ask me to choose between "Keep the existing connection" and "Use the new configuration above", then proceed after I choose. Preserve other MCP services and do not add the same service twice. Use the Key already provided; do not ask for it again, echo it, or include it in command arguments, logs, or rules.

Upgrade existing local MCP components using the official procedure. For remote HTTPS MCP, update the selected connection and re-enable it; do not describe reconnection as an upgrade to the cloud service.

### Step 2: Install the latest skills

Get all the latest skill packages from https://github.com/volcengine/OpenViking/tree/main/agent-plugins/skills and install or update them in the location supported by the client. Preserve all accompanying files. If existing skills have customizations, explain the differences before handling them.

### Step 3: Verify tool functionality

After reloading, confirm that MCP tools are listed, call OpenViking's `health` tool to verify the connection and authentication, then use `list` to check the directories accessible to your account. Confirm that the installed skills are discoverable. `ov health` checks the CLI connection; it does not verify this client's MCP connection.
