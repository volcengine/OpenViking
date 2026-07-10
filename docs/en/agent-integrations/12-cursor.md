# Cursor Memory Plugin

Give Cursor cross-project and cross-session long-term memory. Install the OpenViking Cursor Plugin once; it automatically recalls relevant memories, captures new turns, and exposes explicit memory tools without any separate MCP setup.

Source: [examples/cursor-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/cursor-memory-plugin)

## Install

Install `openviking-memory` from Cursor's Plugins/Customize page. After public Marketplace publication, you can also run this command in Cursor Agent:

```text
/add-plugin openviking-memory
```

The Plugin installs its Hook, MCP server, Rule, and Skill together. No additional MCP configuration is required.

Before the Plugin is available in your Cursor Marketplace, use the shared installer. It installs the complete compatibility runtime and is safe to re-run:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

In regions where GitHub is hard to reach, use the same installer from the Volcengine TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

The compatibility installer writes both the Hook and MCP configuration. Once the Marketplace Plugin is installed, remove the compatibility fallback instead of enabling both.

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

For the plugin path, confirm that `openviking-memory` is enabled in Cursor's Plugins/Customize page, then confirm its OpenViking Hook and MCP server are active. Plugin-managed configuration does not need to appear in user-level JSON files.

For the direct fallback path:

1. Check `~/.cursor/hooks.json` for `cursor-hook.mjs`.
2. Check `~/.cursor/mcp.json` for the `openviking` server.
3. Start a new Agent chat, make one tool-using request, and inspect `~/.openviking/logs/cursor-hooks.log` with `OPENVIKING_DEBUG=1`.

Hook state is isolated under `~/.openviking/hook-state/cursor/`; OpenViking session IDs use the `cu-` prefix.

## Upgrade and uninstall

Upgrade or uninstall a marketplace plugin from Cursor's Plugin/Customize page. Cursor manages all capabilities bundled by that plugin.

For the direct fallback, re-run the install command to upgrade. To uninstall it:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

The fallback uninstall removes only OpenViking-managed Cursor entries and files.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Two OpenViking MCP servers or duplicate recall | Remove the manual/fallback configuration when the plugin is enabled; use one installation path only. |
| Hook command cannot find Node | Ensure `node` is available in Cursor's process PATH, then restart Cursor. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf`; hooks and MCP read the same active config. |

## See also

- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
