# Cursor Memory Integration

Add long-term memory across Cursor projects and sessions. Once installed, Cursor automatically recalls relevant memories, captures new conversations, and provides OpenViking memory tools.

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

If GitHub is unavailable, use the Volcengine TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

Restart Cursor after installation.

## Features

- Loads your profile and project memory when a new session starts.
- Automatically recalls relevant context for the current request.
- Saves new user and assistant messages after each conversation.
- Provides OpenViking tools for searching and managing memory.

## Verify

1. Restart Cursor and start a new Agent session.
2. Open **Cursor Settings → Hooks** and confirm that `cursor-hook.mjs` appears in the Execution Log.
3. Open **Cursor Settings → Tools & MCPs** and confirm that `openviking` is connected.
4. Ask about a previous project or preference and confirm that Cursor can use existing memory.

## Upgrade and uninstall

Re-run the install command to upgrade.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Hooks do not run after installation | Quit Cursor completely, restart it, and create a new Agent session. |
| The Plugins page shows a `Get` button for `openviking-memory` | No action is required. Use the Hooks and Tools & MCPs checks above to verify the installation. |
| Recall runs more than once | Re-run the install command and restart Cursor. |
| Connection or authentication fails | Check the server URL and API key in `~/.openviking/ovcli.conf`. |

## See also

- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
