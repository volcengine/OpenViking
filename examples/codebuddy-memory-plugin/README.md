# OpenViking memory plugin for the CodeBuddy Code CLI

Long-term semantic memory for [CodeBuddy Code](https://cnb.cool/codebuddy/codebuddy-code),
powered by [OpenViking](https://github.com/volcengine/OpenViking).

The plugin reads and writes OpenViking through lifecycle hooks, and exposes the
OpenViking tools through MCP. Both halves resolve the same plugin configuration,
so hooks and MCP always agree on the server, account and user — a property the
shared test suite checks directly (`mcp-hook-parity`).

## Status

Built against CodeBuddy Code **v2.163.0** (also exercised on v2.164.0). The host
contract was established by running a throwaway probe plugin against real CLI
sessions — interactive TUI, `-p`, and `--input-format stream-json` — not by
reading the docs alone. [`docs/HOST-CONTRACT.md`](docs/HOST-CONTRACT.md) records
the verified facts, the method and what is still unknown. The findings that
shaped the design:

- hooks fire in both the interactive TUI and headless (`-p`) runs, **but
  `UserPromptSubmit`, `SessionEnd` and `SubagentStart`/`SubagentStop` only fire in
  the interactive TUI**;
- the `additionalContext` envelope is byte-compatible with Claude Code, unknown
  JSON fields are tolerated, and empty output is safe;
- `PreToolUse` has **no advisory channel** — an `allow`'s `permissionDecisionReason`
  never reaches the model. A `deny`'s reason *does*, as the failed tool result,
  which is why the URI guard denies rather than notices;
- a hook that overruns its `timeout` is killed, but **its descendants are not**,
  which makes the detached async write path safe;
- `SessionStart` carries no `cwd` (use `process.cwd()`), and the transcript is
  already complete by the time `Stop` fires.

## Layout

```
.codebuddy-plugin/plugin.json   manifest (only `name` is required)
hooks/hooks.json                hook registrations (${CODEBUDDY_PLUGIN_ROOT})
.mcp.json                       MCP server for this plugin
servers/mcp-proxy.mjs           stdio MCP proxy onto the resolved configuration
scripts/                        hand-written hook entrypoints + tests
scripts/lib/                    hand-written, plugin-local helpers
scripts/shared/                 GENERATED from examples/memory-plugin-shared/lib — do not edit
skills/                         GENERATED from examples/skills — do not edit
docs/HOST-CONTRACT.md           verified host contract + method
```

`scripts/shared/` and `skills/` are produced by
`node examples/memory-plugin-shared/sync.mjs` from the repository root. Edit the
shared library under `examples/memory-plugin-shared/lib/`, then re-run the
generator; never edit the generated copies.

## What the hooks do

| Event | Entrypoint | Behaviour |
| --- | --- | --- |
| `SessionStart` | `session-start.mjs` | health probe → replay the offline queue → inject the profile/catalog (and, on resume/compact, the archive block) as one `<openviking-context>` block |
| `UserPromptSubmit` | `auto-recall.mjs` | search OpenViking and inject an `<openviking-context>` block; skips host-generated continuations (`is_internal_continuation`) |
| `PreToolUse` | `uri-guard.mjs` | deny file-tool calls whose path is a `viking://` URI, pointing at the OpenViking MCP tool |
| `Stop` | `auto-capture.mjs` | read the transcript incrementally, push new turns, commit once pending passes the threshold |
| `PreCompact` | `pre-compact.mjs` | commit before the transcript is rewritten |
| `SessionEnd` | `session-end.mjs` | final commit so the last turns become an archive |
| `SubagentStart` | `subagent-start.mjs` | remember the subagent's own OV session id |
| `SubagentStop` | `subagent-stop.mjs` | push the subagent's transcript into that session and commit |

The increment cursor lives under `${CODEBUDDY_PLUGIN_DATA}/capture-state` and
advances only on an acknowledged write, so a transient failure re-pushes rather
than dropping turns, and a re-delivered `Stop` sends nothing twice. Failures go
to an offline queue (`~/.openviking/pending`) that the next `SessionStart`
replays.

⚠️ **Capture writes your conversation into OpenViking.** That is the point, but
it is worth stating: with the plugin enabled, session transcripts are sent to the
configured server, where OpenViking extracts memories from them. Use
`OPENVIKING_MEMORY_ENABLED=0`, `OPENVIKING_BYPASS_SESSION`, or
`OPENVIKING_BYPASS_SESSION_PATTERNS` to keep particular sessions out.

## Configuration

Resolution order (shared with every OpenViking memory plugin):

```
OPENVIKING_* env vars → workspace .openviking/config*.json → ovcli.conf `plugin.codebuddy`
→ ovcli.conf `plugin` → ov.conf `codebuddy` section → schema defaults
```

Connection and credentials always come from `OPENVIKING_URL` /
`OPENVIKING_API_KEY` (or `OPENVIKING_BEARER_TOKEN`), then `ovcli.conf`, then
`ov.conf`'s `server` section. On a machine with no environment at all, a
`~/.openviking/ovcli.conf` holding `{ "url": …, "api_key": … }` is enough.

Enable/disable: `OPENVIKING_MEMORY_ENABLED=0|1`, or `codebuddy.enabled` in
`ov.conf`. Without either, the plugin is enabled only when `ov.conf` or
`ovcli.conf` exists.

Debug logging: `OPENVIKING_DEBUG=1` (or `codebuddy.debug: true`) writes JSON
Lines to `~/.openviking/logs/cb-hooks.log` (override with
`OPENVIKING_DEBUG_LOG`).

## Loading it locally

```bash
codebuddy --plugin-dir examples/codebuddy-memory-plugin
```

`--plugin-dir` is session-scoped: nothing is written to any CodeBuddy
configuration, and dropping the flag leaves no trace — except the plugin's own
state under `~/.codebuddy/plugins/data/<id>-inline/`. For a persistent install,
add the local marketplace under `examples/codebuddy-memory-plugin-marketplace`
and install it into the user scope:

```bash
codebuddy plugin marketplace add examples
codebuddy plugin install openviking-memory@openviking --scope user
```

## Tests and diagnostics

```bash
npm test                                # 34 unit/hook tests (mock OpenViking; no network)
node scripts/ov-memory-doctor.mjs       # install + config + connection + activity report
node scripts/ov-memory-doctor.mjs --json
```

The doctor reads the plugin registry, the resolved configuration, the live
server, and the state the hooks leave behind. Its install section only goes
green once the plugin is installed through a marketplace; in a `--plugin-dir`
dev checkout it warns instead of failing, apart from the
`codebuddy plugin list` row.

## Known limitations

- **`PreCompact` fires only when compaction actually happens.** Verified in a real
  TUI session: the host leaves a `{"type":"summary","providerData":{"source":"pre-compact"}}`
  record, and this plugin's commit is what archived the turns. `/compact` triggers it on
  demand if you want to exercise it deliberately.
- **Recall and the final commit only run in the interactive TUI.** Headless
  (`-p`, `--input-format stream-json`) runs never emit `UserPromptSubmit` or
  `SessionEnd`, so a headless session captures on `Stop` and never commits. This
  is a host property, not a plugin bug.
- **The URI guard cannot advise, only block.** CodeBuddy defines no
  `additionalContext` under `PreToolUse`, and an `allow`'s reason never reaches
  the model, so there is no way to say "careful, that's a virtual URI" while
  letting a call proceed. `Bash` is left out of the guard's matcher for the same
  reason.
- **A subagent is captured only at `SubagentStop`.** In-subagent hooks never
  fire, so a subagent that dies without the event leaves its session unrecorded.
- **Skills are CodeBuddy-discovered, not OpenViking-installed.** The three
  bundled skills are files under `skills/`; the skill *catalog* that the plugin
  injects at session start is whatever the server holds.

## Naming

The harness id is **`codebuddy`**. OpenViking's server-side log ingestion ships a
`workbuddy` adapter that parses this same transcript format, but that name
belongs to the server's ingest namespace — it is not a harness id and must not be
registered in `HARNESS_KEYS` (a `plugin.workbuddy.*` override is silently
ignored).
