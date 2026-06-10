# ARA Compiler Profile for ov-add-paper

This profile adapts the Agent-Native Research Artifact compiler pattern for OpenViking ingestion. It keeps the ARA compiler's epistemic structure, but the final deliverable is an OV resource directory.

Source reference: https://github.com/AmberLJC/Agent-Native-Research-Artifact

## Compilation Principles

- Treat the paper as evidence first, narrative second.
- Read the entire paper, including appendices and supplementary sections available in the provided source.
- Preserve raw evidence before synthesis.
- Separate exact source facts, visual estimates, model inference, and unavailable information.
- Strong claims require direct evidence; use weaker wording when evidence is narrower.
- Every source reference should point to an actual page, section, figure, table, equation, or provided repo file.

## Required ARA Layout

```text
PAPER.md
logic/
  problem.md
  claims.md
  concepts.md
  experiments.md
  related_work.md
  solution/
    constraints.md
src/
  environment.md
trace/
  exploration_tree.yaml
evidence/
  README.md
  figures/
  tables/
```

Additional files are allowed only when the paper warrants them, such as `logic/solution/algorithm.md`, `logic/solution/architecture.md`, `data/dataset.md`, `src/configs/`, or `evidence/proofs/`.

## Evidence Pass

Build an evidence ledger before writing claims:

1. Enumerate every numbered `Figure N` and `Table N` in the paper, in order.
2. For each filed object, save both:
   - a cropped or full-page PNG preserving the source visual
   - a Markdown transcription or structured description
3. If an object cannot be filed, account for it in `evidence/README.md` with the reason.
4. Keep raw source evidence separate from derived subsets.

Figure Markdown should include:

- Source
- Caption
- Figure type: `quantitative_plot`, `diagram`, `qualitative_sample`, or `mixed`
- Extraction method: `exact_from_labels`, `digitized_estimate`, or `visual_description`
- Reading confidence
- Supports
- Transcription or visual description

Table Markdown should include:

- Source
- Caption
- Supports
- faithful table transcription

## Cognitive Layer

`logic/problem.md` should capture observations, gaps, key insight, and assumptions.

`logic/claims.md` should use `C01`, `C02`, ... headings. Each claim needs:

- Statement
- Status
- Falsification criteria
- Proof, referencing experiment IDs such as `E01`
- Evidence basis
- Interpretation when useful
- Dependencies
- Tags

`logic/experiments.md` should use `E01`, `E02`, ... headings. It describes verification plans, not exact result numbers. Exact numbers belong in evidence files.

`logic/concepts.md` should define paper-specific concepts. Do not pad with generic terms.

`logic/related_work.md` should describe typed dependencies such as imports, extends, baseline, bounds, or refutes.

`logic/solution/constraints.md` is always required and should state boundary conditions, assumptions, and limitations.

## Artifact Layer

`src/environment.md` is always required. Other `src/` files should capture concrete artifacts only when they exist in the paper or provided source material.

Do not manufacture code stubs from prose-only methods. If code is included, mark whether it is transcribed from source or reconstructed from explicitly printed pseudocode/equations.

## Exploration Trace

`trace/exploration_tree.yaml` records the research DAG:

- central questions
- experiments
- decisions
- dead ends
- pivots
- support level: `explicit` or `inferred`

Do not invent failures or decisions. If the paper hides the process, use a smaller trace and mark reconstructed nodes as inferred.

## Coverage Loop

Before validation, do up to three coverage passes:

1. Re-read source headings, figures, tables, equations, appendix sections, and references.
2. Compare missing items against the artifact.
3. Patch omissions, weak claim wording, missing evidence links, or unresolved source refs.
4. Stop early if a pass finds no material gaps.

## Done State

The ARA compile phase is complete only when:

- mandatory files exist and are non-empty
- figure/table evidence has Markdown plus PNG when filed
- claims and experiments cross-reference correctly
- `PAPER.md` has a useful layer index
- the artifact passes `scripts/validate_ara.py`
