# VS Code

Give VS Code's agent long-term memory across projects and sessions through the [Local hooks](https://code.visualstudio.com/docs/agent-customization/hooks) feature. The OpenViking hook loads your memory profile when a session starts, captures each prompt you submit, and commits it for memory extraction right away, because VS Code declares no session-end hook.

## Scope of the first version

VS Code's hooks are in Preview and several parts of their surface are deliberately narrower than the other integrations. This integration covers the documented Local hook contract only:

- `SessionStart` injects your profile — the one event the [hooks reference](https://code.visualstudio.com/docs/agents/reference/hooks-reference) documents as carrying `additionalContext`.
- `UserPromptSubmit` cannot inject context (it accepts the common output format only), so automatic recall has no channel on this host yet. The hook still records the prompt so it can be captured.
- `Stop` and `PreCompact` capture the submitted prompt and commit. VS Code provides no assistant response on these events, and its transcript file format is declared unstable, so the assistant side is not captured.
- `PreToolUse` denies local file tools a `viking://` URI and points the agent back at the OpenViking MCP tools; a terminal command that carries a `viking://` URI still runs, with a notice pointing at the same tools.
- MCP is not configured by the installer. Add the OpenViking server to your workspace's `.vscode/mcp.json` yourself if you want the MCP tools.

Provider-hosted Copilot, Claude, and Codex sessions in VS Code have separate hook contracts and are out of scope.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a VS Code release with agent hooks enabled (the `chat.useHooks` setting, on by default). The installer guides you through the OpenViking connection settings.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`); for a remote self-hosted server, select **Custom URL / keep current** and enter its URL.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

When the installer asks which harnesses to install, select **VS Code**, or pass it directly:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness vscode --url <server-url> --api-key <api-key>
```

## What gets installed

- `~/.copilot/hooks/openviking.json` — the user-scope Local hook file VS Code reads across Local sessions, with `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`, and `PreCompact` entries. VS Code picks it up on its next window reload; no hook approval flow applies to user-scope Local hook files.
- `~/.openviking/agent-integrations/vscode/` — the hook runtime the hook file points at. The OpenViking connection settings live in `~/.openviking/ovcli.conf`, shared with the other integrations.

## Verify

1. Reload the VS Code window and create a new agent session.
2. Check the OpenViking hook log (below) for a `SessionStart` entry after the first prompt.
3. Tell the agent a temporary preference, and confirm the `Stop` hook logs a capture and a commit.
4. After memory extraction completes, start a new session in the same workspace and ask about it; the profile injected at `SessionStart` should now include it.
5. Ask the agent to read `viking://resources/project/file.md` with its file tool and confirm the call is denied with a pointer at the OpenViking MCP tools.

For Hook diagnostics, start VS Code with `OPENVIKING_DEBUG=1` (for example `OPENVIKING_DEBUG=1 code .`) and inspect `~/.openviking/logs/vscode-hooks.log`.

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness vscode
```

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| No hook log appears | VS Code has not reloaded since the install, or `chat.useHooks` is off. Reload the window and check the setting. |
| Nothing is recalled into the chat | Expected for now: `UserPromptSubmit` on VS Code cannot carry injected context, so recall only reaches the session through the `SessionStart` profile. |
| The answer does not use stored memory | Confirm the previous session's `Stop` hook committed (hook log), and wait for memory extraction to finish before asking in a new session. |
| MCP tools are missing | The installer does not configure MCP for VS Code; add the OpenViking server to `.vscode/mcp.json` as shown in [MCP Clients](./06-mcp-clients.md). |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
