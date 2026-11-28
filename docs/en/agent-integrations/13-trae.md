# TRAE

Give TRAE, TRAE CN, and TraeCode CLI 2.0 long-term memory across projects and sessions. OpenViking hooks load relevant context and capture each conversation turn; MCP remains available for explicit memory search, reading, and management.

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) (TRAE and TRAE CN files under [hosts/trae](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/trae))

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a TRAE/TRAE CN release that supports the `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`, and `PostCompact` hooks.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`). For a remote self-hosted server, select **Custom URL** and enter its URL. If a connection is already saved, this option is named **Custom URL / keep current**.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

When the installer asks which harnesses to install, select **TRAE**, **TRAE CN**, **TraeCode CLI 2.0**, or several of them. Quit and restart the corresponding client after installation. TRAE and TRAE CN use the installed `hooks.json` and need no hook approval.

**TraeCode CLI 2.0** installs the Codex plugin. Starting `trae-cli` shows a hook review prompt: pick **Trust all and continue**, and confirm `openviking-memory` is enabled in `/plugins`. It behaves as described on the [Codex page](./04-codex.md), including [hook trust](./04-codex.md#first-launch-trust-the-hooks).

Once installed, start a new TRAE session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Verify

1. Restart the client and create a new Agent session.
2. Confirm that `openviking` is connected in the client's MCP settings.
3. Ask about an existing project or preference and confirm that the answer uses stored memory.
4. For TraeCode CLI 2.0, run `trae-cli plugin list` to confirm that `openviking-memory` is enabled, then run `/hooks` in the session and confirm the OpenViking entries are trusted and switched on.

For hook diagnostics, start the client with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/trae-hooks.log` (TRAE), `~/.openviking/logs/trae-cn-hooks.log` (TRAE CN) or `~/.openviking/logs/codex-hooks.log` (TraeCode CLI 2.0).

## What is different on TRAE and TRAE CN

- **No session end and no pre-compaction event.** The plugin does not commit the last stretch of a session, and does not commit before a compaction. Enable `memory.session_auto_commit.enabled` on the server so the idle commit covers it. See [What happens at exit](./16-capability-reference.md#what-happens-at-exit).
- **After a compaction**, `PostCompact` makes the next prompt carry a short note naming the session's OpenViking id and the `session_history` MCP tool; the Agent passes that id as the tool's `session`.
- **Capture reads the Stop event** (prompt and last assistant message), not a transcript.
- **`viking://` guard:** `PreToolUse` denies `Read`, `Glob`, and `Grep` calls on a `viking://` path and points to the OpenViking MCP tools; a `Bash` or `RunCommand` command that carries a `viking://` URI still runs, with a notice.
- **Skills** `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, and `ov-memory-doctor` are installed into `~/.trae/skills/<name>` (TRAE CN: `~/.trae-cn/skills/<name>`). A skill you edited, or an unmanaged directory of the same name, is preserved on upgrade. TraeCode CLI also reads `~/.trae/skills`, so with both the TRAE integration and the TraeCode CLI 2.0 plugin installed, the CLI lists each of these skills twice.
- **No subagent capture.**

Everything else is in the [Capability Reference](./16-capability-reference.md).

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall TRAE CN:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness trae-cn
```

Replace `trae-cn` with `trae` for TRAE. Uninstall removes the OpenViking-managed hooks, MCP configuration, and skills; a skill you edited and an unmanaged directory of the same name are preserved. For TraeCode CLI 2.0, use `trae-cli plugin uninstall openviking-memory@openviking`.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Automatic recall does not run | Quit the client completely, restart it, and create a new Agent session. |
| TraeCode CLI 2.0 has the plugin, but nothing is recalled or captured | Trust and enable the OpenViking entries in `/hooks`, and confirm `openviking-memory` is enabled in `/plugins`; both have to be on. An update that changes a hook asks for trust again. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart the client. |
| The same content is captured more than once | Check user and project hooks for older `trae-auto-recall.mjs` or `trae-auto-capture.mjs` entries. Re-running the installer removes OpenViking-managed legacy entries. |
| TraeCode CLI 2.0 does not list the plugin | Run `trae-cli plugin list`; if `openviking-memory` is absent, rerun the installer and select **TraeCode CLI 2.0**. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
