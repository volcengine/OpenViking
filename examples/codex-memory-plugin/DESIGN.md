# Codex memory plugin — commit decision design

This document records *why* the plugin commits when it commits. The commit
shape (which OpenViking session is sealed by which hook event) is the part
worth understanding before reading code: the codex hook surface gives us
**no clean SessionEnd signal**, so we have to reason about which observable
events imply "context for a particular codex `session_id` is gone".

## Vocabulary

- **codex `session_id`** — the codex thread/session id. Stable across
  process restarts when zouk-daemon resumes the same thread; replaced when
  `/clear`, `/new`, fresh codex startup, or zouk reset occurs.
- **OV session** — `viking://user/sessions/cx-<codex-session-id>`. New captures
  derive the OV session id from the codex `session_id` with a `cx-` prefix,
  append messages on every `Stop`, and commit it (which triggers OV's
  memory extractor) at session-end-equivalent moments. `/messages`
  auto-creates the OV session, so the plugin does not call session create.
- **State file** — `~/.openviking/codex-plugin-state/<safe-codex-session-id>.json`,
  shape `{ codexSessionId, ovSessionId, capturedTurnCount, createdAt, lastUpdatedAt }`.
- **Active window** — state files whose `lastUpdatedAt` is within
  `ACTIVE_WINDOW_MS` (default 2 min) of "now". Used to detect "the codex
  session that just ended".

## Codex hook surface (what we observe)

| Codex event | Fires when | What we learn |
|---|---|---|
| `SessionStart` source=`startup` | fresh codex process; `/new`; zouk daemon spawn-without-sessionId; zouk reset | new `session_id` was created |
| `SessionStart` source=`resume` | `/resume`; short reconnect; zouk daemon spawn-with-sessionId | same `session_id` continues; may need archive continuity |
| `SessionStart` source=`clear` | `/clear` (creates a fresh thread, preserves prior thread on disk as resumable) | new `session_id`; previous one orphaned |
| `UserPromptSubmit` | every user turn before model | recall context inject |
| `Stop` | end of every model turn (NOT end of session) | append turns to OV session |
| `PreCompact` | `/compact` or auto-compact | context is about to be summarized |
| `PostCompact` | after compaction | (unused) |
| SIGTERM / SIGINT / Ctrl+C / `/exit` | process killed | **no hook fires** — confirmed in `codex-rs/hooks/src/events/` |

Verified against codex-rs `main` 2026-05-10. Upstream issues #17421, #20374
have requested a `SessionEnd` hook; OpenAI rejected with two reasons:
"threads can always be resumed" and "/exit only makes sense in TUI". Not
landing.

## Commit triggers

We commit an OV session in exactly these places. Everything else is no-op
or append-only.

### 1. `PreCompact` — deterministic, current session

Codex fires `PreCompact` before summarizing. We catch up with any
unappended turns from the transcript, commit the OV session for this codex
`session_id`, and clear `ovSessionId` so the next `Stop` re-derives the
same `cx-<codex-session-id>` OV session id for the post-compact half.
`capturedTurnCount` is preserved unless the transcript was truncated by
compaction (see "Post-compact transcript shrink" below).

### 2. `SessionStart` source=`clear` — heuristic, same shape as `startup`

`/clear` creates a brand-new codex `session_id` and orphans the previous
in-memory thread (preserved on disk). Naively committing "every state file
whose `codexSessionId` ≠ new id" would falsely commit concurrent codex
processes' still-active sessions on the same machine.

Instead, we treat `clear` and `startup` identically: both run the
**active-window heuristic** below. `/clear` only invalidates the current
codex process's *previous* session; the heuristic correctly catches that
session (a single recently-touched orphan) without trampling unrelated
parallel codex processes.

### 3. `SessionStart` source=`startup` — heuristic, active-window

Triggered by `/new`, fresh codex CLI startup, and zouk daemon
spawn-without-sessionId (including zouk's "reset codex" UI action).

The hook script gates internally on `source ∈ {startup, clear}`. On a
match, it iterates state files (excluding the new `session_id` itself) and
counts how many were touched within `ACTIVE_WINDOW_MS`:

```
recently-active count ⇒ action
─────────────────────────────────
0     ⇒ no-op (no orphan to commit)
1     ⇒ commit it (the just-ended session)
≥2    ⇒ skip; rely on idle TTL
```

The single-recent case captures the common path: user runs codex, hits
`/new` or `/clear` after a turn or two; the previous session's `Stop` just
fired and bumped `lastUpdatedAt`; we commit it. The multi-recent case
implies concurrent codex sessions are active; we can't tell which one (if
any) ended, so we defer to idle TTL to clean up genuinely-dead ones.

### 4. `SessionStart` source=`resume` — never commits, optional archive inject

Short reconnects and `/resume` re-fire `SessionStart` for the same
`session_id`. Committing here would seal a still-active session. So
`resume` is a no-op for commit purposes.

Resume may still need continuity after `PreCompact` or idle sweep already
committed the live OV session. If local state has `ovSessionId = null`
(or no state file remains), the hook derives `cx-<codex-session-id>`,
calls `GET /api/v1/sessions/{id}/context?token_budget=...`, and injects
`latest_archive_overview` via `hookSpecificOutput.additionalContext` when
present. The injected block includes
`viking://user/sessions/{id}/history/` so the model can use OpenViking MCP
read/search tools for exact prior details.

If local state still has a live `ovSessionId`, resume injection is skipped:
the session is appendable and Codex should already be resuming its own
transcript.

### 5. Idle TTL sweep — fallback

State files whose `lastUpdatedAt` is older than `IDLE_TTL_MS` (default 30
min) get committed and cleared. Mental model: a session not touched for
30 min is "temporarily concluded"; if the user resumes later, subsequent
turns append under the same deterministic OV session id, and the next
commit creates another archive there.

This covers:
- SIGTERM / Ctrl+C / `/exit` (no hook fires; state file rots)
- Crashes
- Mid-turn zouk reset where `Stop` got cancelled before bumping
  `lastUpdatedAt`
- The `≥2 recently-active` skip from rule 3

**Sweep trigger**: at the tail of `session-start-commit.mjs` only. We do
not sweep on every `Stop` because state-write-on-every-turn already gives
us the freshness signal we need; running the sweep once per session start
is the right cadence. The Stop hook contains a comment marking the option
to add sweep there if codex's session creation rate is low enough that
arbitrarily-orphaned state files accumulate.

**Known limitation**: if the user never starts another codex on this
machine, no sweep ever runs and the OV session stays open server-side
forever. Accepted. Future work could add an MCP tool
`openviking_commit_pending` so the model can commit explicitly.

## Stop hook — append only, no commit

Every `Stop` reads `transcript_path`, slices to `[capturedTurnCount, end)`,
and appends each new user/assistant turn to the OV session for this codex
`session_id` (the `/messages` endpoint auto-creates it on first append).
State is updated:
`{ovSessionId, capturedTurnCount, lastUpdatedAt: now}`. Never commits.

## Injected context boundary

`UserPromptSubmit` stdin includes the user's `prompt` plus the Codex
`session_id`. Recall derives the same OpenViking session id used by Stop
capture (`cx-<safe-session-id>`) directly from the Codex session id and
calls `/api/v1/search/search` with that `session_id`, so OpenViking can
use recent session messages and archive overview during query expansion.
Recall does not read plugin state, so a corrupt or missing state file
cannot crash the recall hook. Recalled memory is sent back through
`hookSpecificOutput.additionalContext`, then Codex injects it into the
model turn. Transcript capture may later see that injected context
adjacent to the prompt, so plugin-generated recall and resume context are
wrapped in a deterministic boundary:

```text
<openviking-context source="auto-recall" format="digest">
OpenViking memory digest:
- ...
</openviking-context>
```

The compressor is still instructed not to generate XML/HTML wrappers. The
wrapper is added by the hook after compression so capture can strip it
mechanically. Legacy `<relevant-memory>` / `<relevant-memories>` blocks and
unwrapped `OpenViking memory digest:` blocks are stripped as backward
compatibility fallbacks.

## Edge cases handled

### Post-compact transcript shrink

Codex's `/compact` may rewrite or truncate `transcript_path`. After
compaction, if `allTurns.length < state.capturedTurnCount`, our slice
math underflows and we silently drop new turns. Defensive fix: when this
inequality is detected on `Stop`, reset `capturedTurnCount = 0` so the
next slice captures everything in the new transcript.

### Commit failure

When OV `/commit` returns non-2xx or times out, we currently log and treat
the result as null. We must NOT call `clearState` on failure — keep the
state file so the next sweep / SessionStart can retry. A transient OV
outage shouldn't lose a session's worth of memory.

### Race: SIGTERM before Stop completes

Codex's tokio runtime cancels in-flight async tasks on SIGTERM, so the last
turn's `Stop` hook may be aborted before it bumps `lastUpdatedAt`. This
makes the state look older than it actually is. Consequence: that session
may fall outside the 2 min active window when the user respawns codex and
we can't commit it deterministically — idle TTL will catch it later.

### Commit-then-resume

After PreCompact we set `ovSessionId = null` but keep
`capturedTurnCount`. The next `Stop` for the same codex `session_id`
re-derives the same `cx-<codex-session-id>` OV session id and starts
appending from `capturedTurnCount`. Memory remains grouped under the same
OV session id, while commits create additional archives under that session.

## State file schema

```json
{
  "codexSessionId": "0193af...",   // codex thread id
  "ovSessionId": "cx-0193af...-or-null", // null means "committed, awaiting next Stop"
  "capturedTurnCount": 7,            // turns from transcript already appended
  "createdAt": 1715000000000,
  "lastUpdatedAt": 1715000300000
}
```

Legacy state files from earlier plugin versions may still contain a UUID
`ovSessionId`; those are now overwritten with the derived `cx-*` id on the
next resolve. The migration window for preserving old UUID sessions has
closed.

State files are atomic-write (tmpfile + rename) to survive crash mid-write.

## Configuration

Env var overrides for tuning without rebuilding:

| Var | Default | Purpose |
|---|---|---|
| `OPENVIKING_CODEX_STATE_DIR` | `~/.openviking/codex-plugin-state` | state file dir |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` (2 min) | rule-3 active window |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` (30 min) | idle sweep TTL |
| `OPENVIKING_RECALL_TIMEOUT_MS` | `120000` (2 min) | whole UserPromptSubmit auto-recall deadline |
| `OPENVIKING_RECALL_COMPRESS` | `1` | set `0` / `off` to skip `codex exec` compression |
| `OPENVIKING_RECALL_COMPRESS_MODEL` | unset | custom first-choice compressor model; `off` disables compression |
| `OPENVIKING_RECALL_COMPRESS_THINKING` | unset | custom `model_reasoning_effort`; `default` means omit override; alias `OPENVIKING_RECALL_COMPRESS_REASONING_EFFORT` |
| `OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP` | `1` | recreate/cache compressor profile during every `SessionStart` |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS` | `15000` | per-candidate compressor probe timeout |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS` | `604800000` (7 days) | cache TTL used by `UserPromptSubmit` reads |
| `OPENVIKING_RESUME_ARCHIVE_INJECT` | `1` | inject latest archive summary on `source=resume` when no live OV session is open |
| `OPENVIKING_RESUME_ARCHIVE_TOKEN_BUDGET` | `32000` | token budget for `/sessions/{id}/context` on resume |
| `OPENVIKING_RESUME_ARCHIVE_MAX_CHARS` | `6000` | max chars injected from latest archive overview |
| `OPENVIKING_CAPTURE_TOOL_MAX_CHARS` | `2000` | max chars retained per compressed tool call/result |
| `OPENVIKING_DEBUG` | `0` | enable hook debug log |

## Resume context inject

`SessionStart` `source=resume` runs only the archive-inject path above. It
never commits and never runs idle sweep. This keeps short reconnects cheap
while still restoring continuity after a committed archive. The API shape
is the existing session context endpoint; no archive listing UX is required
for the model.

Injected context is intentionally a summary, not raw history. If exact
commands, file paths, code snippets, config values, or tool outputs matter,
the injected `viking://` URI tells the model to use OpenViking MCP
read/search tools.

## Recall compressor profile

`codex exec` supports `--model` / `-m`, and Codex config overrides such as
`model_reasoning_effort` are passed with `-c`. The recall compressor uses
both:

```bash
codex -m <model> -c 'model_reasoning_effort="low"' exec ...
```

`thinking=default` omits the `model_reasoning_effort` override. This is
important for model families whose default effort is tuned by Codex.

Model availability is re-probed at every `SessionStart`, not in every
`UserPromptSubmit`. Recreating the profile on each session start catches
cross-session env/config changes. The detector writes
`recall-compressor-profile.json` under `OPENVIKING_CODEX_STATE_DIR` and
auto-recall reads that cache. Cache misses in auto-recall use the first
candidate directly and fall back to deterministic digest if `codex exec`
fails.

Fallback order:

1. configured model/thinking (`OPENVIKING_RECALL_COMPRESS_MODEL` +
   `OPENVIKING_RECALL_COMPRESS_THINKING`)
2. `gpt-5.3-codex-spark`, thinking `default`
3. `gpt-5.5`, thinking `low`
4. off (deterministic digest, no child `codex exec`)

Configured `off` (`OPENVIKING_RECALL_COMPRESS=0`, model `off`, or thinking
`off`) skips all probing and writes a disabled profile.

## What changed vs v0.3.1

- `SessionStart` matcher widened from `"clear"` to `"clear|startup|resume"`
  so the active-window heuristic runs on /clear and /new (and zouk reset),
  while `/resume` can inject latest archive context without commit/sweep.
- `session-start-commit.mjs` switches commit logic from "all non-current"
  to active-window heuristic.
- Idle TTL sweep brought back, but only at the tail of
  `session-start-commit.mjs` (not every `Stop`). Default TTL 30 min.
- `auto-capture.mjs` Stop hook guards against post-compact transcript
  shrink (resets `capturedTurnCount` to 0 if `allTurns.length` < cached).
- Capture parsing shared by Stop and PreCompact now filters obvious hook
  noise, strips deterministic OpenViking context wrappers, and compresses
  tool calls/results instead of dropping them or storing full blobs.
- `auto-recall.mjs` has a whole-hook timeout (default 2 min) in addition
  to per-request timeouts.
- Recall compression model selection is recreated at each SessionStart and
  cached so each user prompt does not probe Codex model availability.
- All commit failure paths preserve state instead of clearing.
- All state writes go through tmpfile + rename for crash safety.

## Open questions / future work

- **MCP tool `openviking_commit_pending`**: explicit commit for the model
  to call, useful when user knows they're about to exit.
- **Subagent hook events**: kimicode has them, codex doesn't yet.
  When codex adds them, we should hook to keep subagent memory threads
  separate from main session.
- **Upstream `SessionEnd`**: rejected by OpenAI. If they reverse, idle
  TTL becomes redundant — replace with deterministic SessionEnd commit.

## Verified hook payload reference

```json
// SessionStart input (from codex-rs/hooks/schema/generated/session-start.command.input.schema.json)
{
  "session_id": "0193af...",
  "source": "startup" | "resume" | "clear",
  "cwd": "/path/to/cwd",
  "model": "gpt-5.5",
  "permission_mode": "default" | "acceptEdits" | "plan" | "dontAsk" | "bypassPermissions",
  "transcript_path": "/path/to/rollout.jsonl" | null,
  "hook_event_name": "SessionStart"
}

// UserPromptSubmit input
{
  "session_id": "0193af...",
  "prompt": "user prompt text",
  "cwd": "/path/to/cwd",
  "model": "gpt-5.5",
  "permission_mode": "default",
  "hook_event_name": "UserPromptSubmit"
}

// Stop input
{
  "session_id": "0193af...",
  "turn_id": "turn-N",
  "transcript_path": "/path/to/rollout.jsonl",
  "last_assistant_message": "...",
  "stop_hook_active": false,
  "model": "gpt-5.5",
  "permission_mode": "default",
  "cwd": "/path/to/cwd",
  "hook_event_name": "Stop"
}

// PreCompact input
{
  "session_id": "0193af...",
  "transcript_path": "/path/to/rollout.jsonl",
  "trigger": "manual" | "auto",
  "cwd": "/path/to/cwd",
  "model": "gpt-5.5",
  "hook_event_name": "PreCompact"
}
```

Output schema for SessionStart / UserPromptSubmit supports
`hookSpecificOutput.additionalContext`. Stop / PreCompact only support
`{ continue, stopReason, suppressOutput, systemMessage }` — `{}` is a
valid no-op.
