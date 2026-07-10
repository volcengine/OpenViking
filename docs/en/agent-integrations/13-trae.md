# TRAE and TRAE CN Memory Integration

Give TRAE and TRAE CN cross-project and cross-session long-term memory. Run one installer; automatic prompt recall, turn capture, and explicit OpenViking tools are configured together.

Source: [examples/trae-memory-hooks](https://github.com/volcengine/OpenViking/tree/main/examples/trae-memory-hooks)

## Install

Install either or both variants:

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae-cn

# Both
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae,trae-cn
```

For the TOS mirror, replace the URL and add `--dist tos`:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos
```

The installer configures the complete integration, including native Hooks and the OpenViking MCP server. No additional setup is required.

## How it works

| Event | Behavior |
|-------|----------|
| `SessionStart` | Replays pending writes and injects profile/project context. |
| `UserPromptSubmit` | Searches OpenViking and injects relevant memory before the model runs. |
| `Stop` | Captures `prompt` plus `last_assistant_message` or `text_content`. |

TRAE and TRAE CN use separate state/log directories and `tr-` / `trcn-` session prefixes. A turn threshold commits long-running sessions; failed writes use the shared pending queue and replay on the next `SessionStart`.

## Configuration paths

| Client | Hooks | MCP on macOS | Portable MCP fallback |
|--------|-------|--------------|-----------------------|
| TRAE | `~/.trae/hooks.json` | `~/Library/Application Support/Trae/User/mcp.json` | `~/.trae/mcp.json` |
| TRAE CN | `~/.trae-cn/hooks.json` | `~/Library/Application Support/Trae CN/User/mcp.json` | `~/.trae-cn/mcp.json` |

The installer merges only OpenViking entries and preserves other hooks and MCP servers.

## Verify

1. Restart TRAE after installation.
2. Confirm `SessionStart`, `UserPromptSubmit`, and `Stop` in the relevant `hooks.json`.
3. Confirm the `openviking` MCP server in `mcp.json`.
4. With `OPENVIKING_DEBUG=1`, inspect `~/.openviking/logs/trae-hooks.log` or `trae-cn-hooks.log`.

## Upgrade and uninstall

Re-run the installation command to upgrade. To remove one variant:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Stop fires but no session is stored | Confirm the dedicated TRAE adapter is installed; Claude's transcript parser is incompatible. |
| Memory appears under the wrong client | Check the hook command's final argument (`trae` or `trae-cn`). |
| Recall/capture runs twice | Remove older OpenViking hook entries and re-run the installer; the installer keeps one managed entry per event. |
| MCP works but recall is not automatic | Verify `UserPromptSubmit`; MCP alone remains model-invoked. |
