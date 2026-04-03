# OpenViking Claude Memory Plugin (Scheme B)

Claude Code memory plugin built on **OpenViking Session memory**.

- Session data is accumulated during a Claude session (`Stop` hook).
- At `SessionEnd`, plugin now queues a detached commit worker instead of blocking Claude exit on `session.commit()`.
- Memory recall is handled by `memory-recall` skill.

## Design choices in this version

- Mode: **auto switch**
  - Try HTTP first (from `./ov.conf` `server.host` + `server.port`, health check `/health`)
  - Fallback to embedded local mode if server is unreachable
- Config: **strict**
  - Must have `./ov.conf` in project root
- Plugin state dir: `./.openviking/memory/`
  - Session state: `./.openviking/memory/session_state.json`
  - Detached commit queue: `./.openviking/memory/pending/`
  - Detached commit logs: `./.openviking/memory/logs/`

## Structure

```text
examples/claude-memory-plugin/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   ├── common.sh
│   ├── session-start.sh
│   ├── user-prompt-submit.sh
│   ├── stop.sh
│   └── session-end.sh
├── scripts/
│   ├── ov_memory.py
│   └── run_e2e_claude_session.sh
└── skills/
    └── memory-recall/
        └── SKILL.md
```

## Hook behavior

- `SessionStart`
  - Validate `./ov.conf`
  - Auto-detect backend mode (http/local)
  - Create a new OpenViking session and persist plugin state
- `UserPromptSubmit`
  - Adds lightweight hint that memory is available
- `Stop` (async)
  - Parse transcript last turn
  - Summarize turn (uses `claude -p --model haiku` when available; fallback to local summary)
  - Append user + assistant summary to OpenViking session
  - Deduplicate by last user turn UUID
- `SessionEnd`
  - Queue a detached commit worker so `/exit` is not blocked on remote extraction
  - Persist `pending_commit_file`, `pending_commit_log`, `commit_requested_at`, and `commit_in_progress`
  - Finish the real `session.commit()` in the background and write the final result into the pending state/log

## SessionEnd behavior

The plugin now treats `SessionEnd` as a queueing step, not a long blocking RPC.

- The hook returns quickly with a status like `session commit queued`
- A detached Python worker continues the real OpenViking commit after Claude exits
- The live session state flips to `active=false` immediately so exit is not held open
- The background worker records final outcome in the pending state file and log

If you want to inspect a commit after exit, check:

- `./.openviking/memory/session_state.json`
- `./.openviking/memory/pending/<session-id>.json`
- `./.openviking/memory/logs/session-end-<session-id>-<timestamp>.log`

## Skill behavior

`memory-recall` runs bridge command:

```bash
python3 .../ov_memory.py recall --query "<query>" --top-k 5
```

It searches:

- `viking://user/memories/`
- `viking://agent/memories/`

Then returns concise, source-linked memory summaries.

## One-click E2E

Run a real Claude headless session end-to-end using source config:

```bash
cd /Users/quemingjian/.codex/worktrees/6e45/OpenViking
bash /Users/quemingjian/.codex/worktrees/6e45/OpenViking/examples/claude-memory-plugin/scripts/run_e2e_claude_session.sh
```

Custom source config and prompt:

```bash
bash /Users/quemingjian/.codex/worktrees/6e45/OpenViking/examples/claude-memory-plugin/scripts/run_e2e_claude_session.sh \
  /Users/quemingjian/Source/OpenViking/ov.conf \
  "请只回复: CUSTOM_E2E_TOKEN"
```

What the script does:

- Creates a Python 3.11 virtual environment under `/tmp` (one-time dependency install).
- Generates a temporary project `./ov.conf` from source config and injects HTTP server fields.
- Starts OpenViking HTTP server, runs a real `claude -p` session with this plugin, then triggers deterministic Stop + SessionEnd validation.
- Verifies `session_state.json`, `ingested_turns >= 1`, and session archive file creation.
- Also tolerates the detached SessionEnd path by waiting briefly for the queued commit to finish.
- Restores original `./ov.conf` when done.

## Resolved issues

- **Health check timeout:** `_health_check()` uses 1.5s timeout (down from 3.5s). Offline detection completes in ~2.7s total.
- **SessionStart hook:** Runs synchronously with 5s timeout. Subagent sessions are filtered (exit immediately on `agent_id`). Recall is skipped when server is offline.
- **Offline buffering:** When the OV server is unreachable, turns are buffered locally and replayed on reconnect.

## Notes

- This MVP does not modify OpenViking core.
- If `./ov.conf` is missing, hooks degrade safely and report status in systemMessage.
- Primary state file: `./.openviking/memory/session_state.json`
- Detached SessionEnd state: `./.openviking/memory/pending/`
- Detached SessionEnd logs: `./.openviking/memory/logs/`
