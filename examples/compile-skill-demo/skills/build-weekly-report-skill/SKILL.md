---
name: build-weekly-report-skill
description: Create or update a reusable weekly-report writing Skill package from report examples and trajectory memories. Use during ov compile when the target is a skills namespace and the sources contain sample reports plus proven authoring, review, or quality-control experience.
---

# Build Weekly Report Skill

Turn source evidence into an installable Skill that teaches future agents how to
write weekly or biweekly reports. The output is a Skill package, not a summary of
the source documents and not a Wiki.

## Build workflow

1. Read all source reports and trajectory memories before drafting.
2. Separate stable writing patterns from demo-specific facts:
   - Reports provide examples of structure, evidence, tone, and decision context.
   - Trajectories provide tested workflow, review feedback, and failure prevention.
3. Infer the output Skill name from the Compile reason. For this demo, use
   `weekly-report-writer`.
4. Design the smallest package that preserves the reusable workflow.
5. Check every drafted file against the output contract below.

## Output contract

- Emit only `CompileFileDraft` outputs.
- Put every draft under one target-relative Skill directory:
  `weekly-report-writer/...`.
- Always draft `weekly-report-writer/SKILL.md`.
- Add files under `weekly-report-writer/references/` only when they keep
  `SKILL.md` meaningfully shorter or make a reusable template clearer.
- Do not draft Wiki pages, overview pages, catalog files, derived semantic files,
  or unrelated documentation.
- Do not add scripts unless the source proves deterministic automation is needed.

The generated `SKILL.md` must:

- Start with YAML frontmatter containing only `name` and `description`.
- Use a lowercase hyphen-case `name` matching its directory.
- Make `description` explain both what the Skill does and when it should trigger.
- Teach a future agent to turn raw facts into a concise, credible, actionable
  weekly report.
- Include evidence gathering, result-oriented organization, risk and next-step
  writing, and a final factual consistency check when supported by the sources.
- Tell the future agent not to invent missing facts or overstate conclusions.

## Generalization rules

- Generalize procedures and quality gates; never hard-code project names, owners,
  dates, metrics, or outcomes from the examples.
- Preserve uncertainty. Convert missing evidence into an explicit follow-up or
  placeholder rule rather than fabricating content.
- Prefer concrete instructions over commentary about how the Skill was derived.
- If updating an existing Skill, retain useful compatible instructions and
  auxiliary files, changing only what the source evidence justifies.
