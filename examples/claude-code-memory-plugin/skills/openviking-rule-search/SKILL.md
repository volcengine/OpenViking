---
name: openviking-rule-search
description: Retrieve applicable OpenViking Rules and check compliance. Use when planning an action or response subject to task-specific constraints.
metadata:
  vikingbot:
    always: true
---

# OpenViking Rule Search

## Goal

Use the registered OpenViking Rule search tool to retrieve the compiled Rules that constrain
the current task, then validate the planned action or response against every applicable Rule.
The host may expose it as `rule_search`, `openviking_rule_search`,
`mcp__openviking__rule_search`, `ov_rule_search`, `viking_rule_search`, or another
server-qualified variant; use the exact registered name.

## Workflow

1. Build `task_context` from the user's goal, the planned action or decision, and concrete facts
   that may affect which Rules apply. Include material unknowns instead of guessing them.
2. Call the registered Rule search tool with that `task_context`.
3. Review every returned Rule independently. Apply a Rule when its `applies_when` condition is
   supported by the task context and no `exception` applies.
4. Adjust the planned action or response to comply with all applicable Rules.
5. Call the Rule search tool again when new information materially changes the task context.

## Boundaries

- Treat returned Rules as constraints, not as evidence that unobserved facts are true.
- Do not omit known facts from `task_context` to force a preferred result.
- A missing tool or failed request does not mean no Rules apply. Report the unavailable
  check instead of substituting ordinary memory search for Rule search.
- `delivery.complete=false` means some matched Rules are absent from the inline context.
  Read omitted compiled Rule URIs or the saved full result in bounded sections before acting.
  Do not repeat an identical oversized search or treat the delivered subset as a complete check.
  If the remaining Rules cannot be retrieved, report the incomplete check and pause the action.
- After context compaction expires older Rule lookups, refresh Rules for the current task.
