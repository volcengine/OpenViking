# Cursor Memory Integration

Give Cursor long-term memory across projects and sessions. After installation, OpenViking Hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and preferably the latest stable Cursor release. The installer guides you through the OpenViking connection settings.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** only when an OpenViking server is running locally.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

If GitHub is unavailable, use the TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

Quit Cursor completely and restart it after installation.

## What gets installed

- Lifecycle Hooks for profile loading, prompt recall, conversation capture, session commit, and `viking://` URI protection.
- The OpenViking MCP server with tools such as `search`, `recall`, `read`, and `remember`.
- An always-on Rule and memory Skill that tell the Agent how to use injected context and memory tools.

## Verify

1. Restart Cursor and create a new Agent session.
2. Open **Cursor Settings → Hooks** and confirm that the OpenViking lifecycle Hooks execute `cursor-hook.mjs` and its URI protection Hooks execute `uri-guard.mjs`.
3. Check that the `beforeSubmitPrompt` output contains `additional_context`. This confirms that recall reaches the Agent without requiring an MCP call first.
4. Open **Cursor Settings → Tools & MCPs** and confirm that `openviking` is connected.
5. Tell Cursor a temporary preference, wait for the response to finish, then create a new session and ask for that preference to verify capture and cross-session recall.

## How it works

- `sessionStart` loads your profile and the current project's memory index.
- `beforeSubmitPrompt` recalls context for the current request and injects it through `additional_context`.
- `beforeReadFile` and `beforeShellExecution` redirect accidental local access to `viking://` paths back to OpenViking MCP tools.
- `stop` incrementally captures new user and assistant messages.
- `preCompact` and `sessionEnd` commit pending messages for memory extraction.

Project identity uses Cursor's `workspace_roots`, keeping workspace peers separate. Hooks and MCP share credentials from `~/.openviking/ovcli.conf`.

## Upgrade and uninstall

Re-run the install command from the same distribution channel to upgrade. Use the same channel for uninstall:

```bash
# GitHub
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes

# TOS
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

Uninstall removes only OpenViking-managed Cursor Hooks, MCP, Rule, Skill, and runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Hooks do not run | Quit Cursor completely, restart it, and create a new Agent session. |
| Recall appears in Hook output but not in the answer | Upgrade to the latest stable Cursor; older releases may not support `beforeSubmitPrompt.additional_context`. |
| The same event runs multiple OpenViking Hooks | Cursor may be importing an older Claude Code plugin. Upgrade or remove the legacy OpenViking plugin ids reported by the installer, then restart Cursor. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart Cursor. |
| Detailed diagnostics are needed | Start Cursor with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/cursor-hooks.log`. |

## See also

- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
