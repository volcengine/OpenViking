---
name: ov_dream
description: Use when the user explicitly types `ov dream` or `ov recall <query>` and the request should be routed to the local OpenViking sync/recall CLI instead of handled as normal chat.
---

# OV Dream

Use this skill only for manual OpenViking validation without occupying the OpenClaw `contextEngine` slot.

## When To Use

Use this skill when the user message begins with one of these exact prefixes:

- `ov dream`
- `ov recall `

Do not treat those messages as normal conversation. They are explicit operator commands.

## Commands

- `ov dream`
  Manual sync. Read the active OpenClaw session, upload new `user` and `assistant` messages to OpenViking, then commit when new messages exist.

- `ov recall <query>`
  Manual recall. Search OpenViking memories under `viking://user/memories`.

## Mode 3: Recall

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
