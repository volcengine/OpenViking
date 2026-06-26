---
name: experience_loader
description: Load relevant OpenViking experience memories via case-linked experience candidates before solving a task.
---

# experience_loader

Use this skill before taking task actions when reusable execution experience may help.

## Required workflow

1. Before taking task actions, call `search_experience` with a natural-language query that describes the current task.
2. Build the query from the current domain, user intent, target object, requested operation, policy keywords, and likely tool/action family. Avoid vague queries such as "help user".
3. Review the returned candidates. Each candidate is a matched case plus the experience URI(s) linked from that case.
4. Choose which linked experience(s) to read yourself. If any linked experience is plausibly relevant, call `read_experience` on at least one returned experience URI before acting.
5. You may call `search_experience` multiple times with improved keywords, and you may call `read_experience` multiple times if useful.
6. Treat loaded experiences as reusable guidance, not as current-task truth. Current policy, current tool results, and current user facts override prior experience.
7. Apply a loaded experience only when its situation and applicability boundaries match the current task. If no linked experience is plausibly relevant, continue without experience guidance.

## Tools

- `search_experience(query, limit=10)`: searches OpenViking `memories/cases` under the current user, reads each matched case's `## Linked Experiences` section, and returns JSON candidates with case score, case URI, task signature, input summary, and linked experience URI(s).
- `read_experience(experience_uri)`: reads one OpenViking experience memory by full URI and returns Markdown.
