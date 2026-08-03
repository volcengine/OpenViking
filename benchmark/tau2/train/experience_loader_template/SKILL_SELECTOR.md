---
name: experience_loader
description: Load relevant past-task experience memories through an isolated retrieval filter before solving a task.
---

# experience_loader

1. Before taking task actions, call `load_relevant_experience` once with a natural-language description of the current task: the user's intents, target objects, requested operations, and relevant policy keywords.
2. The tool returns at most 2 past-task experiences that were judged applicable — or a message that none apply. If none apply, proceed from the current policy and tool results; do not retry with rephrased queries.
3. If a genuinely NEW user intent emerges later in the conversation that the first call's description did not cover, you may call `load_relevant_experience` once more for that intent.
4. Returned experiences are guidance from prior tasks. Their `## Situation` states when they apply ("Applies when") and when they do not ("Does not apply when"); `## Reminder` and `## Procedure` describe what to do; `## Anti-pattern` records what went wrong before. Current policy, current tool results, and current user requests always take precedence.
