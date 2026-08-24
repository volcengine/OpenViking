---
name: openviking-memory
description: Recall and persist long-term memory through the OpenViking MCP tools. Use at the start of any substantive task (coding, configuration, debugging, multi-step or tool-based work) to retrieve relevant prior knowledge with find/search/read, and during or after work to persist durable facts, preferences, decisions, and lessons with remember. Do not use for casual chat or simple factual questions the model can answer directly.
---

# OpenViking Memory

OpenViking is a long-term semantic memory store addressed by `viking://` URIs.
This client has no lifecycle hooks, so nothing is recalled or captured
automatically — you drive both halves of the loop with the `openviking` MCP
tools.

Core tools, available on every supported deployment:

- Recall: `find`, `search`, `read`, `list`, `grep`, `glob`
- Persist: `remember`, `add_resource`
- Maintain: `forget`, `health`

Some deployments register more than the core set — `tree`, `write`, `edit`,
`list_watches`, `cancel_watch`. These are optional: which ones exist depends
on the server version and hosting mode (the managed cloud service trims some
of them). Check the session's registered tool list; if any optional tool is
present, read [references/optional-tools.md](references/optional-tools.md)
before using it. Never call a tool that is not registered, and do not fall
back to raw HTTP. If no OpenViking tools are registered at all, continue
without memory.

## Recall: at task start

1. Decide whether the request warrants memory. Retrieve for executable or
   multi-step work, anything touching a system you may have seen before, and
   recovery from failures. Skip retrieval for small talk and one-off trivia.
2. Build one concise query from the task goal, domain objects, intended
   operation, and constraints. After a failure, include the failed operation
   and the stable part of the error message.
3. Call `find` (fast, ranked results with URI + abstract + score) with
   `limit` around 5-10. Use `search` when deeper intent analysis helps, or use
   `search` with `mode="context"` for a server-assembled, token-budgeted
   context block. In list mode, scope with `target_uri` when you know where to look, e.g.
   `viking://~/memories/experiences` for prior task experience.
4. Judge results by task and environment fit, not title similarity. `read` the
   one to three exact file URIs likely to change how you execute. Ignore
   sidecar files such as `.abstract.md`, `.overview.md`, and
   `.relations.json`.
5. If nothing relevant comes back, proceed without memory. Make at most one
   focused follow-up search when execution fails for a materially new reason.

Treat retrieved memory as advisory. Priority order: system and developer
instructions, the current user request, current environment and tool evidence,
then memory. Verify commands, paths, and versions against the present task;
prior success never authorizes a destructive action now.

## Persist: during and after work

Because capture is not automatic here, durable information is lost unless you
store it. When you encounter something worth keeping, persist it in the same
session:

- `remember(messages)` — the default. Pass the key exchange or a short factual
  summary as role-tagged messages; the server extracts and files memories
  (preferences, entities, events, experience) on its own. Use it when the user
  says "remember this", states a lasting preference or decision, or when a
  hard-won lesson (root cause, working procedure, environment quirk) emerges.
- `add_resource` — to import external documents or URLs as searchable
  resources.
- When you need an exact document at a known location (curated notes under
  `viking://~/` — your own user root — or shared reference material under
  `viking://resources/`), the optional `write` / `edit` tools cover that — see
  [references/optional-tools.md](references/optional-tools.md). If they are
  not registered, fall back to `remember`.

What to persist: stable preferences and conventions, environment facts,
decisions with their rationale, and reusable procedures or fixes. What not to
persist: secrets and credentials, transient state, speculation, or bulk
transcript dumps — store conclusions, not scrollback.

## Example

User asks to fix a failing deployment:

1. `find` with query `deployment image pull failure private registry`,
   `target_uri: "viking://~/memories/experiences"`.
2. `read` the most relevant experience URI; check its assumptions against the
   current cluster before applying its steps.
3. Fix the issue, verify the live result.
4. `remember` a short summary of the root cause and the working fix so the
   next session can recall it.
