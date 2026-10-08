# Qoder CLI

Give Qoder CLI long-term memory across projects and sessions. OpenViking Hooks load profile context at session start, recall relevant memory before each prompt, and capture the completed conversation turn on `Stop`. The OpenViking MCP tools remain available for explicit search, reading, and memory management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, Qoder CLI, and a running OpenViking server.

```bash
curl -fsSL https://openviking.ai/install | bash
# Select Qoder CLI when the installer asks which tools to configure.
```

To install only this integration without the selection menu, add `--harness qoder` to the installer command. Restart Qoder CLI after installation.

## What gets installed

The installer preserves unrelated settings and writes these entries under `${QODER_CONFIG_DIR:-~/.qoder}`:

- `settings.json`: `SessionStart`, `UserPromptSubmit`, and `Stop` Hooks, plus the `openviking` MCP server.
- `skills/`: `openviking-memory`, `openviking-skills`, and `ov-experience-memory`.
- `~/.openviking/agent-integrations/qoder/`: the shared Hook runtime and MCP proxy.

If `mcpServers.openviking` already exists and is not managed by this installer, installation stops instead of replacing it.

## Verify

1. Restart Qoder CLI and create a new session.
2. Confirm that `settings.json` contains the three OpenViking Hook entries and `mcpServers.openviking`.
3. Confirm that the OpenViking MCP tools are available.
4. Tell Qoder a test preference and finish the turn. With `OPENVIKING_DEBUG=1`, confirm capture and commit in `~/.openviking/logs/qoder-hooks.log`.
5. After memory extraction completes, start a new session in the same project and ask about the preference.

## How it works

- `SessionStart` injects the user profile, memory index, and available OpenViking skills.
- `UserPromptSubmit` recalls context for the prompt and returns it in `hookSpecificOutput.additionalContext`.
- `Stop` reads new user and assistant messages from Qoder's JSONL transcript, sends them to a `qd-` OpenViking session, and commits when it captured new messages.

Hooks and MCP share the connection and identity settings in `~/.openviking/ovcli.conf`. Set `QODER_CONFIG_DIR` when Qoder uses a non-default configuration directory. This integration does not add a `PreToolUse` Hook.

## Upgrade and uninstall

Re-run the installer to upgrade. To remove only OpenViking-managed Qoder entries and files:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness qoder
```

Other Qoder settings, Hooks, MCP servers, and Skills are preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Hooks do not run | Restart Qoder CLI and start a new session. Then check the Hook commands in `settings.json`. |
| Recall is returned but not used | Confirm that the Hook response contains `hookSpecificOutput.additionalContext` and upgrade Qoder CLI. |
| MCP does not connect | Check the URL and API key in `~/.openviking/ovcli.conf`, then restart Qoder CLI. |
| Installation reports an existing `openviking` server | Rename or remove the foreign `mcpServers.openviking` entry, or keep it and skip this integration's MCP proxy. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Qoder CLI Hooks](https://docs.qoder.com/cli/hooks)
- [Qoder CLI MCP reference](https://docs.qoder.com/cli/mcp-reference.md)
