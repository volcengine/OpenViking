# Cursor

Give Cursor long-term memory across projects and sessions. OpenViking hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) (Cursor files under [hosts/cursor](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/cursor))

## Install

Prerequisites: macOS or Linux, Node.js 18+, and preferably the latest stable Cursor release.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`). For a remote self-hosted server, select **Custom URL** and enter its URL. If a connection is already saved, this option is named **Custom URL / keep current**.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

Quit Cursor completely and restart it after installation. The installer adds the hooks, the `openviking` MCP server, an always-on Rule, and the skills `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search` and `ov-memory-doctor`.

Once installed, start a new Cursor session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Verify

1. Restart Cursor and create a new Agent session.
2. Open **Cursor Settings → Hooks** and confirm that the OpenViking hooks run `scripts/hook.mjs` and the URI protection hook runs `scripts/uri-guard.mjs`.
3. Open **Cursor Settings → Tools & MCPs** and confirm that `openviking` is connected.
4. Ask about an existing project or preference and confirm the answer uses stored memory. For diagnostics, start Cursor with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/cursor-hooks.log`.

## What is different on Cursor

- **No usable session end.** Cursor registers `sessionEnd`, but it fires only on window close after Cursor has stopped running hook commands, so it does not commit in practice. The plugin does not commit the last stretch of a session. Enable `memory.session_auto_commit.enabled` on the server so the idle commit covers it. See [What happens at exit](./16-capability-reference.md#what-happens-at-exit).
- **Tool results are captured with the turn.** Cursor's own tools report through `postToolUse` / `postToolUseFailure`, and MCP calls through `afterMCPExecution`; each result is captured as a tool part with its call ID, input, output and success or failure. A result without a call ID or tool name is skipped.
- **`viking://` guard:** `beforeReadFile` denies reading a `viking://` path as a local file and points the Agent to the OpenViking MCP tools.
- **`session_history`:** `preToolUse` matches only `MCP:session_history` and fills in the current conversation as its `session` when the Agent leaves it out.
- **No subagent capture.**

Everything else is in the [Capability Reference](./16-capability-reference.md).

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall, run:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness cursor
```

Uninstall removes only OpenViking-managed Cursor hooks, MCP, Rule, skills, and runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Hooks do not run | Quit Cursor completely, restart it, and create a new Agent session. |
| Recall appears in hook output but not in the answer | Upgrade to the latest stable Cursor; older releases may not support `beforeSubmitPrompt.additional_context`. |
| The same event runs multiple OpenViking hooks | Cursor may be importing an older Claude Code plugin. Remove the legacy OpenViking plugin ids reported by the installer, then restart Cursor. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart Cursor. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
