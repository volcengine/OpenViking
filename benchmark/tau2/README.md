# TAU-2 Benchmark

This directory contains a small OpenViking-style entry point for TAU-2 memory
evaluation. The first version is intentionally narrow:

- no-memory control;
- fresh OpenViking memory baseline;
- trajectory / procedure-view treatment;
- optional pre-write recall.

Category rerank and other harness-only diagnostics are not migrated here yet.

## Layout

```text
benchmark/tau2/
├── config/
│   ├── baseline.yaml
│   └── prewrite.yaml
├── scripts/
│   ├── preflight.py
│   ├── run_eval.py
│   ├── summarize.py
│   └── parity_check.py
└── run_full_eval.sh
```

Generated artifacts are written to `benchmark/tau2/result/<run_id>/`.

## Quick Start

Plan the default benchmark without running TAU-2:

```bash
python benchmark/tau2/scripts/preflight.py --config benchmark/tau2/config/baseline.yaml
python benchmark/tau2/scripts/run_eval.py --config benchmark/tau2/config/baseline.yaml --plan-only
```

Plan a one-cell upstream TAU-2 smoke:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
  --domain retail \
  --strategy-id no_memory \
  --num-tasks 1 \
  --repeat-count 1
```

Run with execution enabled after TAU-2, model credentials, and OpenViking are
configured:

```bash
benchmark/tau2/run_full_eval.sh --config benchmark/tau2/config/prewrite.yaml --execute
```

When using Doubao through an OpenAI-compatible endpoint, set `OPENAI_API_KEY`
and `OPENAI_API_BASE` for LiteLLM before running upstream TAU-2.

The initial no-memory cells use upstream TAU-2 CLI flags only. OpenViking memory
cells are kept in the same plan, but marked adapter-pending until the TAU-2
agent adapter is wired in this benchmark directory.

## Evidence Boundary

Only completed `retail + airline` runs with the same config, same seeds/repeats,
and non-empty artifacts should be read as benchmark evidence. Partial runs,
single-task probes, or missing OpenViking corpus identity are diagnostics.
