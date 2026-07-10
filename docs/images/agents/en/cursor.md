## Install the Cursor Plugin

The OpenViking Cursor Plugin bundles lifecycle Hooks, the OpenViking MCP server, an always-on Rule, and a memory Skill. Installing the Plugin is the complete setup; do not add another MCP server manually.

When `openviking-memory` is available in your Cursor Marketplace, install it from the Plugins/Customize page or run:

```text
/add-plugin openviking-memory
```

Before Marketplace publication, use the compatibility installer. It configures both Hooks and MCP automatically:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

## Verify

For the Plugin installation:

1. Confirm `openviking-memory` is enabled in Cursor's Plugins/Customize page.
2. Confirm the Plugin-provided OpenViking Hook and MCP server are active.
3. Start a new Agent chat and make a tool-using request.

For the compatibility installer:

1. Confirm `cursor-hook.mjs` exists in `~/.cursor/hooks.json`.
2. Confirm the `openviking` server exists in `~/.cursor/mcp.json`.
3. Restart Cursor and start a new Agent chat.

See the complete [Cursor integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/12-cursor.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Two OpenViking MCP servers or duplicate recall | Keep only the Plugin or compatibility installer; do not enable both. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart Cursor. |
| Hook cannot find Node.js | Ensure `node` is available in Cursor's process `PATH`. |
