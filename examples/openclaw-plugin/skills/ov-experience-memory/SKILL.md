---
name: ov-experience-memory
description: >
  Use OpenViking experience memories during task execution. Search relevant
  experiences with search_experience, read selected experiences with
  read_experience, and leave standard tool parts in the committed session so
  OpenViking can report recall and injection usage.
version: 2026.8.7
tags:
  - openviking
  - experience-memory
  - agent-memory
  - usage-reporting
---

# OpenViking Experience Memory

Use this skill when the current user request starts or continues an executable
task, especially tasks involving tools, files, code changes, data operations,
workflow decisions, or multi-step actions.

Do not use this skill for casual chat, pure explanation, or one-off factual Q&A
that does not require operational guidance.

## Runtime Contract

The agent runtime must expose two tools with these exact names:

- `search_experience`
- `read_experience`

Both are served by the OpenViking server's MCP endpoint, so any runtime
connected to an up-to-date OpenViking server already has them. If neither tool
is listed, the connected server predates them — upgrading the server is the
fix; the plugin ships no local fallback. A harness that namespaces MCP tools
(for example Claude Code's `mcp__openviking__search_experience`) is fine —
usage reporting strips the namespace prefix.

OpenViking usage reporting recognizes only completed tool parts with these tool
names. Calls to generic `find`, `search`, `read`, `ov_search`, or `ov_read` do
not count as experience recall or injection events.

## Tool: search_experience

Purpose: search reusable execution experiences from the OpenViking experience
library before assembling task context.

Input schema:

```json
{
  "query": "string",
  "limit": 5
}
```

Output schema:

```json
{
  "results": [
    {
      "uri": "viking://user/<current_user_id>/memories/experiences/example.md",
      "title": "example",
      "score": 0.82,
      "snippet": "Short summary or matched situation"
    }
  ]
}
```

Implementation:

The server pins the search to the authenticated user's
`viking://user/<current_user_id>/memories/experiences/` and applies no score
threshold. Callers provide only `query` and optional `limit` (clamped to
`[1, 20]`, default `5`); they cannot pass or override `target_uri`. Every
returned `uri` is canonical and owned by the current user — never construct one
with a hardcoded `default` or another user ID. Internal sidecar files
(`.abstract.md`, `.overview.md`, `.relations.json`) never appear in results, and
`snippet` is truncated to 120 characters.

Usage reporting:

A completed `search_experience` tool part is counted as an experience recall
event for every `results[].uri` value.

## Tool: read_experience

Purpose: read the full Markdown body of a selected experience and inject it into
the agent prompt as task execution guidance.

Input schema:

```json
{
  "uri": "viking://user/<current_user_id>/memories/experiences/example.md"
}
```

Output schema:

```json
{
  "uri": "viking://user/<current_user_id>/memories/experiences/example.md",
  "content": "Experience Markdown body"
}
```

Implementation:

Always pass the canonical URI exactly as `search_experience` returned it. The
server rejects anything else — another user's URI, a sidecar file, or an aliased
form carrying a `?` or `#` suffix — with a tool error rather than an empty
result, so a rejected call is never counted as an injection. The returned
content should be inserted into the prompt as operational guidance, not as user
profile facts.

Usage reporting:

A completed `read_experience` tool part is counted as an experience injection
event for `tool_input.uri` or `tool_output.uri`. In this design, reading an
experience through `read_experience` means the experience was injected into the
prompt.

## Recommended Flow

1. When a task begins, build a short query from the latest user instruction,
   current plan, active skill name, and important tool/environment context.
2. Call `search_experience` before final prompt assembly.
3. Review returned titles/snippets and select only experiences likely to affect
   execution.
4. Call `read_experience` for selected experience URIs.
5. Inject the returned Markdown into the prompt under an explicit experience
   section.
6. Continue task execution.
7. Commit the session normally. The committed session must include the
   `search_experience` and `read_experience` tool parts so OpenViking can report
   usage.

## Prompt Injection Format

Use a compact and explicit block:

```text
<openviking-experience-memory>
The following guidance was retrieved from prior task execution experience.
Use it as operational guidance. Do not treat it as user identity or preference.

<experience uri="viking://user/<current_user_id>/memories/experiences/example.md">
...experience markdown...
</experience>
</openviking-experience-memory>
```

## Commit Requirements

The session committed to OpenViking must preserve tool parts with:

- `tool_name`
- `tool_status`
- `tool_input`
- `tool_output`
- `tool_id`

Only `tool_status == "completed"` is counted. Failed, cancelled, or skipped tool
parts are ignored by usage reporting.
