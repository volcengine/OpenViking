# Wiki schema reference (README-first compile)

This is the output contract for the Wiki. It replaces the upstream `.llm-wiki/` on-disk
layout: in an unattended compile you do not maintain `_schema.md`/`_log.md` or an update
mode — every run builds a fresh set of pages at the task-relative output paths, and the
pipeline finalizes them.

## Output layout

```
index.md                         # type: index — root navigation
entity/<subsystem>.md            # type: entity — one per project/subsystem/core module
concept/runtime-mechanisms.md    # type: concept — verified behaviors (>=3 anchors)
method/<topic>.md                # type: method — build/test/extend/onboarding workflow
analysis/<topic>.md              # type: analysis — bounded cross-cutting map
```

6-12 pages total. Group related subsystems onto one landing page rather than creating a
page per file/class/function.

## Frontmatter

Every page starts with YAML frontmatter followed by one H1 that matches the title.

```yaml
---
type: entity            # index | entity | concept | method | analysis
title: Canonical title
description: One factual sentence describing the page.
tags: [orm, models, django]
---
```

`index.md` uses `type: index`. Subsystem overview pages use:

```yaml
---
type: entity
title: Django ORM and Models
tier: landing
covers: [db-models, db-migrations]
description: ...
tags: [...]
---
```

## `covers` and subsystem ids

- Copy `covers` ids verbatim from `repository_profile.major_subsystems[].id` in the task
  data. Never invent an id that the profile does not provide.
- Coverage gate: every `major_subsystems` id must appear in at least one landing page's
  non-empty `covers` list. One landing page may list several related ids.
- A `tier: landing` entity page without `covers` is rejected.

## Source citations / implementation anchors

- Module-level, README/doc-grounded references may be plain repository-relative paths
  (landing pages and guides).
- A **verified implementation anchor** has the shape `relative/path.py:Symbol` followed
  within a few characters by an exact line token:
  - `django/db/models/base.py:ModelBase lines 280-310`
  - `django/core/handlers/wsgi.py:WSGIHandler (L113-144)`
  - `django/urls/resolvers.py:URLResolver.resolve @668`
- The file must exist in the repository, the symbol must be concrete (a real class or
  function), and the line number/range must come from an `openviking_multi_read` window.
- Bare package paths, `__init__.py`, and a `path.py:Symbol` with no line number do not
  count as verified anchors.
- Use an ASCII hyphen `-` and ordinary spaces in ranges.

## Tier requirements

| Page | Minimum evidence |
| --- | --- |
| landing entity (`tier: landing`, `covers`) | README/docs + module paths; no symbol anchor required |
| deep entity (no `tier: landing`) | at least 1 verified implementation anchor |
| `concept/runtime-mechanisms.md` | at least 3 distinct verified implementation anchors |
| `method` / `analysis` | manifests, entry points, docs; anchors where runtime behavior is claimed |

## `index.md` shape

`index.md` is a content catalog, not a file listing. Include:

- repository purpose and intended audience;
- detected language/framework distribution;
- a compact architecture map;
- every generated page exactly once, grouped, with a one-line description and a relative
  link;
- a suggested onboarding reading order.

Keep it navigational; do not duplicate page bodies.
