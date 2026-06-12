---
name: ov-add-paper
description: "Load when the user asks to add, import, compile, or ingest a research paper/PDF into OpenViking, especially when they mention ov-add-paper, ARA, claims, evidence, figures, tables, or paper-to-OV knowledge resources."
compatibility: OpenViking CLI configured at `~/.openviking/ovcli.conf`
version: 0.1.1
last_updated: 2026-06-10
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
tags:
  - openviking
  - paper-ingestion
  - research
  - ara
---

# ov-add-paper

## Goal

Turn a research paper into an OpenViking-ready structured resource, then complete ingestion with `ov add-resource`. The job is not done until the generated artifact directory has been validated and submitted to OpenViking.

## Inputs

- Required: a paper source, usually a local PDF path or paper URL.
- Optional: output directory, OpenViking target URI, domain notes, related repo/source files, and whether to wait for OV processing.
- If the paper source is missing or inaccessible, ask for it before starting.

## Workflow

1. Read the paper completely, including appendices and all numbered figures/tables.
2. Compile an ARA-style artifact directory using `references/ara-compiler-profile.md`.
3. Validate the artifact with `scripts/validate_ara.py`.
4. Fix validation failures unless the user explicitly accepts them.
5. Ingest the validated artifact directory with `ov add-resource` directly.
6. Confirm the target with `ov stat`/`ov tree` and return the artifact path, OV target/root URI, validation result, ingest result, and any unresolved gaps.

## Output Contract

The generated artifact must include:

- `PAPER.md`
- `logic/problem.md`, `logic/claims.md`, `logic/concepts.md`, `logic/experiments.md`, `logic/related_work.md`
- `logic/solution/constraints.md`
- `src/environment.md`
- `trace/exploration_tree.yaml`
- `evidence/README.md`
- Markdown plus PNG evidence files for every filed numbered table and figure

The final response must include the `ov add-resource` command result, or the recovery checks proving the target landed despite a broken `--wait`, or the exact blocker that prevented ingestion.

## Verification

Run from this skill directory, or use an absolute path to the validator:

```bash
python3 scripts/validate_ara.py <artifact-dir>
```

Then ingest with the OV CLI:

```bash
ov add-resource <artifact-dir> --to viking://resources/papers/<slug> --wait --timeout 300
```

Before ingest, use `ov -o json stat <target-uri>` to check whether the target already exists. After ingest, verify with `ov -o json stat <target-uri>` and `ov tree <target-uri>`.

## Permissions

- Writing a new OpenViking resource is allowed when the user asked to add or ingest the paper.
- Ask before intentionally reusing a target URI that may overwrite or replace an existing resource.
- `--skip-validation` may be used only when the user explicitly accepts the listed validation errors.

## Boundaries

- Do not use `ov add-skill`; this skill creates paper resources, not OV skills.
- Do not silently skip `ov add-resource`; if ingestion fails, report the command, error, and recovery path.
- If `ov add-resource --wait` exits with a connection error after creating the target, do not immediately retry the same URI. Run `ov stat`, `ov wait --timeout <seconds>`, `ov observer queue`, and `ov tree` to determine whether ingestion completed.
- Do not invent claims, evidence, source refs, code, numbers, or research history.
- Do not overwrite an explicit existing OV target unless the user asked for that target.
- Mark unsupported or unreadable content as unavailable instead of filling it in.

## Runtime Resources

- Load `references/ara-compiler-profile.md` before compiling the artifact.
- Load `references/openviking-ingest.md` before running OV ingestion or debugging an ingestion failure.
- Use `scripts/validate_ara.py` for deterministic checks.
