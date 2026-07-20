## Install the TRAE Integration

Requires macOS/Linux and Node.js 18+. Run the command for your client; Hooks and MCP are configured together:

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos

# Both
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae,trae-cn --dist tos
```

When asked where to connect, select **Volcengine OpenViking Cloud** and enter your API key. Choose **Self-hosted / local** only for a locally running OpenViking server.

## Verify

1. Restart TRAE after installation.
2. Confirm that `openviking` is connected in TRAE settings.
3. Start a new session and ask about a previous project or preference.
4. Share a temporary preference and ask for it in the next session to verify capture and commit.

See the complete [TRAE integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/13-trae.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Automatic recall does not run after installation | Quit TRAE completely, restart it, and create a new Agent session. |
| A new session cannot recall the previous turn | Check `~/.openviking/logs/trae-hooks.log` or `trae-cn-hooks.log` and confirm that Stop committed successfully. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart TRAE. |
