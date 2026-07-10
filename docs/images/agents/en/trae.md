## Install the TRAE Integration

The shared installer configures native lifecycle Hooks and the OpenViking MCP server together. One command completes the setup.

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos

# Both
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae,trae-cn --dist tos
```

## Verify

1. Restart TRAE after installation.
2. Confirm `SessionStart`, `UserPromptSubmit`, and `Stop` in `~/.trae/hooks.json` or `~/.trae-cn/hooks.json`.
3. Confirm the `openviking` MCP server is enabled in TRAE settings.
4. Start a new chat and submit a prompt that should recall prior context.

See the complete [TRAE integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/13-trae.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Hooks or MCP are missing | Re-run the shared installer for the correct `trae` or `trae-cn` harness. |
| Recall/capture runs twice | Remove the old OpenViking Hook entries, then re-run the installer. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart TRAE. |
