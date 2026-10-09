# Grok Build

Give Grok Build long-term memory across projects and sessions. OpenViking recalls relevant context for each prompt, captures completed turns, and exposes its MCP tools for explicit memory work.

## Important delivery behavior

Grok Build currently discards stdout from an allowed `UserPromptSubmit` hook. OpenViking therefore recalls at prompt time, caches the result, and delivers it once through the first `PostToolUse` or `PostToolUseFailure` event.

This has one visible limit: automatic recall reaches the model only after the turn's first tool result. A turn that uses no tool receives no automatic recall context. The OpenViking MCP tools remain available from the start of the session.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a Grok Build release with native Hooks, MCP, and Skills support.

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness grok
```

The installer asks for the OpenViking connection settings. Restart Grok Build after installation.

## What gets installed

- Native lifecycle Hooks in `~/.grok/hooks/openviking-memory.json`.
- An `openviking` MCP server in a marked, installer-managed block in `~/.grok/config.toml`.
- The `openviking-memory`, `openviking-skills`, and `ov-experience-memory` Skills under `~/.grok/skills/`.
- The shared runtime under `~/.openviking/agent-integrations/`.

The installer preserves unrelated Hook and TOML configuration. It refuses to replace an existing `mcp_servers.openviking` table that it does not manage.

## How it works

1. `SessionStart` replays retryable writes that an earlier offline session queued.
2. `UserPromptSubmit` recalls context for the new prompt. The first prompt also prepares the profile, memory index, and OpenViking skill catalog. Nothing is written to stdout because Grok would discard it.
3. The first `PostToolUse` or `PostToolUseFailure` consumes the cached block and returns it as `additionalContext`. Later tool results in the same turn emit nothing.
4. `Stop` reads Grok's native `lastAssistantMessage`, pairs it with the cached prompt, stores the turn, and commits it for memory extraction.

Sessions use the `gr-<session id>` prefix. Hooks and MCP share credentials from `~/.openviking/ovcli.conf`.

## Verify

1. Restart Grok Build and start a new session.
2. Run `/hooks` and confirm that the five OpenViking events are enabled: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, and `Stop`.
3. Run `grok mcp list` and confirm that `openviking` is enabled.
4. Ask about a stored preference in a prompt that causes one tool call. Confirm that the first tool result carries an OpenViking context note.
5. Complete a turn, then inspect `~/.openviking/logs/grok-hooks.log` with `OPENVIKING_DEBUG=1` to confirm capture and commit.

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness grok
```

Uninstall removes only the OpenViking Hook file, managed MCP block, installed Skills, and runtime files. Other Grok configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Recall does not appear before any tool call | This is expected. Grok discards allowed `UserPromptSubmit` stdout. The first tool-result event carries the cached context. |
| A no-tool answer does not use automatic recall | This is the current carrier limit. Ask Grok to use an OpenViking MCP search tool when memory is required immediately. |
| The installer refuses the MCP config | `~/.grok/config.toml` already has an unmanaged `mcp_servers.openviking` table. Rename or remove that server before installing. |
| MCP does not connect | Check the URL and API key in `~/.openviking/ovcli.conf`, then restart Grok Build. |
| Capture is missing after an interrupted or failed turn | OpenViking captures `Stop`, which Grok sends only for genuine completion. `StopCancelled` and `StopFailure` are not capture events. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Grok Build Hooks documentation](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md)
