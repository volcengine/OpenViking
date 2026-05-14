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
- **OV session** — `viking://session/<uuid>`. We open one per codex
  `session_id`, append messages on every `Stop`, and commit it (which
  triggers OV's memory extractor) at session-end-equivalent moments.
- **State file** — `~/.openviking/codex-plugin-state/<safe-codex-session-id>.json`,
  shape `{ codexSessionId, ovSessionId, capturedTurnCount, createdAt, lastUpdatedAt }`.
- **Active window** — state files whose `lastUpdatedAt` is within
  `ACTIVE_WINDOW_MS` (default 2 min) of "now". Used to detect "the codex
  session that just ended".

## Codex hook surface (what we observe)

| Codex event | Fires when | What we learn |
|---|---|---|
| `SessionStart` source=`startup` | fresh codex process; `/new`; zouk daemon spawn-without-sessionId; zouk reset | new `session_id` was created |
| `SessionStart` source=`resume` | `/resume`; short reconnect; zouk daemon spawn-with-sessionId | same `session_id` continues |
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
`session_id`, and clear `ovSessionId` so the next `Stop` opens a fresh OV
session for the post-compact half. `capturedTurnCount` is preserved unless
the transcript was truncated by compaction (see "Post-compact transcript
shrink" below).

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

### 4. `SessionStart` source=`resume` — never commits

Short reconnects and `/resume` re-fire `SessionStart` for the same
`session_id`. Committing here would seal a still-active session. So
`resume` is a no-op for commit purposes.

### 5. Idle TTL sweep — fallback

State files whose `lastUpdatedAt` is older than `IDLE_TTL_MS` (default 30
min) get committed and cleared. Mental model: a session not touched for
30 min is "temporarily concluded"; if the user resumes later, they get a
fresh OV session for the new turns (memory will be split, but each chunk
gets extracted).

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
`session_id` (creating one on first append). State is updated:
`{ovSessionId, capturedTurnCount, lastUpdatedAt: now}`. Never commits.

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

After PreCompact (or idle sweep, or rule-3 commit) we set `ovSessionId =
null` but keep `capturedTurnCount`. The next `Stop` for the same codex
`session_id` opens a fresh OV session and starts appending from
`capturedTurnCount`. Memory ends up split across two OV sessions; each
gets extracted independently. Acceptable.

## State file schema

```json
{
  "codexSessionId": "0193af...",   // codex thread id
  "ovSessionId": "uuid-or-null",    // null means "committed, awaiting next Stop"
  "capturedTurnCount": 7,            // turns from transcript already appended
  "createdAt": 1715000000000,
  "lastUpdatedAt": 1715000300000
}
```

State files are atomic-write (tmpfile + rename) to survive crash mid-write.

## Configuration

Env var overrides for tuning without rebuilding:

| Var | Default | Purpose |
|---|---|---|
| `OPENVIKING_CODEX_STATE_DIR` | `~/.openviking/codex-plugin-state` | state file dir |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` (2 min) | rule-3 active window |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` (30 min) | idle sweep TTL |
| `OPENVIKING_DEBUG` | `0` | enable hook debug log |

## Phase 2: resume context inject (not yet implemented)

When `SessionStart` source=`resume` fires for a codex `session_id` whose
state shows `ovSessionId = null` (already committed via idle TTL or
PreCompact), we have no live OV session to resume into. The model loses
continuity unless the most recent committed memories are surfaced.

Proposed flow:
1. Load state for the resumed `session_id`. If `ovSessionId` is non-null,
   no action — the session is still appendable.
2. Otherwise list `viking://session/<codex-session-id>/history/archive_*/`
   on the OV server, take the most recent.
3. Read its abstract (L0) / overview (L1).
4. Emit via `hookSpecificOutput.additionalContext` so codex injects the
   summary into the resumed turn.

Deferred because (a) it requires a new OV API call shape, (b) the failure
mode is acceptable in v0.3 (model just lacks continuity for one turn,
recovers via auto-recall), and (c) the core commit logic above must be
proven first.

## What changed vs v0.3.1

- `SessionStart` matcher widened from `"clear"` to `"clear|startup"` so the
  active-window heuristic runs on both /clear and /new (and zouk reset).
- `session-start-commit.mjs` switches commit logic from "all non-current"
  to active-window heuristic.
- Idle TTL sweep brought back, but only at the tail of
  `session-start-commit.mjs` (not every `Stop`). Default TTL 30 min.
- `auto-capture.mjs` Stop hook guards against post-compact transcript
  shrink (resets `capturedTurnCount` to 0 if `allTurns.length` < cached).
- All commit failure paths preserve state instead of clearing.
- All state writes go through tmpfile + rename for crash safety.

## Open questions / future work

- **Phase 2 resume context inject** (above).
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
