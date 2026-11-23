# CodeBuddy Code

Give CodeBuddy Code long-term memory across projects and sessions. After installation, OpenViking Hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and CodeBuddy Code with its `codebuddy` CLI on `PATH`. The installer guides you through the OpenViking connection settings; select **CodeBuddy Code** when it asks which agents to set up.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`); for a remote self-hosted server, select **Custom URL / keep current** and enter its URL.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

Start a new CodeBuddy session after installation; `/reload-plugins` applies it to a running one.

Once installed, start a new CodeBuddy session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## What gets installed

The installer assembles a self-contained plugin under `~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/`, with the shared hook runtime beside the adapter, registers it as a local marketplace named `openviking`, and installs it through the CodeBuddy CLI. The plugin contains:

- Lifecycle Hooks for profile loading, prompt recall, conversation capture, session commit, and `viking://` URI protection.
- The OpenViking MCP server as a stdio proxy, with tools such as `search`, `read`, `remember`, and `add_skill`.
- The `openviking-memory`, `openviking-skills`, `ov-experience-memory`, and `openviking-rule-search` skills.

## Verify

1. Run `codebuddy plugin list` and confirm that `openviking-memory@openviking` is enabled.
2. Start a new interactive session and confirm that the `openviking` MCP server is connected.
3. Tell CodeBuddy a test preference and end the session normally. After memory extraction completes, start a new session in the same workspace and ask about it.

## How it works

- **SessionStart** injects the user profile and replays anything the offline queue is holding.
- **UserPromptSubmit** recalls relevant memories and injects them as an `<openviking-context>` block in `additionalContext`. Host-generated continuation turns are skipped, so a turn the host produced on its own never triggers a search.
- **PreToolUse** denies reading or writing a `viking://` URI as a local path and points the Agent at the OpenViking MCP tools. Shell commands are not checked, so `ov` invocations and literal `viking://` arguments still work.
- **Stop**, **PreCompact**, and **SessionEnd** capture new transcript turns incrementally in a detached worker. `Stop` commits the OpenViking session every 8 captured messages (`commitTurnThreshold`); `PreCompact` and `SessionEnd` commit everything not yet committed, including messages `Stop` already sent.

Hooks and MCP read the same credentials from `~/.openviking/ovcli.conf`, so they cannot disagree about the server, account, or user. `OPENVIKING_URL` and `OPENVIKING_API_KEY` override that file, and per-plugin settings live under `plugin.codebuddy` (see [Plugin Settings](../configuration/02-client.md#plugin-settings)). Sessions are stored in OpenViking as `cb-<session id>`.

### Behaviour that follows from the host

`UserPromptSubmit` and `SessionEnd` fire **only in the interactive TUI**. Headless runs (`-p`, `--input-format stream-json`) never emit them, so a headless session captures on `Stop` but never sends the final commit. `SessionStart` carries no `cwd` and can be delivered twice per session, so the session-start hook is idempotent and falls back to the process working directory. A hook that overruns its timeout is killed, but its child processes are not, which is what lets commits run in a detached worker after the hook process returns.

Subagent capture is not supported yet: the host fires `SubagentStart`/`SubagentStop`, but the shared dispatcher has no subagent stage. The verified host contract and transcript format are recorded in [`hosts/codebuddy/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/codebuddy/DESIGN.md).

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall, run:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness codebuddy
```

Uninstall removes the plugin, its local marketplace, the copy CodeBuddy caches, and the runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| No recall in headless runs | `UserPromptSubmit` fires only in the interactive TUI. Use an interactive session. |
| The plugin is not listed | Re-run the installer and check that `codebuddy` is on `PATH`, then start a new session. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then start a new session. |
| Detailed diagnostics are needed | Start CodeBuddy with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/codebuddy-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Source: examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
