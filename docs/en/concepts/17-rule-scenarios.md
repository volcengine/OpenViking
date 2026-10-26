# Rule Scenarios

Rules are grouped by **Scenario**, the situations in which their constraints may matter.
The compiler uses `scenario` for NEW mutations and `scenario_id` for Scenario identity.
Rule search returns `scenarios`, each Rule's `scenario_id`, and `scenario_selection`.

Each Scenario is a directory directly under the user's Rule library:

```text
viking://user/<user_id>/memories/rules/
└── file-operations/
    ├── abstract.md
    ├── confirm-delete.md
    └── preserve-backups.md
```

`abstract.md` contains Scenario metadata and a short description:

```markdown
---
schema: openviking.rule.scenario/v1
scenario_id: file-operations
name: File operations
description: Constraints for creating, modifying, and deleting files.
updated_at: "2026-10-21T00:00:00+00:00"
---

# File operations

Constraints for creating, modifying, and deleting files.
```

The other Markdown files contain complete Rule records, including `atomic_id`,
`scenario_id`, revision, status, content, applicability, exceptions, and evidence.
`abstract` is reserved as a file name. The storage system's generated `.abstract.md`
and `.overview.md` are separate internal summaries.

Scenario retrieval searches only `*/abstract.md`; Rule files cannot occupy the
Scenario candidate limit. After selecting Scenarios, the service reads their direct
Rule files. Membership comes from the directory, so no `rule_uris` list is needed.
Paused and archived Rules remain in their Scenario until lifecycle cleanup removes
expired archived Rules. Rule search uses active Rules; compilation considers active
and paused Rules. Archived Rules are logically deleted and appear in neither path.

Rule status is `active`, `paused`, or `archived`. `PAUSE` suspends an active Rule
without scheduling removal. `ARCHIVE` marks a Rule for cleanup after 30 days by
default, using `archived_at`; repeating it does not reset that timestamp.
`RESTORE` reactivates a paused Rule. Archived Rules cannot be restored through Rule
compilation.
Updating a Rule preserves its current lifecycle status. Replaced Rules become archived.
Lifecycle actions require explicit user instructions. `ARCHIVE` specifically requires
explicit deletion, discard, or recycle-bin intent in the user's instruction. Requests
to pause, disable, deactivate, retire, stop applying, or stop using a Rule use `PAUSE`.

Jev judges Scenario irrelevance and Rule applicability. When Jev is unconfigured,
unavailable, fails, or returns invalid results, the affected candidates pass without
VLM judgment. A failed Rule judgment passes all candidate Rules with score `1.0`,
`judge=pass`, and a warning; this is a pass score, not an estimated probability.
Agents still check applicability and exceptions before enforcing a Rule. Rule
compilation continues to use the compiler VLM; the VLM `rule_matcher` setting is removed.
