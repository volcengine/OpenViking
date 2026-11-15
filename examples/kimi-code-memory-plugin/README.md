# OpenViking Memory for Kimi Code

Long-term memory for Kimi Code, backed by OpenViking. The plugin captures the
conversation the model actually saw, recalls relevant memories when you ask a
question, and exposes the server's own tools over MCP.

## Relationship to the legacy Kimi integration

This directory is a standalone, self-contained plugin for Kimi Code 2.x. Like
the legacy adapter, it is a community-maintained implementation, not a
first-party OpenViking product. It does not modify, import, or depend on the older
`examples/agent-hook-plugin/hosts/kimicode` adapter, which targets Kimi Code CLI
0.43.x and keeps its own code and documentation.

Both packages use the plugin name `openviking-memory`. The host registry keeps
one entry per name; installing this plugin does not modify the legacy
distribution's files or documentation. Do not enable both on the same host:
two Kimi memory plugins must not capture the same session twice.

The modules under `scripts/shared/` are generated from OpenViking's common
`examples/memory-plugin-shared/lib/`; they are not a copy of the legacy adapter.
Documentation for this plugin lives in this directory and in
`docs/{en,zh}/agent-integrations/22-kimi-code.md`; the legacy integration's
documentation is separate and unchanged.

## What has been verified, and what has not

**Verified here:** the hook payloads, MCP tool naming and wire protocol were read
from **Kimi Code 2.6.2**'s own embedded source, and the plugin's behaviour is
covered by behaviour tests against a fake OpenViking server — capture, recall,
commit, recovery, branch projection, forks, the MCP handshake and the archive
closure. Those tests run on Windows with Node 24 and an isolated
`KIMI_CODE_HOME`; the six entry points and MCP handshake were also exercised
from the unpacked final ZIP. A real OpenViking 0.5.5 server was additionally
exercised for the explicit backfill import: messages were delivered, subagent
sessions committed, and memory extraction produced memories.

**Verified on this machine (2026-11-16):** the plugin was installed through the
host's registry from a local path (`installed.json` entry, managed copy, doctor
reports no failures); the host-spawned `UserPromptSubmit` injected memory; the
real server accepted capture and commit and extracted memories for the main
session and for three subagent sessions; at a real turn boundary `Stop` delivered
the closed turn and committed it; a real compaction triggered `PreCompact`,
which committed the closed turn before the transcript was condensed; exiting the
TUI triggered `SessionEnd`, which wrote the end marker and committed every
settled agent; MCP `health`/`search` worked; and a direct recall injection
against the real server returned a context block.

**Not verified here:** POSIX, remote/Web environments and a real turn-sliced
fork remain unverified; media is unsupported by design. The TUI and Desktop use
the same hook path, so they are not separate verification surfaces.

**Source-level contract evidence:** the embedded **Kimi Code 2.6.2** source
confirms the remaining host-side contracts: POSIX spawns hooks detached and
signals the process group while Windows uses `taskkill /T`; `Stop`,
`PreCompact` and `SessionEnd` are registered events with timeout/stdout
handling; fork records carry `base.{branch,line}` and `legacyUndoLine`; session
metadata carries `environmentId`. That is source evidence, not a runtime test.

## Install

The plugin installs through Kimi Code's own plugin manager. There is no separate
installer, and this plugin never writes `installed.json` itself.

```text
/plugins install <path to this directory>
/plugins install <https://…/openviking-memory.zip>
```

Then check it:

```text
/plugins list
/plugins info openviking-memory
```

Re-run the same `/plugins install` command to upgrade. Removal is covered under
[Uninstall](#uninstall).

### Install from the plugin marketplace

For a repeatable install URL, point Kimi Code at the OpenViking-published
marketplace. It publishes only this plugin's ZIP (~280 KB), not the whole
OpenViking repository (~416 MB):

```text
https://docs.openviking.ai/dl/plugins/kimi/marketplace.json
```

Set it before launching Kimi Code:

```bash
export KIMI_CODE_PLUGIN_MARKETPLACE_URL=https://docs.openviking.ai/dl/plugins/kimi/marketplace.json
```

Or, in the TUI, open the plugin marketplace and enter that URL in the **Custom**
tab, then install **OpenViking Memory** from the list. The entry is marked
`third_party` because OpenViking is not the host vendor; Kimi verifies the ZIP's
SHA-256 from the marketplace before installing it.

To build the ZIP yourself:

```bash
node ./scripts/pack.mjs --out openviking-memory.zip
```

The archive root is the plugin root — `kimi.plugin.json` at the top level — and
it carries `scripts/` including the generated `scripts/shared/`, `servers/`,
`skills/` and `locales/`. Tests, `node_modules` and `.git` are excluded.

**Runtime requirement:** hooks run `node` from `PATH`, so Node.js **22.19+** must
be on the `PATH` the host inherits. A GUI launch may not read your shell profile;
if hooks silently never run, that is the first thing to check. The MCP proxy can
fall back to the host's own runtime.

## What it does

| Event | What happens | Output |
| --- | --- | --- |
| `SessionStart` | replays queued writes, sweeps sessions that ended earlier | none |
| `UserPromptSubmit` | delivers the previous turn, injects profile + recall | one text block |
| `PreToolUse` | refuses `viking://` paths handed to local file tools | deny JSON or none |
| `Stop` | delivers the closed turn, commits when the threshold is met | none |
| `PreCompact` | delivers, then commits regardless of thresholds | none |
| `SessionEnd` | writes an end marker, delivers, commits | none |

Everything else is explicit: the model calls the OpenViking MCP tools, or the
bundled Skills explain the workflows.

### Capture

Each agent of a session has its own `wire.jsonl`. The plugin reads it
incrementally — complete lines only, a byte cursor, a 32 MB scan budget — and
projects the **active branch**, which is a tree: `agent.switched` records move
the agent onto a branch based on an older line, and only the recursive chain is
what the model saw. Undo, clear and compaction are handled with the host's own
semantics, and a fork captures only what came after its `forked` marker.

User input becomes `user_query` only when the host would have shown it to the
model as a user message. A task prompt, a system trigger, an injection or a hook
result does not. Assistant text becomes ordered `assistant_step` messages; each
tool call stays on the assistant message and its result becomes a
`tool_transport` message linked by its `tool_id`. Thinking parts, media bytes
and compaction summaries are not captured.

### Delivery and commit

Messages go out in the server's batches. A message counts as delivered only when
the server confirms it. A durable queued message may advance the handover
cursor, but remains queued in the ledger, is **not** delivered, and prevents
newer messages from overtaking it. A non-retryable failure is recorded once and
is not retried on every event — only a change to the capture configuration or
credential-source fingerprint retries it. Until then the agent cannot commit,
because archiving a transcript with a hole in it would be worse than waiting.

`commit` is requested when the server reports enough pending tokens, or after
enough complete turns, or at `PreCompact`/`SessionEnd`, which always commit what
is already delivered. `keep_recent_count` is fixed at `0`: Kimi Code holds its own
context, so the server does not need to keep a tail.

A commit that only reached the local queue is not a commit. It settles only when
a later replay reports the server accepted it.

### Recall

The first successful prompt also injects your profile and the server's skill
catalogue; later prompts inject recall only. `noAutoInject` turns off the profile
and skill list, `autoRecall` turns off per-turn recall, and `minQueryLength`
limits recall to queries worth searching. All three are independent, and none of
them affects explicit MCP calls.

Recall fails open. A missing server, a timeout or an empty answer produces no
text at all — never an error message in your prompt. Two consecutive failures
pause recall for a minute, a third for five, and a success resets it.

### Local protection

A `viking://` URI handed to `Read`, `Glob`, `Grep`, `Edit`, `Write` or
`ReadMediaFile` is refused with the MCP tool that can answer it instead. A shell
command is never blocked: `viking://` in a command line is usually data. This
host does not display `additionalContext` on `PreToolUse`, so the Skills explain
the MCP route rather than relying on a notice.

## Configuration

All settings come from the shared OpenViking configuration chain, resolved for
the **session's** directory, not the plugin's:

```text
OPENVIKING_* env → workspace registry → .openviking/config.local.json
  → .openviking/config.json → ovcli.conf plugin.kimicode → ovcli.conf plugin
  → ov.conf kimicode → schema defaults
```

Credentials are resolved separately by `resolveConnection()` and never come from
a workspace file. The complete list of knobs, their aliases and defaults is in
[the shared library README](../memory-plugin-shared/README.md); this page only
covers what is specific to Kimi Code.

| Setting | Effect here |
| --- | --- |
| `enabled`, `autoCapture`, bypass | same as every harness; bypass affects hooks only, not MCP |
| `autoRecall`, `noAutoInject`, `minQueryLength` | the three independent recall switches above |
| `recallQueryFilters` | rewrites or drops the recall query before the length gate, like CC and Codex |
| `commitTokenThreshold`, `commitTurnThreshold` | when a normal `Stop` commits |
| `autoCommitOnCompact` | `false` skips only `PreCompact`'s commit |
| `captureMaxLength`, `captureToolMaxChars`, `captureFilters` | applied by the shared capture shaper |
| `profileTokenBudget`, `skillCatalog`, `skillCatalogTokenBudget` | the profile block |
| `commitKeepRecentCount` | **not used**; this plugin always sends 0 |
| `mcpEnabled` | **not used**; use `/plugins mcp enable\|disable openviking-memory openviking` |
| `writePathAsync` | **not used**; no hook detaches a worker |

`captureToolResults` has no consumer on this path: tool evidence is always
captured. `sessionStartMaxBytes`, the resume/takeover/repo-context knobs and
`recallCompress*` have no corresponding host path and are not advertised.

## Tools

The MCP server is named `openviking`, so the host displays tools as
`mcp__plugin-openviking-memory_openviking__<tool>`. The catalogue is whatever the
server's `tools/list` returns; this plugin has no copy of it.

| Workflow | Skill |
| --- | --- |
| retrieve, remember, forget | `openviking-memory` |
| find, read, create, install server Skills | `openviking-skills` |
| retrieve applicable Rules | `openviking-rule-search` |
| retrieve and apply Experience | `ov-experience-memory` |

Kimi Code shows them as `openviking-memory:<skill>`. The Skills name the tools
the server actually exposes and say what to do when a server version lacks
`rule_search` or `add_skill`.

## Diagnostics and recovery

```bash
node ./scripts/doctor.mjs             # full report
node ./scripts/doctor.mjs --offline   # no network requests
node ./scripts/doctor.mjs --json
```

The doctor checks the manifest and hook registration, the host's plugin registry,
leftover hand-written hooks in `config.toml`, the resolved configuration and
credentials, the session layout, every session's blocked state, the offline
queue, and — unless `--offline` — the server's health.

A capture that failed permanently, or a queued message the queue dropped, blocks
commits on purpose. Two commands clear a blockage, and both need the session id:

```bash
node ./scripts/recover.mjs retry-commit --session <id> --agent <agentId>
node ./scripts/recover.mjs accept-loss  --session <id> --agent <agentId> --yes
node ./scripts/recover.mjs accept-loss  --session <id> --all-agents --yes
```

`retry-commit` refuses while a message is still only in the queue: retrying a
commit over undelivered messages is exactly the hole the block prevents. It warns
that a commit the server already accepted may be archived twice.
`accept-loss` records the loss in a tombstone, advances the cursor past it, and
keeps `hasCaptureGaps` set; the session reports `partial_loss` only when every
other agent is settled. Neither command touches the network, deletes a wire file
or removes a memory.

## Importing existing history

Hooks only remember from the moment they first see a session; nothing installed
before the plugin existed is replayed automatically. To import those sessions:

```bash
node ./scripts/backfill.mjs --dry-run        # what would be imported
node ./scripts/backfill.mjs                  # import every eligible session
node ./scripts/backfill.mjs --session <id>   # one native session
```

A session with no plugin state is read from its first record, every closed turn
goes through the same capture pipeline the hooks use, and the final pass commits
it, so even a short session gets extracted. The state file it writes is the one
the hooks already use, so later capture continues from that cursor.

A session that already has plugin state is only caught up from it: OpenViking
has no server-side message dedup, so replaying a captured prefix would create a
second copy. A corrupt or unreadable state is skipped and left for
`doctor`/`recover`. `autoCapture=false` and `bypassSession` are honored, so a
session the hooks stay out of is not imported by this command either. The
command checks `/health` before its first write and refuses to run when the
server is unreachable, rather than enqueueing an entire history for later.

A live session's open tail is not sent; it waits for the next hook or a later
backfill run. While that tail is open the session's commit is blocked by the
same guard as a normal boundary, so its pending messages stay uncommitted until
the turn ends. A fork is imported only after its `forked` marker: inherited
prefix history is not copied, because the parent may already own it. Deleting
the plugin state file and re-running the command would import the transcript
again; the state file is the only duplicate guard. Non-local environments and
legacy or incompatible wires are skipped.

Each pass is bounded and persisted, so an interrupted run resumes where it
stopped. `--timeout <seconds>` (default 300) bounds one session, `--limit <n>`
caps how many are processed, and `--json` prints machine-readable results.

## Limits

- **At-least-once, not exactly-once.** A send that succeeded while the state
  write failed is replayed. Stable wire ids suppress most duplicates; the set is
  bounded, so a very long session may repeat an old message.
- **No rollback of delivered turns.** Undo, clear and branch switches only affect
  what has not been delivered yet. A message the server already has stays.
- **No automatic history import.** A session the plugin has not seen starts at
  the current end of its wire; importing the past is the explicit
  `scripts/backfill.mjs` job described above.
- **No media.** Only text is captured; images, audio and video contribute nothing
  and are never read from disk.
- **Local environment only.** A session with a non-local `environmentId` is
  skipped with a diagnosis: its wire is not on this machine.
- **Crash tails need the sweep.** An open turn is closed only when an end marker
  exists, the lock is held and the wire is quiet across two reads. A crash with
  no marker waits for the host to write the terminal record.
- **Legacy wires are not guessed.** A wire without a `metadata` line is reported
  and skipped rather than interpreted as protocol 1.5.
- **One commit per sweep.** A backlog drains over several starts by design.

## Uninstall

```text
/plugins disable openviking-memory
/plugins remove openviking-memory
```

`remove` drops the registration and leaves the plugin's files in place, as this
host documents. Memories, queued writes and other plugins are untouched.

## Development

```bash
npm test                       # unit + contract + e2e
node ./scripts/pack.mjs --check
```

The shared modules in `scripts/shared/` and the four skills are **generated**:

```bash
node examples/memory-plugin-shared/sync.mjs
```

Never edit those copies. Change `examples/memory-plugin-shared/lib/` or
`examples/skills/` and regenerate.

The tests use a temporary `KIMI_CODE_HOME`, a temporary pending directory and a
fake OpenViking server on a port the OS chooses, so they never touch a real
session or a real server.
