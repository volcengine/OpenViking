# Import Local Agent Logs (openviking-server ingest)

`openviking-server ingest` parses the conversation logs that AI coding / agent harnesses (Claude Code, Codex, OpenCode, Hermes, OpenClaw) already leave on your machine, then "replays" them through OpenViking's existing session pipeline (`create session → batch add messages → commit`, where commit triggers memory extraction). This turns both your historical and newly written conversations into long-term memory. It complements the per-harness memory plugins: a plugin captures **while a conversation is happening**, whereas this tool is for **importing existing logs** and **watching for new logs offline** — no plugin required and no change to the harness itself.

Key difference from the plugins: this tool is an OpenViking **client**. It runs where the logs live and points at a local or remote server via the SDK, and it is **off by default** — installing OpenViking does not silently scan your local files.

Source: [openviking/ingest](https://github.com/volcengine/OpenViking/tree/main/openviking/ingest)

## Off by default

The feature is doubly disabled and must be turned on explicitly:

- the master switch `ingest.enabled` defaults to `false`;
- each harness's `enabled` defaults to `false`, and harnesses you do not list are never read;
- backfill of existing logs is a manual command and supports `--dry-run` (count only, write nothing) and `--since` (bound the time window) so you can verify first.

## Supported harnesses

| harness | Status | Default log path | Notes |
|---|---|---|---|
| `claude_code` | Supported | `~/.claude/projects/*/*.jsonl` | append-only JSONL, byte-offset cursor |
| `codex` | Supported | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | append-only JSONL |
| `hermes` | Supported | `~/.hermes/sessions/*.jsonl` | group-chat agent; user peer = original username |
| `openclaw` | Supported | `~/.openclaw/agents/*/sessions/*.jsonl` | group-chat agent; user peer = original username |
| `opencode` | Experimental | `~/.local/share/opencode/opencode.db` | SQLite, polled by `(time, id)`; the legacy file-store is not supported |
| `cursor` | Deferred | `~/Library/Application Support/Cursor/User/**/state.vscdb` | undocumented, version-unstable KV blobs; not yet implemented |

> "harness" (agent framework) here means a whole tool like Claude Code or Codex — distinct from OpenViking's "tool" (tool-call) concept.

## Enable in ov.conf

Add an `ingest` section to `ov.conf`, listing the harnesses to import and their mode:

```json
{
  "ingest": {
    "enabled": true,
    "server_url": "$OPENVIKING_URL",
    "api_key": "$OPENVIKING_API_KEY",
    "account": "default",
    "user": "default",
    "harnesses": {
      "claude_code": { "enabled": true, "mode": "both" },
      "codex":       { "enabled": true, "mode": "backfill" },
      "opencode":    { "enabled": false, "mode": "watch", "experimental": true },
      "hermes":      { "enabled": false, "mode": "both", "user_field": "sender" },
      "openclaw":    { "enabled": false, "mode": "both", "user_field": "sender" }
    }
  }
}
```

- `mode`: `off` | `backfill` (one-shot import of existing logs) | `watch` (incremental) | `both`.
- `paths`: override the harness's default discovery roots (multiple allowed).
- `user_field`: for group-chat harnesses, the log key holding the original username, used as the user-side peer_id.
- `commit`: commit policy — `commit_token_threshold`, `commit_idle_seconds`, `keep_recent_count`.
- Deploy-time toggles can also be overridden via env: `OPENVIKING_INGEST_ENABLED`, `OPENVIKING_INGEST_SERVER_URL`, `OPENVIKING_INGEST_API_KEY`.

When `server_url` is empty it falls back to `OPENVIKING_URL` or `http://localhost:1933`, so it can target either a local or a remote server.

## Usage

The `openviking-server ingest` command is installed together with OpenViking.

```bash
# Show registered harnesses and their config
openviking-server ingest list-sources

# Dry run first: count the sessions / messages that would be replayed, write nothing
openviking-server ingest backfill --dry-run

# Backfill one harness, only sessions started on/after a date
openviking-server ingest backfill --harness claude_code --since 2026-06-01

# Backfill existing logs for real
openviking-server ingest backfill

# Watch for new logs and replay incrementally (blocks in the foreground)
openviking-server ingest watch --harness claude_code

# Honor each harness's configured mode: backfill then watch
openviking-server ingest run

# Show how far each session has been ingested (read the cursor state)
openviking-server ingest status
```

`--reset` deletes and recreates the OV session before replaying. Without `--reset`, re-running is idempotent — the cursor store guarantees nothing is appended twice.

## peer_id

Every message carries a peer_id so OpenViking can profile both the human and the model:

- assistant turns: `{harness}/{model}` (or `{harness}/{provider}/{model}` when the provider is meaningful), e.g. `claude_code/claude-opus-4-8`, `opencode/bytedance_ark/doubao-...`;
- user turns: single-user dev harnesses (claude_code / codex / opencode) use the git identity of the session cwd repo (`user.email` / `user.name`), falling back to the configured `ingest.user` when there is no git repo; group-chat harnesses (hermes / openclaw) use the original username from the log (selected by `user_field`).

Non-ASCII identifiers (e.g. a CJK username) fall back to a valid `ext-<base64>` form.

## How it works

Each harness has a thin adapter that parses its logs into normalized messages and hands them to a replayer that runs `ensure_session → batch add (<=100 per call) → commit`. Memory extraction only runs on **commit**, server-side. OV session ids are `import__{harness}__{native_session_id}` — deterministic and idempotent.

- **Backfill** enumerates every session, replays from the cursor to the end, then commits once per session.
- **Watch** mirrors OpenViking's own `WatchScheduler`: it uses **interval polling** (not filesystem events) driven by durable cursors, so a missed tick, a sleep, or a restart just reads cursor→end on the next tick and self-heals. JSONL uses a byte-offset cursor (with partial-line / truncation / rotation handling); SQLite uses a `(time, id)` cursor read read-only (WAL-aware).

Cursor state persists in `~/.openviking/ingest/state.db`, so both backfill and watch resume across restarts without re-ingesting.

## Cost and privacy

- Commit triggers memory extraction (an LLM call). Backfilling months of history at once can produce many calls — prefer `--dry-run` first, narrow with `--since`, and enable harnesses in batches.
- Logs may contain sensitive content (credentials, file contents). Use this in a trusted deployment and confirm `server_url` points at the server you intend.
- Tool-call inputs/outputs are dropped as low-value by default; only user / assistant text is ingested.

## See also

- [Overview](./01-overview.md) — the per-harness memory plugins (real-time capture)
- [Deployment guide → CLI](../guides/03-deployment.md#cli) — `ov.conf` / credential configuration
