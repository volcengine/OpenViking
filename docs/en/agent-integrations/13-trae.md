# TRAE, TRAE CN, and TraeCode CLI 2.0 Memory Integration

Give TRAE, TRAE CN, and TraeCode CLI 2.0 long-term memory across projects and sessions. OpenViking Hooks automatically load relevant context, capture each conversation turn, and commit it for memory extraction. MCP remains available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a TRAE/TRAE CN release that supports the `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `Stop` Hooks. TraeCode CLI 2.0 uses the Codex-compatible plugin format directly. The installer guides you through the OpenViking connection settings.

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

# TraeCode CLI 2.0
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cli
```

If GitHub is unavailable, use the TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos

# TraeCode CLI 2.0
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae-cli --dist tos
```

Quit and restart the corresponding client after installation.

## What gets installed

- `SessionStart` loads your profile and current project memory.
- `UserPromptSubmit` recalls and injects context for the current request.
- `PreToolUse` redirects accidental local access to `viking://` paths back to OpenViking MCP tools.
- `Stop` captures and immediately commits the completed turn, including short sessions.
- The OpenViking MCP server transparently exposes the full server MCP tool set (15 tools): `find`, `search`, `read`, `list`, `tree`, `remember`, `write`, `edit`, `add_resource`, `list_watches`, `cancel_watch`, `grep`, `glob`, `forget`, and `health`. `search` with `mode="context"` returns assembled context.

## Verify

1. Restart TRAE, TRAE CN, or TraeCode CLI 2.0 and create a new Agent session.
2. Confirm that `openviking` is connected in the client's MCP settings.
3. Ask about an existing project or preference and confirm that the answer uses stored memory.
4. Tell the Agent a temporary preference, wait for the response to finish, then create a new session and ask for it again to verify capture, commit, and cross-session recall.
5. For TraeCode CLI 2.0, run `trae-cli plugin list` and confirm that `openviking-memory` is enabled.

For Hook diagnostics, start the client with `OPENVIKING_DEBUG=1` and inspect:

- TRAE: `~/.openviking/logs/trae-hooks.log`
- TRAE CN: `~/.openviking/logs/trae-cn-hooks.log`
- TraeCode CLI 2.0: `~/.openviking/logs/codex-hooks.log`

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

Replace `trae-cn` with `trae` for TRAE. For TraeCode CLI 2.0, use `trae-cli plugin uninstall openviking-memory@openviking`. Running the installer with `--harness trae-cli --uninstall` only removes the deprecated standalone Hooks integration from older installations.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Automatic recall does not run | Quit the client completely, restart it, and create a new Agent session. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart the client. |
| A new session cannot recall the previous turn | Inspect the Hook log and confirm that `Stop` ran without `/commit` connection or authentication errors. |
| The same content is captured more than once | Check user and project Hooks for older `trae-auto-recall.mjs` or `trae-auto-capture.mjs` entries. Re-running the installer removes OpenViking-managed legacy entries. |
| TraeCode CLI 2.0 does not list the plugin | Run `trae-cli plugin list`; if `openviking-memory` is absent, rerun the installer with `--harness trae-cli`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
