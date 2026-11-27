# CodeBuddy Code

Give CodeBuddy Code long-term memory across projects and sessions. After installation, OpenViking Hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and CodeBuddy Code with its `codebuddy` CLI on `PATH`. The installer guides you through the OpenViking connection settings; select **CodeBuddy Code** when it asks which agents to set up.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`). For a remote self-hosted server, select **Custom URL** and enter its URL. If a connection is already saved, this option is named **Custom URL / keep current**.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

Start a new CodeBuddy session after installation; `/reload-plugins` applies it to a running one.

Once installed, start a new CodeBuddy session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## What gets installed

The installer assembles a self-contained plugin under `~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/`, registers it as a local marketplace named `openviking`, and installs it through the CodeBuddy CLI. The plugin contains lifecycle hooks, the OpenViking MCP server as a stdio proxy (tools such as `search`, `read`, `remember`, `add_skill`), and the `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, and `ov-memory-doctor` skills.

Hooks and MCP read the same credentials from `~/.openviking/ovcli.conf`. `OPENVIKING_URL` and `OPENVIKING_API_KEY` override that file, and per-plugin settings live under `plugin.codebuddy` (see [Plugin Settings](../configuration/02-client.md#plugin-settings)). Sessions are stored in OpenViking as `cb-<session id>`.

## Verify

1. Run `codebuddy plugin list` and confirm that `openviking-memory@openviking` is enabled.
2. Start a new interactive session and confirm that the `openviking` MCP server is connected.
3. Tell CodeBuddy a test preference and end the session normally. After memory extraction completes, start a new session in the same workspace and ask about it.

## Host-specific behaviour

CodeBuddy registers `SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`, and `SessionEnd`, plus a `PreToolUse` URI guard on `Read|Write|Edit|Glob|Grep`. Capture, commits, recall, the offline queue, and everything else shared are described in the [Capability Reference](./16-capability-reference.md).

- `SessionEnd` fires **only in the interactive TUI**. A headless session (`-p`, `--input-format stream-json`) is captured at each `Stop` but gets no session-end commit.
- `UserPromptSubmit` fires only in the TUI before CodeBuddy Code 2.185.0; from 2.185.0 it fires in headless `-p` runs too. Without it there is no recall.
- Host-generated continuation turns are not treated as prompts, so they never trigger recall.
- Context added at `SessionStart` is dropped after a compaction or a `--resume`, so the profile and the session-history note are delivered with the next `UserPromptSubmit`.
- Shell commands are not checked by the URI guard: on this host `PreToolUse` can only deny, so only file tools are matched.
- Subagents are not captured.

The verified host contract and transcript format are recorded in [`hosts/codebuddy/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/codebuddy/DESIGN.md).

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall, run:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness codebuddy
```

Uninstall removes the plugin, its local marketplace, the copy CodeBuddy caches, and the runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| No recall in headless runs | Before CodeBuddy Code 2.185.0, `UserPromptSubmit` fires only in the interactive TUI. Upgrade or use an interactive session. |
| The plugin is not listed | Re-run the installer and check that `codebuddy` is on `PATH`, then start a new session. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then start a new session. |
| Detailed diagnostics are needed | Start CodeBuddy with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/codebuddy-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Source: examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
