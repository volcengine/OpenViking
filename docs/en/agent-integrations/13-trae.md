# TRAE and TRAE CN Memory Integration

Give TRAE and TRAE CN long-term memory across projects and sessions. OpenViking Hooks automatically load relevant context, capture each conversation turn, and commit it for memory extraction. MCP remains available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a TRAE/TRAE CN release that supports the `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `Stop` Hooks. The installer guides you through the OpenViking connection settings.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** only when an OpenViking server is running locally.

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn

# Both
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn
```

If GitHub is unavailable, use the TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos
```

Quit and restart the corresponding client after installation.

## What gets installed

- `SessionStart` loads your profile and current project memory.
- `UserPromptSubmit` recalls and injects context for the current request.
- `PreToolUse` redirects accidental local access to `viking://` paths back to OpenViking MCP tools.
- `Stop` captures and immediately commits the completed turn, including short sessions.
- The OpenViking MCP server provides explicit tools such as `search`, `recall`, `read`, and `remember`.

## Verify

1. Restart TRAE or TRAE CN and create a new Agent session.
2. Confirm that `openviking` is connected in the client's MCP settings.
3. Ask about an existing project or preference and confirm that the answer uses stored memory.
4. Tell the Agent a temporary preference, wait for the response to finish, then create a new session and ask for it again to verify capture, commit, and cross-session recall.

For Hook diagnostics, start the client with `OPENVIKING_DEBUG=1` and inspect:

- TRAE: `~/.openviking/logs/trae-hooks.log`
- TRAE CN: `~/.openviking/logs/trae-cn-hooks.log`

## Upgrade and uninstall

Re-run the corresponding install command to upgrade. Use the original distribution channel for uninstall:

```bash
# GitHub, TRAE CN example
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes

# TOS, TRAE CN example
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

Replace `trae-cn` with `trae` for TRAE. Uninstall removes only OpenViking-managed configuration and runtime files.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Automatic recall does not run | Quit the client completely, restart it, and create a new Agent session. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart the client. |
| A new session cannot recall the previous turn | Inspect the Hook log and confirm that `Stop` ran without `/commit` connection or authentication errors. |
| The same content is captured more than once | Check user and project Hooks for older `trae-auto-recall.mjs` or `trae-auto-capture.mjs` entries. Re-running the installer removes OpenViking-managed legacy entries. |

## See also

- [Authentication](../guides/04-authentication.md)
