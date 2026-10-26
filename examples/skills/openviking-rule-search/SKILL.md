---
name: openviking-rule-search
description: Check applicable OpenViking Rules with openviking_rule_search. Load when an agent must validate a planned action or response against task-specific constraints.
metadata:
  vikingbot:
    always: true
---

# OpenViking Rule Search

## Goal

Use `openviking_rule_search` to retrieve the compiled Rules that constrain the current task,
then validate the planned action or response against every applicable Rule.

## Workflow

1. Build `task_context` from the user's goal, the planned action or decision, and concrete facts
   that may affect which Rules apply. Include material unknowns instead of guessing them.
2. Call `openviking_rule_search` with that `task_context`.
3. Review every returned Rule independently. Apply a Rule when its `applies_when` condition is
   supported by the task context and no `exception` applies.
4. Adjust the planned action or response to comply with all applicable Rules.
5. Call `openviking_rule_search` again when new information materially changes the task context.

## Boundaries

- Treat returned Rules as constraints, not as evidence that unobserved facts are true.
- Do not omit known facts from `task_context` to force a preferred result.
