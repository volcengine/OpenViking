---
name: experience_loader
description: Load relevant OpenViking experience memories via case-linked experience candidates before solving a task.
---

# experience_loader

Use this skill before taking task actions. It searches for reusable execution experiences,
filters by the short `situation` snippet, then loads the full applicable experience.

## Required workflow

1. Before taking task actions, call `search_experience` with a concise natural-language `situation` that describes what is currently true. If this skill contains a `Runtime Case context`, also pass its exact `task_signature`; otherwise omit that optional argument. Never infer or modify a `task_signature`.
2. Build the `situation` only from facts in the current conversation. Use declarative sentences, preserve the user's known goal, target, scope, and explicit constraints, and prefer the user's original terms. Do not write a keyword list, infer unstated facts, or add speculative alternatives and unrelated negative examples.
3. Review the returned candidates. Each candidate has a `case_name` and linked experience entries; each experience entry has an experience `uri` and a short `situation` snippet describing applicability and exclusions.
4. Treat `situation` as a filter only, not as the full experience. Do not rely on the snippet as sufficient guidance.
5. **Read by default.** For each linked experience, call `read_experience` unless its `situation` explicitly excludes the current task family, object type, policy branch, or requested operation. Do not skip reading merely because the experience may apply later rather than at the opening request.
6. A candidate may apply at a later boundary in the same task, such as before confirmation, before write, after write, a required summary, or the final response. If it is not explicitly excluded and could apply to any such boundary, read it before taking task actions that could reach that boundary.
7. **Re-search on new subtasks.** When the user adds, changes, or combines intents later in the conversation, call `search_experience` again before the next business action. This is required for new information/list/summary/value requests such as total cost, count, remaining/rest, other, those, 其他, 剩余, or similar relative wording.
8. After each search, read every candidate that is not explicitly excluded. If multiple experiences pass this gate, read each applicable experience.
9. Treat loaded experiences as reusable guidance, not as current-task truth. Current policy, current tool results, and current user facts override prior experience.
10. **Re-verify after reading.** Before acting on a loaded experience, check its full `## Situation` against current facts you have obtained from tools (cabin class, reservation status, flight dates, segment state, etc.). If an exclusion condition clearly matches the current task, ignore that experience and proceed from policy and tool results instead.
11. Multi-intent tasks (e.g. "cancel, then book", "upgrade then change flight", "refuse a modification then offer a fallback") may legitimately require more than one experience; gate and apply each segment's experience independently. Do not end the task (`done` / `transfer_to_human_agents`) until every user intent is completed, blocked by policy/missing input, not applicable, or explicitly stopped/transferred.
12. If no linked experience is plausibly relevant after reading and re-verifying candidates, continue without experience guidance.

## Tools

- `search_experience(situation, task_signature=None, limit=2)`: when Runtime Case context supplies `task_signature`, first reads that exact Case and returns it even if it has no linked experiences; only a missing exact Case falls back to Situation search. Without `task_signature`, searches OpenViking `memories/cases` under the current user using the declarative current Situation. Each returned experience entry contains only `uri` and a `situation` snippet from that experience's `## Situation` section.
- `read_experience(experience_uri)`: reads one OpenViking experience memory by full URI and returns Markdown.
