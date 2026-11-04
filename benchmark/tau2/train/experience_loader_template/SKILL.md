---
name: experience_loader
description: Load OpenViking experiences linked to the exactly matching current task case before solving a task.
---

# experience_loader

Use this skill before taking task actions when reusable execution experience may help.

## Required workflow

1. Before taking task actions, call `search_experience` with a short description of the current task.
2. The runner binds this tool to the current task's exact `task_signature`. The query is logged but does not select cases. There is no relevance search or fallback to similar tasks. If no exact case is returned, continue without experience guidance; do not retry with different keywords.
3. Review the returned exact-match cases and their linked experience entries; each experience entry includes its `name`, `uri`, and a short `situation` snippet describing its applicability and exclusions.
4. **Gate before reading.** For each linked experience, read its `situation` snippet and check whether the current task matches the experience's applicability AND does NOT match any of its exclusions / "不适用于" / "does not apply to" items. Skip experiences whose situation explicitly excludes your case (e.g. wrong cabin class, flights already flown, different action family, or different change type). Only call `read_experience` on experiences that plausibly apply after this check. If no experience passes the gate, continue without experience guidance.
5. Only call `read_experience` for URIs linked from the returned exact-match cases. You may read multiple linked experiences that pass the gate; do not search for other cases or guess experience URIs.
6. Treat loaded experiences as reusable guidance, not as current-task truth. Current policy, current tool results, and current user facts override prior experience.
7. **Re-verify after reading.** Even after `read_experience`, before acting on the experience, check its full `## Situation` against current facts you have obtained from tools (cabin class, reservation status, flight dates, segment state, etc.). If any "不适用于" / exclusion condition matches the current task now that you have concrete facts, DISCARD the experience and proceed from policy and tool results instead — do NOT apply its Approach or Reflect.
8. Multi-intent tasks (e.g. "cancel, then book", "upgrade then change flight", "refuse a modification then offer a fallback") may legitimately require more than one experience; gate and apply each segment's experience independently. Do not end the task (`done` / `transfer_to_human_agents`) just because one segment's experience reaches a local return marker — check whether the user has a remaining intent.
9. If no linked experience is plausibly relevant after gating, continue without experience guidance.

## Local return markers in loaded experiences

Experience return markers are **local to the covered intent/subtask**. They are not whole-task success/failure labels and are not automatic permission to call `done`.

- `RETURN_COMPLETED`: the specific intent/subtask covered by this experience has been completed, usually after the required business read/write tool calls and required customer communication. If the user has another independent intent, continue with that next intent instead of ending the conversation.
- `RETURN_BLOCKED(reason="...")`: the covered intent/subtask cannot proceed under the current facts, policy, missing input, refusal boundary, or escalation boundary. Perform any required communication/escalation from the experience, then continue other remaining user intents if they are still actionable.
- `RETURN_NOT_APPLICABLE`: the experience does not match the current facts; discard it and use another applicable experience or current policy/tool facts.

Refusal, no-option, policy-ineligible, missing-input, and `transfer_to_human_agents` branches should be interpreted as `RETURN_BLOCKED(...)` for that local intent, not as whole-task completion. Before ending globally, verify that every user intent is completed, blocked, not applicable, or explicitly transferred/stopped by the user/environment.

## Tools

- `search_experience(query, limit=10)`: directly reads deterministic case URIs under the current user's OpenViking `memories/cases`, verifies the runner-supplied `task_signature` exactly, and returns linked experience entries (each with `name`, `uri`, and a `situation` snippet). Query relevance is not used. Missing or mismatched cases return no candidates, with no semantic-search fallback.
- `read_experience(experience_uri)`: reads one OpenViking experience memory by full URI and returns Markdown.
