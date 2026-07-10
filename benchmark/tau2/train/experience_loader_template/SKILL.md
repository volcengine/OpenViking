---
name: experience_loader
description: Load relevant OpenViking experience memories via case-linked experience candidates before solving a task.
---

# experience_loader

Use this skill before taking task actions. It searches for reusable execution experiences,
filters by the short `situation` snippet, then loads the full applicable experience.

## Required workflow

1. Before taking task actions, call `search_experience` with a natural-language query that describes the current task.
2. Build the query from the current domain, user intent, target object, requested operation, policy keywords, and likely tool/action family. Avoid vague queries such as "help user".
3. Review the returned candidates. Each candidate has a `case_name` and linked experience entries; each experience entry has an experience `uri` and a short `situation` snippet describing applicability and exclusions.
4. Treat `situation` as a filter only, not as the full experience. Do not rely on the snippet as sufficient guidance.
5. **Gate before reading.** For each linked experience, use its `situation` snippet to decide whether it may apply to the current task, either now or at a later boundary in the same task (for example before confirmation, before write, after write, or final response). Skip reading only when the situation explicitly excludes this task/action family/object/policy branch (for example wrong object type, wrong action family, already-completed state, or "does not apply" / "不适用").
6. If an experience is not explicitly excluded and may apply to any current or later task boundary, you MUST call `read_experience` with that experience URI before taking task actions that could reach that boundary. If multiple experiences pass this gate, read each applicable experience.
7. You may call `search_experience` multiple times with refined keywords. After each search, apply the gate above and read applicable experience URIs.
8. Treat loaded experiences as reusable guidance, not as current-task truth. Current policy, current tool results, and current user facts override prior experience.
9. **Re-verify after reading.** Even after `read_experience`, before acting on the experience, check its full `## Situation` against current facts you have obtained from tools (cabin class, reservation status, flight dates, segment state, etc.). If any "不适用于" / exclusion condition matches the current task now that you have concrete facts, DISCARD the experience and proceed from policy and tool results instead — do NOT apply its Approach or Reflect.
10. Multi-intent tasks (e.g. "cancel, then book", "upgrade then change flight", "refuse a modification then offer a fallback") may legitimately require more than one experience; gate and apply each segment's experience independently. Do not end the task (`done` / `transfer_to_human_agents`) just because one segment's experience reaches a local return marker — check whether the user has a remaining intent.
11. If no linked experience is plausibly relevant after gating, continue without experience guidance.

## Local return markers in loaded experiences

Experience return markers are **local to the covered intent/subtask**. They are not whole-task success/failure labels and are not automatic permission to call `done`.

- `RETURN_COMPLETED`: the specific intent/subtask covered by this experience has been completed, usually after the required business read/write tool calls and required customer communication. If the user has another independent intent, continue with that next intent instead of ending the conversation.
- `RETURN_BLOCKED(reason="...")`: the covered intent/subtask cannot proceed under the current facts, policy, missing input, refusal boundary, or escalation boundary. Perform any required communication/escalation from the experience, then continue other remaining user intents if they are still actionable.
- `RETURN_NOT_APPLICABLE`: the experience does not match the current facts; discard it and use another applicable experience or current policy/tool facts.

Refusal, no-option, policy-ineligible, missing-input, and `transfer_to_human_agents` branches should be interpreted as `RETURN_BLOCKED(...)` for that local intent, not as whole-task completion. Before ending globally, verify that every user intent is completed, blocked, not applicable, or explicitly transferred/stopped by the user/environment.

## Tools

- `search_experience(query, limit=1)`: searches OpenViking `memories/cases` under the current user, reads each matched case's `## Linked Experiences` section, and returns JSON candidates with `case_name` and linked experience entries. Each experience entry contains only `uri` and a `situation` snippet from that experience's `## Situation` section.
- `read_experience(experience_uri)`: reads one OpenViking experience memory by full URI and returns Markdown.
