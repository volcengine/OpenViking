# Qoder CLI

Give Qoder CLI long-term memory across projects and sessions. After installation, OpenViking Hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and Qoder CLI with `qodercli` on `PATH` (or the CN build `qoderclicn`). The installer guides you through the OpenViking connection settings; select **Qoder CLI** when it asks which agents to set up.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`); for a remote self-hosted server, select **Custom URL / keep current** and enter its URL.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

Start a new `qodercli` session after installation.

Once installed, start a new Qoder session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

If you installed the Claude Code plugin into Qoder from the OpenViking GitHub marketplace, the installer replaces that `openviking` marketplace, so the two plugins never capture the same session twice.

## What gets installed

The installer assembles a self-contained plugin under `~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/`, with the shared hook runtime beside the adapter, registers it as a local marketplace named `openviking`, and installs it with `qodercli plugins install`. The plugin contains:

- Lifecycle Hooks for profile loading, prompt recall, conversation capture, session commit, and `viking://` URI protection.
- The OpenViking MCP server as a stdio proxy, with tools such as `search`, `read`, `remember`, and `add_skill`.
- The `openviking-memory`, `openviking-skills`, `ov-experience-memory`, and `openviking-rule-search` skills, listed under the plugin's name, for example `openviking-memory:openviking-skills`.

Every Qoder CLI build on `PATH` gets the plugin: `qodercli` in `~/.qoder` (`QODER_CONFIG_DIR`), and the CN build `qoderclicn` in `~/.qoder-cn` (`QODERCN_CONFIG_DIR`). Uninstall removes it from both.

## Verify

1. Run `qodercli plugins list` and confirm that `openviking-memory@openviking` is enabled.
2. Start a new session and confirm that the `openviking` MCP server is connected.
3. Tell Qoder a test preference and end the session normally. After memory extraction completes, start a new session in the same workspace and ask about it.

## How it works

- **SessionStart** injects the user profile and replays anything the offline queue is holding.
- **UserPromptSubmit** recalls relevant memories and injects them as an `<openviking-context>` block in `additionalContext`.
- **PreToolUse** denies reading or writing a `viking://` URI as a local path and points the Agent at the OpenViking MCP tools. Shell commands are not checked, so `ov` invocations and literal `viking://` arguments still work.
- **Stop**, **PreCompact**, and **SessionEnd** capture new transcript turns incrementally in a detached worker. `Stop` commits the OpenViking session every 8 captured messages (`commitTurnThreshold`); `PreCompact` and `SessionEnd` commit everything not yet committed, including messages `Stop` already sent.

Hooks and MCP read the same credentials from `~/.openviking/ovcli.conf`, so they cannot disagree about the server, account, or user. `OPENVIKING_URL` and `OPENVIKING_API_KEY` override that file, and per-plugin settings live under `plugin.qoder` (see [Plugin Settings](../configuration/02-client.md#plugin-settings)). Sessions are stored in OpenViking as `qd-<session id>`.

### Behaviour that follows from the host

`UserPromptSubmit` and `SessionEnd` fire in the TUI and in headless `-p` runs alike, so a headless session is recalled and committed too. Qoder records injected context in its transcript as separate attachment records; capture reads only the conversation records, so recalled memories are never captured back. Every `-p` run also ends an auxiliary session that has no transcript; its `SessionEnd` is ignored.

Qoder loads MCP tools lazily through `mcp_call`. In headless runs with the default permission mode, the MCP tools need a permission rule or a permissive mode. Use the `qodercli` command for the plugin commands: `qoder` also opens the Qoder IDE for some arguments.

Subagent capture is not supported yet: the host fires `SubagentStart`/`SubagentStop`, but the shared dispatcher has no subagent stage. The verified host contract and transcript format are recorded in [`hosts/qoder/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/qoder/DESIGN.md).

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall, run:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness qoder
```

Uninstall removes the plugin, its local marketplace, the copy Qoder caches, and the runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| MCP tools ask for permission in `-p` runs | Add a permission rule for the `openviking` MCP server or use a permissive permission mode. |
| The plugin is not listed | Re-run the installer and check that `qodercli` is on `PATH`, then start a new session. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then start a new session. |
| Detailed diagnostics are needed | Start Qoder with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/qoder-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Source: examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
- [Qoder CLI plugins](https://docs.qoder.com/cli/plugins)
