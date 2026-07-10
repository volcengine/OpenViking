# Cursor Memory Plugin

Give Cursor cross-project and cross-session long-term memory. Install the OpenViking Cursor Plugin once; it automatically recalls relevant memories, captures new turns, and exposes explicit memory tools without any separate MCP setup.

Source: [examples/cursor-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/cursor-memory-plugin)

## Install

Install the complete Cursor Plugin with one command. The installer is idempotent and configures its Hook, MCP server, Rule, and Skill together:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

In regions where GitHub is hard to reach, use the same installer from the Volcengine TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

No additional Cursor or MCP configuration is required. Restart Cursor after installation.

## How it works

The Cursor Plugin is the installable package; its runtime capabilities are bundled inside it:

```text
OpenViking Cursor Plugin
├── Hooks        automatic recall and capture
├── MCP server   explicit OpenViking tools
├── Rule         always-on usage guidance
└── Skill        memory operation guidance
```

| Event | Behavior |
|-------|----------|
| `sessionStart` | Replays failed writes and injects profile/project context. |
| `beforeSubmitPrompt` | Prefetches memories relevant to the prompt into local hook state. |
| First `postToolUse` | Injects the prefetched context through `additional_context`. |
| `stop` | Reads new user/assistant turns from Cursor's `transcript_path`. |
| `preCompact` / `sessionEnd` | Captures remaining turns and commits the OpenViking session. |

Cursor's currently documented `beforeSubmitPrompt` output can allow or block submission but does not provide a stable direct context-injection field. Therefore prompt-specific recall is injected after the first tool result. A no-tool answer receives the baseline `sessionStart` context; the always-on plugin rule tells Cursor to use the recall/search MCP tools when exact history is needed.

## Verify

1. Check `~/.cursor/hooks.json` for `cursor-hook.mjs`.
2. Check `~/.cursor/mcp.json` for the `openviking` server.
3. Check `~/.cursor/rules/openviking-memory.mdc` and `~/.cursor/skills/openviking-memory/SKILL.md`.
4. Start a new Agent chat, make one tool-using request, and inspect `~/.openviking/logs/cursor-hooks.log` with `OPENVIKING_DEBUG=1`.

Hook state is isolated under `~/.openviking/hook-state/cursor/`; OpenViking session IDs use the `cu-` prefix.

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall the Plugin:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

The uninstall command removes only OpenViking-managed Cursor entries and files.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Two OpenViking MCP servers or duplicate recall | Re-run the installer to migrate old OpenViking entries, then restart Cursor. |
| Hook command cannot find Node | Ensure `node` is available in Cursor's process PATH, then restart Cursor. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf`; hooks and MCP read the same active config. |

## See also

- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
