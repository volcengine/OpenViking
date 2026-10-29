# Rules (Beta)

A Rule is a durable constraint an Agent must follow when its conditions hold, such as an obligation, prohibition, or boundary. OpenViking compiles source material in batches into self-contained rules, then retrieves applicable rules for the current task so the Agent can check them before acting or answering.

The current Beta supports explicit imports and on-demand recall. Ordinary session commits do not automatically compile rules, and there is no built-in flow that forcibly injects them on every user message or at the start of every session.

## How rules differ from other context types

| Type | Question it answers | Example |
| --- | --- | --- |
| Resource | What source material can I consult? | An agreement, code repository, or technical specification |
| Memory | What facts, preferences, and experience are known? | A user prefers concise answers; a project uses a particular framework |
| Skill | How do I carry out this kind of task? | Repository review steps or a report generation method |
| Rule | Under which conditions must I do something, or refrain from doing it? | A repository declaring itself open source must provide a locatable license text |

Rules take priority over ordinary Memory. They are a separate context type with dedicated compilation and search interfaces, even though they live under the user's `memories/rules/`. Importing an agreement or standard as a Resource stores the material; its mandatory constraints still need explicit compilation.

Preferences, suggestions, facts, examples, and one-off tasks should not become Rules. Compilation must not turn “should” into “must” or weaken “must” and “must not.”

## Use cases

| Scenario | Rule sources | How the Agent uses them |
| --- | --- | --- |
| Legal agreement review | Obligations, prohibitions, and exceptions in user-provided agreements | Retrieve constraints using the review object and known facts, then check the text or planned actions |
| Coding standards checks | Team development standards and review requirements | Retrieve rules using the repository, changes, and execution stage, then check the proposed changes |
| Open-source license checks | User-provided license requirements | Read repository declarations and license files, verify triggering conditions, and record evidence |

For example, “When a repository declares itself open source, it must provide an explicit, complete, and locatable project license text” can produce:

| Field | Example |
| --- | --- |
| `content` | When a repository declares itself open source, it must provide an explicit, complete, and locatable project license text. |
| `applies_when` | The repository declares itself open source. |
| `exception` | Empty string: the source specifies no exception. |
| Scenario | Project license review |

Topic similarity does not establish applicability. Agents must verify conditions using readable evidence. A rule still applies when its requirement is already satisfied.

## How rules are organized

Each Rule holds one complete constraint that can be updated or withdrawn independently. It includes `content`, applicability conditions in `applies_when`, an `exception`, sources and exact evidence, a stable `atomic_id`, a `revision`, and a `status`. Empty `applies_when` means the rule applies everywhere except its exception.

A Scenario describes situations in which rules may apply, such as “Project license review,” and groups rules for retrieval. It is not a source document title: one document can produce multiple Scenarios, and several sources can share a Scenario.

```text
viking://user/<user_id>/memories/rules/
└── repository-license/
    ├── abstract.md          # Scenario description
    └── project-license.md   # Complete Rule record
```

## Compilation flow

```text
Rule text, files, directories, or URLs
    → Read existing resources, or parse and ingest new sources
    → Recall existing active / paused rules
    → Compiler extracts constraints and proposes mutations
    → Validate structure, targets, exact evidence, and retained requirements
    → Write Rules / Scenarios and refresh indexes asynchronously
```

The Compiler preserves obligation strength, conditions, exceptions, thresholds, and units, separating rules that can be maintained independently. An evolving rule updates its existing record; an explicit full replacement creates a new identity and archives the old record. Fully covered input needs no duplicate write. Material ambiguity or rejected input is recorded with reasons and creates no Rule.

The compilation interface first returns a background task. Inspect the actual mutations after completion. The index task associated with the writes may still be processing; confirm it completes before use as well. See [Rule validation (Beta)](../workflows/02-rule-validation.md) for the steps.

## Search and execution flow

```text
Agent submits the task goal, object, planned actions, and known facts
    → Semantically recall potentially relevant Scenarios
    → Jev filters Scenarios judged unrelated with high confidence
    → Read their active rules and judge conditions and exceptions
    → Return structured rules and complete constraint text
    → Agent checks every rule and constrains its actions or answer
```

Search returns rules scoring at least `0.65` by default, with no fixed Top-K in the public interface. For non-empty conditions, an exception vetoes a rule only if its score reaches the threshold and strictly exceeds the applicability score. With empty conditions, an exception reaching the threshold is sufficient to veto.

If Jev is unconfigured, unavailable, or returns invalid results, affected candidates pass through. Failed Rule judgment returns `judge="pass"`, `probability=1.0`, and a warning; this score means pass-through, not an estimated probability. Agents must still check conditions and exceptions.

`rendered` supplies an application protocol and complete rule blocks for the Agent. Search determines whether rules apply; the Agent uses task evidence to decide whether they are satisfied, violated, or undetermined. Empty results do not prove the library contains no relevant constraints. `delivery.complete` only means matched rules were delivered in full, not that the entire library was recalled.

## Lifecycle

| Status | Compilation candidate | Search candidate | Meaning |
| --- | --- | --- | --- |
| `active` | Yes | Yes | Enabled |
| `paused` | Yes | No | Suspended and retained without expiry cleanup |
| `archived` | No | No | Logically deleted; eligible for cleanup after retention |

Explicit requests to pause or disable use `PAUSE`; reactivation of paused rules uses `RESTORE`. Deletion, discard, or moving to the recycle bin uses `ARCHIVE`. `UPDATE` preserves the current status. The compilation interface cannot restore archived rules.

Archived records store `archived_at`; repeated archival does not reset it. Periodic cleanup is enabled by default with 30-day retention. Physical removal occurs during a scan after retention expires; see [server Rules configuration](../configuration/01-server.md#rules).

## Related documentation

- [Rule validation (Beta)](../workflows/02-rule-validation.md): batch compilation, recall, and Agent checks
- [Rules API (Beta)](../api/26-rules.md): parameters, responses, and failures
- [VikingBot Installation and Configuration](../guides/17-vikingbot.md): the rule search tool
- [Context Types](./02-context-types.md): resources, memory, and skills
