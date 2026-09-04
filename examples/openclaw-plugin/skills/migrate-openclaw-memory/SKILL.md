---
name: migrate-openclaw-memory
description: >
  Migrate an existing OpenClaw installation's hand-written memory into OpenViking long-term memory.
  Reads `workspace/MEMORY.md` and `workspace/memory/*.md`, decides which OpenViking memory category
  each file belongs to by reading its content, writes them to deterministic URIs, and verifies the
  result. Re-runnable: existing targets are skipped unless the user asks to overwrite.
  Trigger when the user says any of: "migrate my OpenClaw memory", "import my old memory",
  "bring my OpenClaw notes into OpenViking", "I already have memory in OpenClaw",
  "把 OpenClaw 的记忆迁过来", "导入旧记忆", "以前记的东西还能用吗".
  This skill covers hand-written memory files only. Conversation transcripts are handled by the
  server-side ingest pipeline, not by this skill.
version: 2026.8.27
metadata:
  openclaw:
    requires:
      bins:
        - openclaw
  emoji: "📦"
  homepage: "https://github.com/volcengine/OpenViking"
tags:
  - migration
  - memory
  - openviking
  - openclaw
---

# Migrate OpenClaw memory into OpenViking

## What this covers

OpenClaw keeps durable, hand-curated memory as markdown:

- `~/.openclaw/workspace/MEMORY.md` — the main memory file
- `~/.openclaw/workspace/memory/*.md` — per-topic and per-day notes

This skill moves those into OpenViking memory so they are recalled automatically in future sessions.

**Not covered: conversation transcripts.** Those are handled by the server-side ingest pipeline,
which is resumable and batched:

```bash
openviking-server ingest backfill --harness openclaw
openviking-server ingest watch --harness openclaw
```

If the user's real goal is "remember my past conversations", point them at those commands instead
and stop — running this skill will not help.

## Why an agent does this instead of a script

The hard part is not moving bytes, it is deciding what each file *is*. A filename cannot tell you
whether `2026-04-01-project-alpha.md` is a project entity, a solved case, or a day's events. You
have to read it. That judgment is the reason this is a skill.

## Before you start

1. Confirm the OpenViking memory plugin is configured and the server is reachable. If not, run the
   `install-openviking-memory` skill first.
2. Confirm the OpenClaw directory. Default is `~/.openclaw`; ask if it is somewhere else.
3. Tell the user what you are about to do and roughly how many files are involved.

## Step 1 — Discover

List the candidates:

```bash
ls -la ~/.openclaw/workspace/MEMORY.md ~/.openclaw/workspace/memory/*.md 2>/dev/null
```

If nothing exists, say so and stop. Do not invent a source directory.

## Step 2 — Read each file and choose a category

Read every file. Do not classify on filename alone.

Targets are the nine preset children of `viking://~/memories`:

| Category | Put a file here when it is… |
| --- | --- |
| `preferences` | how the user wants things done — style, tone, defaults, standing instructions |
| `entities` | a durable subject: a person, project, service, repo, customer, product |
| `events` | something that happened at a point in time — a log entry, an incident, a meeting |
| `cases` | a concrete problem and how it was resolved, worth reusing later |
| `patterns` | a recurring approach or rule of thumb abstracted from several cases |
| `tools` | how to use a specific tool, command, or API in this environment |
| `skills` | a reusable procedure the agent should be able to follow end to end |
| `trajectories` | a record of how a multi-step task actually unfolded |
| `experiences` | a distilled lesson, including what failed and what to do differently |

Guidance:

- `MEMORY.md` is usually **mixed**. If it contains clearly separable sections — preferences in one
  part, project facts in another — propose splitting it into several memories and say why. Do not
  split silently, and do not split a file that is genuinely one topic.
- When a file fits two categories, prefer the more specific one. `cases` beats `events` when the
  point is the resolution, not the timing.
- When you genuinely cannot tell, use `entities` and flag it in the report rather than guessing
  confidently.
- Skip empty files and pure scratch notes. Say which ones you skipped.

## Step 3 — Build the target URIs

Use the home alias form:

```text
viking://~/memories/<category>/<slug>.md
```

`<slug>` should be readable and stable — derive it from the topic, not from a timestamp, unless the
file really is a dated log. Keep it lowercase with hyphens.

**Do not use `viking://user/memories/...`.** The server classifies that as a resource rather than a
memory, and the uid-less form is rejected outright. The `~` alias is expanded server-side to the
calling user's own namespace.

If two files map to the same URI, disambiguate with a suffix rather than overwriting one with the
other.

## Step 4 — Show the plan and get confirmation

Print the mapping as a table: source file → category → target URI → one-line reason. Ask the user to
confirm or correct it. This is the step where a wrong category is cheap to fix; after the write it
is not.

If the user corrects a category, apply the correction and remember the reasoning for similar files
in the same run.

## Step 5 — Write

For each confirmed file, write the content with the OpenViking write tool using `mode="replace"`.
That mode also creates the file when the target does not exist yet.

Before writing, check whether the target already exists:

- exists, and the user did not ask to overwrite → skip it and record that
- exists, and the user asked to overwrite → write it
- does not exist → write it

If a write fails, record the error and **continue with the remaining files**. One rejected file must
not force the user to redo the whole migration. Deterministic URIs make a rerun safe.

## Step 6 — Verify and report

Read back two or three of the written memories to confirm the content landed intact, and run one
search that should now hit migrated content, for example a distinctive phrase from `MEMORY.md`.

Then report:

- how many were imported, skipped, and failed
- the full source → URI mapping
- any file you were unsure about, and why
- for failures, the error and what to do about it

## Pitfalls

- **Do not migrate transcripts with this skill.** See the ingest commands above.
- **Do not paste file contents into chat** to "show your work" — these are the user's private notes.
  Show the mapping, not the bodies.
- **Do not rewrite or summarize content during migration.** Move it verbatim. Rewriting loses the
  user's own wording, which is often the point of a hand-written memory.
- Large files still migrate fine, but if one file is enormous and covers many topics, splitting it
  is usually better than storing one giant memory that always recalls in full.
