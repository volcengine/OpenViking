# Claude Code × OpenViking Memory Hooks

Auto-extract memories from [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions into OpenViking using [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks).

## How It Works

Three hooks capture conversation transcripts at strategic lifecycle points and pipe them into OpenViking's memory system:

| Hook | Trigger | What it does |
|------|---------|--------------|
| `SubagentStop` | A subagent finishes | Extracts the subagent's transcript and saves it as a memory |
| `PreCompact` | Before context compaction | Snapshots the conversation before details are summarized away |
| `SessionEnd` | Session terminates | Archives the full session via structured `ov session` workflow |

```
Claude Code Session
       │
       ├── SubagentStop ──→ ov add-memory <messages>
       │
       ├── PreCompact ────→ ov add-memory <messages>
       │
       └── SessionEnd ────→ ov session new
                              → ov session add-message × N
                              → ov session commit
```

`SubagentStop` and `PreCompact` use `ov add-memory`, which accepts a batch of messages in a single call — ideal when you already have the full transcript in hand and want to minimize client-server round trips.

`SessionEnd` uses `ov session add-message` per message, then commits at the end. This is intentionally kept as a reference pattern: if you integrate hooks that fire incrementally (e.g. `UserPromptSubmit` or `Stop`), you'd call `ov session add-message` after each turn to build up the session over time — without committing on the spot — and only commit when the session is complete.

All hooks run **async** (non-blocking) and the slow LLM extraction step runs in a `nohup` background process so it never delays Claude's responses.

## How Claude Code Hooks Work

When a hook event fires, Claude Code runs the registered shell command and passes event data as **JSON via stdin**. Every hook receives a common base payload plus event-specific fields:

```json
{
  "hook_event_name": "SubagentStop",
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/Users/you/project",
  "permission_mode": "default",
  "agent_type": "general-purpose",
  "agent_transcript_path": "/path/to/subagent.jsonl"
}
```

Hook scripts read this with `INPUT=$(cat)` and parse fields with `jq`. See [Hooks.md](./Hooks.md) for all events and their unique params.

Exit codes control behavior for blocking hooks — exit `0` to allow, `1` to block, `2` to request user confirmation. Hooks registered with `"async": true` (like these) run in the background and their exit code is ignored.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [OpenViking CLI](../../README.md) configured (`~/.openviking/ovcli.conf`)
- `jq` installed (`brew install jq` / `apt install jq`)
- `python3` available (used for unicode-safe content truncation in logs)

## Setup

### 1. Copy hooks

```bash
mkdir -p ~/.claude/hooks
cp hooks/*.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/*.sh
```

### 2. Register in Claude Code settings

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SubagentStop": [
      {
        "hooks": [
          { "type": "command", "command": "$HOME/.claude/hooks/ov-memory-subagent-stop.sh", "async": true }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          { "type": "command", "command": "$HOME/.claude/hooks/ov-memory-pre-compact.sh", "async": true }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "$HOME/.claude/hooks/ov-memory-session-end.sh", "async": true }
        ]
      }
    ]
  }
}
```

### 3. Verify

```bash
claude
/hooks
```

## Debugging

Set `OV_HOOK_DEBUG=1` to enable logging to `/tmp/ov.log`:

```bash
export OV_HOOK_DEBUG=1
claude
```

Then watch the log in another terminal:

```bash
tail -f /tmp/ov.log
```

Log output uses color — gray timestamps, purple `ov` commands — and message content is truncated to 120 characters for readability. The actual data sent to OpenViking is always the full untruncated content.

Example output:

```
[2026-02-22 10:00:01] SubagentStop: queued 4 msgs from general-purpose
2026-02-22 10:00:01  ov add-memory '[{"role":"user","content":"You are a senior backend engineer..."},...]'
[2026-02-22 10:00:03] SubagentStop: saved 4 msgs from general-purpose

[2026-02-22 10:30:00] SessionEnd: queued commit 56 msgs (ov=abc123, reason=other)
2026-02-22 10:30:00  ov session new -o json -c
2026-02-22 10:30:00  ov session add-message --role 'user' --content 'what hooks do you have now' abc123
2026-02-22 10:30:00  ov session commit abc123
```

## Verifying Memories

After hooks fire, check what was extracted:

```bash
ov search "what did I work on today"
```

## Extending to Other Hooks

See [Hooks.md](./Hooks.md) for a quick reference of all 17 Claude Code hook events and the params each provides via stdin. Useful starting points for extending this example:

- `UserPromptSubmit` — add a message to an open `ov session` on every user turn
- `Stop` — commit the session when Claude finishes responding (pairs with `UserPromptSubmit`)
- `PostToolUse` — capture tool results alongside conversation turns

## Customization

- **Skip short sessions**: Add a `$COUNT` threshold check before running `ov` commands
- **Change log file**: Edit `LOG=` in each script (only matters when `OV_HOOK_DEBUG=1`)
- **Add project tags**: Pass metadata via `ov session` flags
