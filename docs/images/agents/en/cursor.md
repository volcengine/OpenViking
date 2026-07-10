## Install the Cursor integration

One command installs lifecycle Hooks, the OpenViking MCP server, an always-on Rule, and a memory Skill. It does not rely on Cursor Marketplace or require manual MCP setup:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

## Verify

1. Confirm `cursor-hook.mjs` exists in `~/.cursor/hooks.json`.
2. Confirm the `openviking` server exists in `~/.cursor/mcp.json`.
3. Confirm `~/.cursor/rules/openviking-memory.mdc` and `~/.cursor/skills/openviking-memory/SKILL.md` exist.
4. Restart Cursor and start a new Agent chat.

See the complete [Cursor integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/12-cursor.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Two OpenViking MCP servers or duplicate recall | Re-run the installer to migrate old OpenViking entries, then restart Cursor. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart Cursor. |
| Hook cannot find Node.js | Ensure `node` is available in Cursor's process `PATH`. |
