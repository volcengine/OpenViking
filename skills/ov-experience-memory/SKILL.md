---
name: ov-experience-memory
description: Retrieve and apply OpenViking Experience memories through the Agent runtime's generic OpenViking search and read tools. Use before or during executable, multi-step, or tool-based work such as coding, file or data changes, configuration, deployment, workflow execution, and failure recovery when prior operational guidance could improve reliability. Do not use for casual chat or simple factual questions.
---

# OpenViking Experience Memory

Use prior task Experience as advisory operational guidance. Keep the current
user request, current environment, and verified tool results authoritative.
Experience retrieval supplements normal context retrieval; it does not replace
user memory, events, preferences, session archives, resources, or Agent Skills.

## Preconditions

- Use only OpenViking tools that are actually registered in the current Agent
  runtime.
- Require both a semantic search capability and an exact URI read capability.
- If either capability is unavailable, continue without Experience. Do not
  invent a tool call, fabricate a ToolPart, or replace the Agent tool call with
  direct HTTP or CLI access.
- Do not substitute broad memory `recall` for a search scoped to the Experience
  root. Continue using the runtime's normal recall and retrieval flows when the
  task also needs user facts, events, decisions, prior conversation, or domain
  resources.

## Select Runtime Tools

Choose the registered names that match the current runtime:

| Runtime | Search | Read |
| --- | --- | --- |
| OpenViking MCP, Codex, Claude Code | `find` or `search` | `read` |
| OpenCode | `openviking_find` or `openviking_search` | `openviking_read` |
| OpenClaw | `ov_search` | `ov_read` or `ov_multi_read` |

The host may display MCP names with a namespace such as
`mcp__openviking__find`. Use the exact registered name and schema shown by the
runtime. Prefer `find` for a fast task-start lookup and `search` when session
context or deeper intent analysis is useful.

## Retrieval Workflow

1. Decide whether the request is an executable task. Retrieve Experience for
   planning, tool use, environment changes, multi-step workflows, or recovery
   from a failed attempt. Skip retrieval for casual conversation and simple
   knowledge answers.
2. Build one concise query containing the task goal, domain object, intended
   operation, and important constraints. After a failure, include the failed
   operation and stable error signature.
3. Search only the current user's Experience root:

   ```text
   viking://~/memories/experiences
   ```

   For tools using MCP-style parameters, set `target_uri` to this root. For
   OpenClaw `ov_search`, set `uri` to this root. Never hardcode `default`,
   `test`, or another user ID.
4. Start with `limit=5` and the tool's normal score threshold. Judge results by
   task, environment, preconditions, and likely effect; title similarity alone
   is insufficient. If no result is relevant, continue without Experience and
   do not broaden the search to unrelated memory directories.
5. Select only the one to three Experience files likely to change execution.
   Require an exact file URI without a query or fragment. Ignore directories,
   unrelated memory types, and sidecars such as `.abstract.md` and `.overview.md`.
6. Read every selected canonical `viking://.../memories/experiences/...` URI
   with the runtime's OpenViking read tool. Search abstracts help selection but
   are not a substitute for reading the Experience body.
7. Apply relevant steps and checks while executing the task. Do not repeat the
   Experience verbatim to the user unless its content is directly needed in the
   answer.
8. If execution fails for a materially new reason, perform at most one focused
   follow-up search using the failure evidence, then read only newly relevant
   Experience files.

## Applying Retrieved Experience

- Treat Experience as reusable procedure, not as a user profile, user intent,
  security policy, or proof that an action succeeded.
- Follow priority in this order: system and developer instructions, current
  user request, current environment and tool evidence, then Experience.
- Ignore stale, incompatible, unsafe, or conflicting guidance. Verify commands,
  paths, APIs, versions, and destructive actions against the current task.
- Preserve confirmation requirements and permission boundaries. Prior success
  never authorizes a destructive or external action in the current session.
- When multiple Experience files conflict, prefer the one whose preconditions
  match the current environment; otherwise proceed conservatively and surface
  the ambiguity when it affects the user.

## Session Evidence

Use real Agent tool calls so the committed OpenViking session retains their
ToolParts:

- A completed generic OpenViking `find`, `search`, or `list` result containing
  an Experience URI records recall for that Experience.
- A completed generic OpenViking `read` or `multi_read` of an Experience URI
  records injection and can associate the resulting trajectory with that
  Experience.
- Failed, cancelled, or incomplete calls do not count.

Do not edit, summarize away, or synthesize these ToolParts before the session
is committed.

## Example

For a request to fix a deployment failure:

1. Search the Experience root with a query such as
   `Kubernetes deployment image pull failure private registry`.
2. Read the most relevant exact Experience URI.
3. Check that its registry, credential, and rollout assumptions match the
   current cluster.
4. Apply the compatible diagnostic steps, verify the live result, and continue
   the user's task.
