# Article templates (README-first compile)

Fill every template from supplied evidence and verified anchors only. Keep the required
frontmatter keys; omit sections you cannot ground rather than inventing content. All
paths are task-relative output paths.

## index.md

```markdown
---
type: index
title: <Project> Wiki
description: Structured directory of the <Project> code wiki.
tags: [index]
---

# <Project> Wiki

<One paragraph: what the project is and who this wiki is for.>

**Stack:** <languages / frameworks / build system, from manifests>.

## Architecture at a glance

<Compact component map; ASCII diagram only if it clarifies real components.>

## Subsystems

- [Django ORM and Models](../entity/db-models.md) — models, querysets, migrations.
- [Request Handling](../entity/request-handling.md) — WSGI handler, URL resolution, middleware.
- ...

## Concepts

- [Runtime mechanisms](../concept/runtime-mechanisms.md) — verified fallback, state and
  error-code behaviors in core workflows.

## How-to

- [Getting started](../method/getting-started.md) — install, configure, run tests.

## Reading order

<Ordered list for a newcomer.>
```

## Landing entity page (breadth, no symbol anchor required)

```markdown
---
type: entity
title: Django ORM and Models
tier: landing
covers: [db-models, db-migrations]
description: Model definition, query construction, and schema migration subsystems.
tags: [orm, models, migrations]
---

# Django ORM and Models

<1-2 sentence responsibility, then its boundary: what it owns and does not own.>

## Scope

| Component | Responsibility |
| --- | --- |
| `django/db/models/` | model/field/queryset machinery |
| `django/db/migrations/` | schema change graph and operations |

## Key ideas

<Behavior grounded in README/docs and module paths. Group every `covers` id here.>

## Where to go deeper

- [Query execution internals](db-query-execution.md) — deep page with implementation anchors.
- [Runtime mechanisms](../concept/runtime-mechanisms.md)
```

## Deep entity page (requires >=1 verified anchor)

```markdown
---
type: entity
title: Query Execution Internals
description: How a QuerySet becomes SQL and how rows are materialized.
tags: [orm, query]
---

# Query Execution Internals

<1-2 sentence purpose.>

## Responsibility

What this owns, and what it does not.

## Key files

| File | Role |
| --- | --- |
| `django/db/models/sql/compiler.py` | builds and executes SQL |
| `django/db/models/query.py` | lazy QuerySet evaluation |

## How it behaves

Describe the real flow and cite verified anchors, e.g.
`django/db/models/sql/compiler.py:SQLCompiler.execute_sql (lines 1310-1380)`.

- Lazy evaluation trigger — `django/db/models/query.py:QuerySet._fetch_all (lines 1868-1896)`.

## Dependencies

- **Uses:** database backends, connection/transaction management.
- **Used by:** model fields, admin, forms.

## See also

- [Django ORM and Models](db-models.md)
- [Runtime mechanisms](../concept/runtime-mechanisms.md)
```

## Runtime mechanisms page (requires >=3 distinct verified anchors)

```markdown
---
type: concept
title: Runtime mechanisms
description: Verified fallback, state-transition, bound, and error-code behaviors in core workflows.
tags: [mechanisms, runtime]
---

# Runtime mechanisms

Only mechanisms verified with openviking_grep + openviking_multi_read. Do not include
keyword matches or planned ("to be verified") topics.

## <Mechanism name, e.g. Transaction atomicity and savepoint fallback>

- **Workflow / symbols:** describe the path and the concrete symbols.
- **Trigger / input condition:** ...
- **Precedence:** which branch/bound is checked first.
- **Outcome:** final state, output, fallback, or error code.
- **Evidence:**
  - `django/db/transaction.py:atomic (lines 176-210)`
  - `django/db/backends/base/base.py:BaseDatabaseWrapper.savepoint_commit (lines 445-460)`
  - `<third distinct file + symbol + exact line range>`

Repeat for each verified mechanism (at least three distinct anchors across the page).
State residual uncertainty explicitly. Never force a mechanism family that is not verified.

## See also

- The subsystem deep pages each mechanism belongs to.
```

## Method page (verified build / test / extend / onboarding workflow)

```markdown
---
type: method
title: Getting started
description: Install, configure, and run the test suite from project manifests.
tags: [setup, testing]
---

# Getting started

## Prerequisites
<Versions pinned in manifests; nothing assumed.>

## Install
```bash
<exact commands from docs/manifests>
```

## Run tests
```bash
<exact command, e.g. tox / pytest entry point evidenced in the repo>
```

## Troubleshooting
| Problem | Fix |
| --- | --- |
| ... | ... |
```

## Analysis page (bounded cross-cutting architecture / newcomer map)

```markdown
---
type: analysis
title: Newcomer code map
description: A bounded tour linking the major subsystems and first tasks.
tags: [onboarding, map]
---

# Newcomer code map

<Cross-cutting view: how a request/change crosses subsystems, each linked to its entity
page, with anchors where runtime behavior is asserted. Keep the scope bounded.>
```
