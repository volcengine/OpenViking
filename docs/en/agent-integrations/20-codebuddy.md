# CodeBuddy Code

Give CodeBuddy Code long-term memory across projects and sessions. After installation, OpenViking Hooks load your profile at session start, recall relevant context before each prompt, and capture new conversation turns as the session goes. The OpenViking MCP server is registered as well, for explicit memory search, reading, and management.

**CodeBuddy is not covered by the unified installer.** Its plugin system is configured per plugin rather than through a host config file, so the plugin is installed from this repository's marketplace instead.

Install the plugin from the marketplace in this repository:

```bash
codebuddy plugin marketplace add volcengine/OpenViking
codebuddy plugin install openviking-memory@openviking --scope user
```

If you are working from a local clone instead, point the marketplace at the `examples/` directory of the checkout:

```bash
git clone --depth 1 https://github.com/volcengine/OpenViking.git
codebuddy plugin marketplace add ./OpenViking/examples
codebuddy plugin install openviking-memory@openviking --scope user
```

Prerequisites: CodeBuddy Code, Node.js 18+, and a running OpenViking server. The plugin reads its connection from `~/.openviking/ovcli.conf`:

```json
{ "url": "http://127.0.0.1:1933", "api_key": "<key>" }
```

`OPENVIKING_URL` and `OPENVIKING_API_KEY` override that file. Per-plugin settings live in the same file under `plugin.codebuddy` (see [Plugin Settings](../configuration/02-client.md#plugin-settings)). Start a new CodeBuddy session after installing.

## What gets installed

- Lifecycle Hooks for profile loading, prompt recall, conversation capture, session and compaction commits, subagent capture, and `viking://` URI protection.
- The OpenViking MCP server, as a stdio proxy onto the same resolved configuration, exposing the server tools as `mcp__openviking__<tool>`. Hooks and MCP read the same credentials, so they cannot disagree about the server, account, or user.
- The `openviking-memory`, `openviking-skills`, and `ov-experience-memory` Skills, which tell the Agent how to use injected context and the memory tools.
- `scripts/ov-memory-doctor.mjs`, a client-side diagnostic that reports the install, the resolved configuration, the connection, and what the hooks last did.

## Verify

1. Run `codebuddy plugin list` and confirm `openviking-memory@openviking` is enabled.
2. Run the doctor from the installed copy; it should exit 0:
   ```bash
   cd ~/.codebuddy/plugins/cache/openviking/openviking-memory/<version>
   node scripts/ov-memory-doctor.mjs
   ```
3. Start an interactive session and ask it to quote the `<openviking-context>` block it was given. Injected context is delivered to the model for that request only and is **not** written to the transcript, so asking the model is the reliable check — grepping the session file finds nothing even when injection works.
4. Confirm the Tools list contains `mcp__openviking__*` entries and that the server reports `connected`.
5. Tell CodeBuddy a preference, let the session end normally, and confirm the commit in the hook log (`OPENVIKING_DEBUG=1`, then `~/.openviking/logs/cb-hooks.log`). After extraction completes, a new session can answer questions about it.

## How it works

- `SessionStart` loads your profile, the memory index, and an `<available-skills>` catalog, then replays anything the offline queue is holding.
- `UserPromptSubmit` recalls context for the current prompt and injects it as `<openviking-context>` in `additionalContext`. Host-generated continuation turns are skipped, so a turn the host produced on its own never triggers a search.
- `PreToolUse` denies reading or writing a `viking://` URI as a local path and points the Agent at the OpenViking MCP tools. The denial is the only model-visible channel this host offers: an `allow` carries no text through to the model. Shell commands are not checked, so `ov` invocations and literal `viking://` arguments still work.
- `Stop` captures new user and assistant turns incrementally.
- `PreCompact` and `SessionEnd` commit pending messages so they become an archive.
- `SubagentStart` and `SubagentStop` give a subagent its own session, `cb-<session>__subagent-<agent_id>`, and capture its transcript there.

Session IDs are `cb-<session id>`, derived from the CodeBuddy session ID so that resume, capture, and recall all target the same OpenViking session. The capture cursor lives under the plugin's data directory and moves only past turns the server accepted; a failed write goes to the offline queue under `~/.openviking/pending` and is replayed at the next session start.

### Behaviour that follows from the host

`UserPromptSubmit`, `SessionEnd`, and the two subagent events fire **only in the interactive TUI**. Headless runs — `-p` and `--input-format stream-json` — never emit them, so a headless session captures on `Stop` and never sends the final commit. This is a property of the host, not of the plugin; interactive sessions are unaffected.

`SessionStart` carries no `cwd` on this host, and it can be delivered twice per session, so the session-start hook is idempotent and falls back to the process working directory.

A hook that overruns its timeout is killed, but its child processes are not, which is what lets commits run in a detached worker after the hook process returns.

## Upgrade and uninstall

```bash
codebuddy plugin update openviking-memory@openviking
codebuddy plugin uninstall openviking-memory@openviking
```

`plugin update` compares the version declared in the marketplace against the installed one, so it only moves when the plugin's version string changes. Because the cache is keyed by version, a superseded version directory stays on disk until the host's own in-use sweep removes it; leave it alone, since sessions started before the update keep referencing it. Uninstalling leaves the `enabledPlugins` entry in `~/.codebuddy/settings.json`; remove it if you are re-installing under a different marketplace name.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Nothing is recalled and nothing is captured | Check `codebuddy plugin list`, then run the doctor. If it reports the plugin disabled, create `~/.openviking/ovcli.conf` or set `OPENVIKING_MEMORY_ENABLED=1`. |
| Recall never happens, but session-start context does | Recall runs on `UserPromptSubmit`, which this host emits only in the interactive TUI. Headless runs do not recall. |
| A session ends without an archive | `SessionEnd` is TUI-only as well; in a headless run, commits come from `Stop` once pending content crosses the commit threshold. |
| `mcp__openviking__*` tools are missing | Start a new session: tools registered by a plugin are loaded at session start. Then check the URL and key in `~/.openviking/ovcli.conf`. |
| Capture happened but the memories never appear | Memory extraction runs on commit. A short session may sit below the commit threshold; it is committed at `SessionEnd` or `PreCompact`, or when the pending tokens reach the threshold. |
| Detailed diagnostics are needed | Set `OPENVIKING_DEBUG=1`, run a session, and inspect `~/.openviking/logs/cb-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Plugin Settings](../configuration/02-client.md#plugin-settings)
- [Authentication](../guides/04-authentication.md)
- [Plugin development standard](./18-plugin-development.md) — the host contract this plugin was measured against is recorded in `examples/codebuddy-memory-plugin/docs/HOST-CONTRACT.md`
