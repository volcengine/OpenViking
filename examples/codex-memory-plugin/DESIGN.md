# Codex memory plugin — commit decision design

This document records *why* the plugin commits when it commits. The commit
shape (which OpenViking session is sealed by which hook event) is the part
worth understanding before reading code: `SessionEnd` gives us a
deterministic end signal for graceful exits, but it does not cover signals,
crashes or older Codex builds, so we still reason about which observable
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
  shape `{ codexSessionId, ovSessionId, transcriptPath, capturedTurnCount, createdAt, lastUpdatedAt }`.
- **End marker** — `<safe-codex-session-id>.ended.<timestamp>`, a sidecar written by the
  `SessionEnd` parent hook, containing the timestamp at which it was
  written. Its presence means "the thread ended and its commit has not been
  confirmed yet"; its timestamp is the token that tells one exit's marker
  from another's, and it lives in the filename so a conditional removal
  targets an immutable path. A bare `<safe-codex-session-id>.ended` is a
  pre-0.8.1 marker and is still read back.
- **Session lock** — `<safe-codex-session-id>.lock`, an exclusive mkdir lock
  serializing the four writers that persist the whole state object. It holds
  an `owner` file so a holder only ever releases its own lock.

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
| `SessionEnd` | graceful thread shutdown: `/quit`, `/exit`, double Ctrl-C, EOF, end of a `codex exec` run | this thread's context is gone; commit it |
| SIGTERM / SIGHUP / terminal close / `kill -9` / crash | process killed | **no hook fires** — tui/exec install no signal handlers |

Verified against codex-rs `main` @ 6be2a6ca, 2026-08-28. `SessionEnd` shipped
in `rust-v0.145.0` and is present in every stable since; older Codex builds
(and TraeCode CLI builds without it) ignore the unknown event name, which is why
the fallback sweep stays.

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

### 2. `SessionEnd` — deterministic, graceful exits

This is the primary commit path. Codex fires `SessionEnd` when a thread
shuts down gracefully: `/quit`, `/exit`, double Ctrl-C, EOF on stdin, and
the end of a `codex exec` run. In a single TUI process every thread the
process touched via `/new` or `/resume` gets its own `SessionEnd`, all in a
burst at process exit — so `/new` on its own does not end the previous
thread; its end event arrives later, when the process leaves.

The rollout is flushed to disk before the hook runs, so the hook sees the
complete transcript. `session-end.mjs` therefore catches up whatever turns
the last `Stop` never sent, then commits the OV session and clears
`ovSessionId` with `touch: false`.

The budget forces the work off the hook's own process. Codex allows 1s by
default, clamps a `timeout` in `hooks.json` to 3s, forces `async: true`
hooks to run synchronously, and ignores stdout — nowhere near enough for a
catch-up append plus a commit. So the parent hook does two cheap things and
exits: write the `.ended` sidecar (lock-free, before anything else, so the
sweep can still recover if the worker never runs) and detach a worker,
regardless of `writePathAsync`. Codex deliberately leaves cleanly detached
helpers running after a hook exits and only kills the process group on a
timeout, and the detached worker holds no inherited stdout/stderr, so Codex
does not wait on it.

`SessionEnd` does **not** fire on SIGTERM, SIGHUP, a closed terminal,
`kill -9`, or a crash. When the TUI is attached to a `codex app-server`
daemon, it is deferred until the thread is unloaded (30 min) or the daemon
shuts down. Those cases fall to the fallback sweep.

### 3. `SessionStart` source=`startup` / `clear` — fallback sweep entry

Triggered by `/new`, `/clear`, fresh codex CLI startup, and zouk daemon
spawn-without-sessionId (including zouk's "reset codex" UI action).

`/clear` creates a brand-new codex `session_id` and orphans the previous
in-memory thread (preserved on disk); `startup` may or may not follow an
exit that already committed. Either way the hook does not try to guess
which session just ended — that is `SessionEnd`'s job. It gates internally
on `source ∈ {startup, clear}` and runs the fallback sweep of rule 5 over
every state file except the new `session_id`.

### 4. `SessionStart` source=`resume` — never commits, optional archive inject

Short reconnects and `/resume` re-fire `SessionStart` for the same
`session_id`. Committing here would seal a still-active session. So
`resume` is a no-op for commit purposes.

All `SessionStart` sources (`startup`, `clear`, and `resume`) independently
load the shared OpenViking profile block unless
`OPENVIKING_NO_AUTO_INJECT=1`. The implementation is the same
`buildProfileBlock()` used by the other coding-agent integrations: full
`profile.md` plus abstract-annotated URI indexes for `preferences/` and
`entities/`, bounded by `OPENVIKING_PROFILE_TOKEN_BUDGET` with the shared
CJK-aware estimator. Profile loading does not alter the commit decision tree.

Resume may still need continuity after `PreCompact` or idle sweep already
committed the live OV session. If local state has `ovSessionId = null`
(or no state file remains), the hook derives `cx-<codex-session-id>`,
calls `GET /api/v1/sessions/{id}/context?token_budget=...`, and injects
`latest_archive_overview` via `hookSpecificOutput.additionalContext` when
present. The injected block includes
`viking://user/sessions/{id}/history/` so the model can use OpenViking MCP
read/search tools for exact prior details. When both profile and archive are
available, they are combined in one `SessionStart` response.

If local state still has a live `ovSessionId`, resume injection is skipped:
the session is appendable and Codex should already be resuming its own
transcript.

### 5. Fallback sweep — everything `SessionEnd` could not reach

On `startup` and `clear`, every state file except the new `session_id` is
examined. A state that still has a live `ovSessionId` is committed for one
of two reasons:

| Reason | Condition | What it means |
|---|---|---|
| `ended_retry` | an `.ended.<timestamp>` sidecar is present | `SessionEnd` fired but its commit never completed: OV was unreachable, `/commit` failed, or the worker was killed (Windows job objects kill detached children) |
| `idle_ttl` | no marker and `lastUpdatedAt` older than `IDLE_TTL_MS` (default 30 min) | no `SessionEnd` was ever going to arrive: signals, crashes, Codex older than 0.145 / TraeCode CLI, app-server deferral, or a mid-turn zouk reset that cancelled `Stop` |

Before committing, the sweep appends whatever the state's recorded
`transcriptPath` still holds past `capturedTurnCount`, so a session whose
own workers never ran is not archived without its tail turns. If part of
that append fails, the sweep keeps `ovSessionId` and the marker and skips
the commit; the next sweep retries from the advanced cursor.

A marker enters the lock even when the state carries no live `ovSessionId`. `PreCompact` releases the id but leaves the cursor and `transcriptPath` behind, so the tail turns of a thread whose `SessionEnd` worker was killed are only reachable through that catch-up — the catch-up derives a live id by itself as soon as it has something to send. Only when the catch-up finds nothing new and there is still no live id is the marker cleared.

An unreadable transcript is never treated as an empty one. If the state records a `transcriptPath` that cannot be read, the sweep logs `transcript_unreadable`, keeps the live id and the marker, and skips the commit, exactly like a partial append — otherwise a transient read failure would archive a session without turns nobody has seen yet. `SessionEnd` and `PreCompact` apply the same guard.

Their transcript cursor is preserved while `ovSessionId` is cleared. Mental
model for the idle case: a session not touched for 30 min is "temporarily
concluded"; if the user resumes later, subsequent turns append under the
same deterministic OV session id, and the next commit creates another
archive there.

Releasing the session id instead of deleting the file writes state without
touching `lastUpdatedAt`, so a committed session doesn't look freshly active
to the next `SessionStart`. A successful commit also removes the `.ended`
marker; a failed one keeps both marker and `ovSessionId` so the next sweep
retries.

Each candidate is committed under its session lock as a **try-lock**: a lock
we cannot take immediately means a `SessionEnd` or `Stop` worker already
owns that session (the user quit and relaunched within seconds), so the
sweep logs the skip and leaves the state alone. State is reloaded inside the
lock before the commit, so the sweep never writes back a snapshot the worker
has since superseded. The `.ended` marker is re-read there too: if an
`ended_retry` candidate's marker is gone (the thread was resumed) or newer
than the snapshot (a later exit whose own worker will commit), the sweep
falls back to the idle rule and leaves anything younger than `IDLE_TTL_MS`
alone.

**Sweep trigger**: at the tail of `session-start-commit.mjs` only. `Stop`
does not sweep; state-write-on-every-turn already gives us the freshness
signal, and once per session start is the right cadence.

**Known limitation**: if a session's `SessionEnd` never fired and the user
never starts another codex on this machine, no sweep ever runs and the OV
session stays open server-side forever. Accepted. Future work could add an
MCP tool `openviking_commit_pending` so the model can commit explicitly.

### 6. Cursor retention — same pass

A committed state file keeps living as a cursor (`ovSessionId: null`) so a
later resume appends instead of replaying. Kept forever that would leak one
file per codex session and make `listStates()` — which reads every file on
every `SessionStart` — slower over time, so the same pass retires them:

| State | Retired after |
|---|---|
| cursor-only, `capturedTurnCount > 0` | `COMMITTED_TTL_MS` (default 30 days) |
| cursor-only, nothing ever captured | `IDLE_TTL_MS` (default 30 min) |

A cursor that outlives its codex rollout has nothing left to resume from, and
a state file that never captured a turn is identical to no state file at all.

## Stop hook — append + threshold commit

Every `Stop` reads `transcript_path`, slices to `[capturedTurnCount, end)`,
and appends each new user/assistant turn to the OV session for this codex
`session_id` (the `/messages` endpoint auto-creates it on first append).
State is updated:
`{ovSessionId, capturedTurnCount, lastUpdatedAt: now}`.

After a successful append, Stop reads session meta and commits when
`pending_tokens >= OPENVIKING_COMMIT_TOKEN_THRESHOLD` (default 20000).
The threshold commit passes
`keep_recent_count=OPENVIKING_COMMIT_KEEP_RECENT_COUNT` (default 10) so
the newest turns stay live after archive/extract. This keeps long-running
sessions from waiting until PreCompact/SessionStart while still avoiding
commit-on-every-turn fragmentation.

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
math underflows and we silently drop new turns. When this inequality is
detected on `Stop`, move `capturedTurnCount` to the latest human turn
so the current interaction is captured without replaying compacted history.

"Human turn" is not just `role === "user"` — `normalizeCaptureRole()` maps
tool results onto the user role as well, so `findLastHumanTurnIndex()`
additionally requires a `text` part. If the rewrite left no human turn at
all (index `-1`), we fall back to capturing the whole transcript and log
`fallback: "full_transcript"`; replaying beats losing the interaction.

**Trade-off**: turns older than that human turn are assumed captured. If
earlier `Stop` hooks failed to reach OV and compaction happened before they
were retried, those turns are dropped rather than duplicated.

### Commit failure

When OV `/commit` returns non-2xx or times out, we log the `trace_id` and
keep `ovSessionId` set. We must NOT call `clearState` on failure — keep the
state file, and in the `SessionEnd` path keep the `.ended` marker too, so
the next sweep retries. A transient OV outage shouldn't lose a session's
worth of memory. An unreadable transcript is handled the same way: no
commit, state and marker preserved.

### Race: exit before Stop completes

Codex's tokio runtime cancels in-flight async tasks when the process goes
away, so the last turn's `Stop` hook may be aborted before it appends its
turns and bumps `lastUpdatedAt`. On a graceful exit this costs nothing: the
`SessionEnd` worker reads the flushed rollout and appends everything past
the cursor before committing. On a signal or a crash there is no
`SessionEnd`, the state looks older than it really is, and the idle TTL
sweep commits it at the next `SessionStart` — with the same catch-up, from
the `transcriptPath` and cursor the last completed `Stop` recorded. A
session whose very first `Stop` never completed has no recorded transcript,
so there is nothing for the sweep to catch up and it commits whatever the
OV session already holds.

### Race: concurrent writers of the same state file

The `Stop` worker, `PreCompact`, the `SessionEnd` worker and the sweep all
persist the whole state object, so without serialization the last writer
wins and can resurrect a committed `ovSessionId` or rewind the cursor. All
four therefore run under `withSessionLock`, and all four load state *inside*
the lock. The lock is a directory (`mkdir` is atomic on every platform we
run on); a holder that dies leaves a lock that is abandoned once its mtime
is `staleMs` (5 min) old, and a live holder refreshes the mtime from the
batch-send callback so a long catch-up never looks stale. Wait budgets:
120s for the `Stop` and `SessionEnd` workers, 40s for `PreCompact` (which
must still answer inside its 60s hook budget, and on timeout emits `{}` and
touches nothing), and 0 for the sweep.

Ownership makes the lock safe to abandon. The holder writes an `owner` file
inside the directory containing `<pid>:<uuid>`, and only releases (or
refreshes) a lock whose `owner` still matches its own — otherwise a taker
that lost a race would release a lock somebody else now holds. Takeover
happens in place, on that same `owner` file: the taker renames it aside and
then creates its own exclusively, two atomic steps that exactly one racer can
complete, and a racer that loses either one leaves the winner's lock intact.
The directory itself is never moved or removed during a takeover, because a
lock path that is momentarily absent would let another racer's `mkdir` succeed
alongside the taker.

### Race: a marker and a worker that outlive each other

The `.ended` marker's timestamp is its identity. The `SessionEnd` parent
passes the timestamp it wrote to the detached worker through
`OPENVIKING_SESSION_END_TOKEN`; the worker, after taking the lock and before
any network call, re-reads the marker and returns without committing unless
it still matches — a cleared marker means the thread was resumed, a
different one belongs to a newer exit whose own worker will commit. A
commit only clears the marker it verified. Because each marker's timestamp
is part of its filename, a conditional removal enumerates the markers older
than its cutoff and unlinks exactly those paths — a marker written between
the enumeration and the unlink is a different file and survives untouched.

`Stop` and `PreCompact` clear the `.ended` marker at entry, because a turn
for this session proves the thread is alive again after a resume. They clear
it only if it is older than the hook's own start time, so a detached `Stop`
worker that boots after the next exit cannot erase that exit's fresh marker;
the `Stop` parent forwards its start time to its worker through
`OPENVIKING_HOOK_STARTED_AT`. `SessionStart` `source=resume` clears with its
own start time for the same reason.

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
  "ovSessionId": "cx-0193af...-or-null", // null means "committed, awaiting next Stop or retirement"
  "transcriptPath": "/path/rollout.jsonl", // last rollout seen; lets the sweep catch up
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

Two sidecars live next to `<safe-codex-session-id>.json`:

| Path | Written by | Meaning |
|---|---|---|
| `<safe-id>.ended.<timestamp>` | `SessionEnd` parent hook (timestamp in the name, also the content; created exclusively, and bumped by a millisecond until that succeeds, so two exits in the same millisecond still get distinct markers) | the thread ended; its commit is not confirmed. Read back by `listStates()` as `endedAt`, which takes the largest timestamp when several markers exist. Removed by a commit that verified this exact timestamp, or by a `Stop` / `PreCompact` / `resume` that started after it was written — each removal unlinks the exact marker paths older than its cutoff, so it can never take out a newer exit's marker. A bare `<safe-id>.ended` (pre-0.8.1) is still honoured, with the timestamp read from its content |
| `<safe-id>.lock` | whichever writer currently holds the session | exclusive `mkdir` lock holding an `owner` file; abandoned when its mtime is older than 5 min, and taken over in place by claiming that `owner` file |

Keeping the end marker out of the JSON is deliberate: a whole-object
`saveState` from a concurrent worker cannot clobber a separate file, and the
parent hook can write it lock-free in about a millisecond. Neither sidecar
is picked up by `listStates()` or the doctor, which read `.json` only.

## Configuration

Env var overrides for tuning without rebuilding:

| Var | Default | Purpose |
|---|---|---|
| `OPENVIKING_CODEX_STATE_DIR` | `~/.openviking/codex-plugin-state` | state file dir |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` (30 min) | idle sweep TTL |
| `OPENVIKING_CODEX_LOCK_WAIT_MS` | `120000` (`SessionEnd` worker), `40000` (`PreCompact`) | how long a writer waits for the session lock |
| `OPENVIKING_CODEX_COMMITTED_TTL_MS` | `2592000000` (30 days) | how long a committed cursor is kept for resume |
| `OPENVIKING_RECALL_TIMEOUT_MS` | `120000` (2 min) | whole UserPromptSubmit auto-recall deadline |
| `OPENVIKING_RECALL_COMPRESS` | `1` | set `0` / `off` to skip `codex exec` compression |
| `OPENVIKING_RECALL_COMPRESS_MODEL` | unset | custom first-choice compressor model; `off` disables compression |
| `OPENVIKING_RECALL_COMPRESS_THINKING` | unset | custom `model_reasoning_effort`; `default` means omit override; alias `OPENVIKING_RECALL_COMPRESS_REASONING_EFFORT` |
| `OPENVIKING_RECALL_COMPRESS_BASE_URL` | unset | custom API base URL for the nested `codex exec` compressor |
| `OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP` | `1` | recreate/cache compressor profile during every `SessionStart` |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS` | `15000` | per-candidate compressor probe timeout |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS` | `604800000` (7 days) | cache TTL used by `UserPromptSubmit` reads |
| `OPENVIKING_RESUME_ARCHIVE_INJECT` | `1` | inject latest archive summary on `source=resume` when no live OV session is open |
| `OPENVIKING_RESUME_ARCHIVE_TOKEN_BUDGET` | `32000` | token budget for `/sessions/{id}/context` on resume |
| `OPENVIKING_RESUME_ARCHIVE_MAX_CHARS` | `6000` | max chars injected from latest archive overview |
| `OPENVIKING_CAPTURE_TOOL_MAX_CHARS` | `1000000` | guard cap on one tool part's `tool_output`; the server externalizes anything over `tool_output_externalization.threshold_chars` (default 20000) |
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

The compressor runs with `--ignore-user-config`, so it does not inherit the
main Codex process's provider table. When
`OPENVIKING_RECALL_COMPRESS_BASE_URL` is set, the plugin adds an isolated
provider for the nested request:

```bash
-c 'model_provider="openviking_compressor"' \
-c 'model_providers.openviking_compressor.name="openviking_compressor"' \
-c 'model_providers.openviking_compressor.base_url="<url>"'
```

The selected model and thinking effort remain profile data; the base URL is
runtime configuration and is supplied when the command is built.

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
3. `gpt-5.6-luna`, thinking `low`
4. off (deterministic digest, no child `codex exec`)

Configured `off` (`OPENVIKING_RECALL_COMPRESS=0`, model `off`, or thinking
`off`) skips all probing and writes a disabled profile.

## What changed vs 0.7.x

- `SessionEnd` (Codex ≥ 0.145) is registered and is now the primary commit
  path; `session-end.mjs` marks the session ended, then a detached worker
  catches up missed turns and commits.
- The active-window heuristic and `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` are
  gone. `SessionStart` `startup|clear` now only sweeps: `ended_retry` for
  states with an `.ended` marker, `idle_ttl` for states past the idle TTL.
- A per-session `.lock` directory serializes the `Stop` worker,
  `PreCompact`, the `SessionEnd` worker and the sweep, with a new
  `OPENVIKING_CODEX_LOCK_WAIT_MS` wait budget and a try-lock for the sweep.
- The `.ended` sidecar is cleared by any `Stop`, `PreCompact` or
  `source=resume` for that session.
- `ov-session.mjs` holds the OV HTTP client, transcript reader and catch-up
  logic that `Stop`, `PreCompact` and `SessionEnd` used to duplicate;
  `PreCompact` gains the post-compact shrink defense it lacked, and an
  unreadable or empty transcript can no longer reset the cursor to 0.
- Behaviour change: after `/new` the abandoned thread is committed when the
  process exits (the `SessionEnd` burst) or after the idle TTL — no longer
  at the next `SessionStart`.

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

// SessionEnd input (Codex >= 0.145; `reason` is a constant)
{
  "session_id": "0193af...",
  "transcript_path": "/path/to/rollout.jsonl",
  "cwd": "/path/to/cwd",
  "hook_event_name": "SessionEnd",
  "reason": "other"
}
```

Output schema for SessionStart / UserPromptSubmit supports
`hookSpecificOutput.additionalContext`. A SessionStart response may include
that field together with `systemMessage`, allowing profile injection and orphan
commit status to coexist. Stop / PreCompact only support
`{ continue, stopReason, suppressOutput, systemMessage }` — `{}` is a
valid no-op. SessionEnd output is ignored entirely; the hook cannot block
and cannot inject, so `session-end.mjs` prints `{}` for symmetry.
