---
name: ov_dream
description: Use when the user explicitly types `ov dream` or `ov recall <query>` and the request should be routed to the OpenViking sync/recall CLI instead of handled as normal chat.
---

# OV Dream

Use this skill for manual OpenViking sync and recall without occupying the OpenClaw `contextEngine` slot.

## When To Use

Use this skill when the user message begins with one of these exact prefixes:

- `ov dream`
- `ov recall `

Do not treat those messages as normal conversation. They are explicit operator commands.

## Commands

- `ov dream`
  Manual sync. Read OpenClaw's `sessions.json`, sync eligible chat transcripts to OpenViking, then commit each session when new messages exist.

- `ov recall <query>`
  Manual recall. Search OpenViking under the default user root URI, `viking://user/default`.

## Sync Behavior

Trigger when the user message is exactly `ov dream`.

Execution flow:

1. Run:

   ```bash
   python3 scripts/dream.py dream
   ```

2. Return the sync summary.

The sync command reads OpenClaw session metadata from `~/.openclaw/agents/main/sessions/sessions.json` when available. It syncs chat-like session keys such as `agent:main:main`, `:direct:`, `:channel:`, `:group:`, and `:room:`.

It must not sync explicitly non-chat sessions, including keys containing `:cron:`, `:heartbeat`, `:subagent:`, `:acp:`, or `:hook:`.

Each source session keeps an independent sync cursor in `~/.openclaw/memory/ov_dream_sync.json`.

## Recall Behavior

Trigger when the user message starts with `ov recall `.

This is a hard routing rule for this skill:

- If the user says `ov recall <query>`, do not answer from general reasoning.
- Do not summarize what recall would do.
- Do not ask whether recall should be run.
- Immediately execute the local recall command.

Execution flow:

1. Extract everything after `ov recall` as the recall query.
2. Run:

   ```bash
   python3 scripts/dream.py recall "<query>"
   ```

3. Return the relevant memory rows to the user.
4. If no memories are found, return `No memories found.`

Rules:

- Treat `ov recall ...` as a manual recall request, not a normal conversation turn.
- Treat the command text after `ov recall` as the exact recall query.
- Run the recall command from the skill directory so `scripts/dream.py` resolves correctly.
- Do not auto-inject retrieved memories into prompt context.
- Do not trigger `ov dream` unless the user separately asks for sync.
- If the query is empty, ask the user for the recall query instead of guessing.

## Notes

- This skill is manual-only in the first version.
- It does not auto-inject recall into prompts.
- It does not replace the OpenViking context-engine plugin.
- Disk-based sync is for recently recorded chat transcripts. It is not a precise "currently running sessions" detector.
- For OpenViking serverless, configure `OPENVIKING_BASE_URL`, `OPENVIKING_API_KEY`, and optionally `OPENVIKING_AUTH_MODE=serverless`. The CLI will use Bearer auth and the serverless session message format automatically.
