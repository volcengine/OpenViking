# OpenMontage Benchmark MVP

This benchmark turns the [OpenMontage RFC](../../docs/en/about/03-roadmap.md) idea into a small,
repo-local fixture that stresses stage-scoped context handoff in OpenViking.

The benchmark is intentionally narrow:

- no dependency on the external OpenMontage repository
- one in-repo production fixture
- deterministic scoring
- no UI or long-running orchestration

## Why This Benchmark Matters

OpenMontage-like pipelines are not generic chat. They move across explicit production stages:

1. `brief`
2. `script`
3. `scene_plan`
4. `asset_manifest`
5. `render_report`

Each stage wants a different slice of context. The benchmark checks whether retrieval can stay
focused on the right artifact instead of dragging the full production history into every step.

## Layout

```text
benchmark/openmontage/
├── README.md
├── data/
│   └── fixture.json
├── import_to_ov.py
├── run_eval.py
├── scorer.py
├── test_smoke.py
└── result/
```

## Fixture Model

The fixture contains one synthetic production, `launch-video`, with five stage artifacts:

- `brief.md`
- `script.md`
- `scene_plan.md`
- `asset_manifest.md`
- `render_report.md`

It also contains a compact evaluation set. Each case defines:

- a query
- the target stage
- the artifact URI suffix that should be retrieved
- keywords that should appear in the returned evidence

## Quick Start

### 1. Import the fixture into OpenViking

Embedded mode:

```bash
python benchmark/openmontage/import_to_ov.py --mode embedded --workspace ./data/openmontage-workspace
```

HTTP mode:

```bash
python benchmark/openmontage/import_to_ov.py --mode http --url http://localhost:1933
```

### 2. Run the evaluation

```bash
python benchmark/openmontage/run_eval.py --mode embedded --workspace ./data/openmontage-workspace
```

This writes a JSON report to `benchmark/openmontage/result/openmontage_eval.json`.

### 3. Score the results again if needed

```bash
python benchmark/openmontage/scorer.py benchmark/openmontage/result/openmontage_eval.json
```

## Evaluation Strategy

The MVP deliberately avoids LLM judges. It scores retrieval deterministically:

- pass if the expected artifact URI suffix appears in the returned hits
- pass if all required keywords appear across the top retrieved evidence

That keeps this benchmark cheap, reproducible, and suitable for CI smoke usage.

## Expected Use

This benchmark is useful when you want to compare:

- stage-local retrieval quality
- hierarchy-aware directory layout decisions
- whether artifact handoff remains coherent as a project accumulates more files

It is not a full production workflow benchmark yet. The next step would be to add multiple
projects, more stage transitions, and latency/cost reporting.
