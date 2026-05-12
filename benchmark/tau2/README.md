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
│   ├── official.yaml
│   └── prewrite.yaml
├── scripts/
│   ├── run_eval.py
│   ├── setup_tau2_repo.sh
│   └── tau2_common.py
└── run_full_eval.sh
```

Generated artifacts are written to `benchmark/tau2/result/<run_id>/`.

## Quick Start

This benchmark delegates task simulation and scoring to an external TAU-2
checkout. Point the runner at that checkout and CLI explicitly when they are not
on the default path:

```bash
export TAU2_REPO=/path/to/tau2-bench
export TAU2_CLI=/path/to/tau2
```

For a local one-command setup, clone and install TAU-2 into ignored benchmark
directories:

```bash
benchmark/tau2/scripts/setup_tau2_repo.sh
source benchmark/tau2/.env.tau2
```

Plan the default benchmark without running TAU-2:

```bash
python benchmark/tau2/scripts/run_eval.py --config benchmark/tau2/config/baseline.yaml --plan-only
```

Add `--preflight` or `--strict-preflight` when you want the runner to write a
small environment/config check next to the run plan.

After setup, verify the local TAU-2 link and write a one-cell run plan:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
  --strict-preflight \
  --domain retail \
  --strategy-id no_memory \
  --task-id 5 \
  --repeat-count 1
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

## User Simulator Policy

The runner default is the official TAU-2 user simulator if
`eval.user_simulator_policy` is omitted. The bundled OpenViking memory benchmark
config sets `confirmation_aware`, because a memory benchmark should not treat
user confirmation as task completion before the backend write has happened.

`confirmation_aware` applies a small idempotent prompt patch to the configured
TAU-2 checkout before planning or running. The patch appends only the behavioral
confirmation boundary to the TAU-2 user simulator guidelines; metadata such as
the upstream PR link is kept in run artifacts, not in the simulator prompt.
Reference: [sierra-research/tau2-bench#297](https://github.com/sierra-research/tau2-bench/pull/297).

Use `config/official.yaml` with a clean TAU-2 checkout when you need an
official-user-simulator parity run. If the checkout was already patched, the
artifact records that boundary instead of labeling the run pure official.

## Evidence Boundary

Only completed `retail + airline` runs with the same config, same seeds/repeats,
and non-empty artifacts should be read as benchmark evidence. Partial runs,
single-task probes, or missing OpenViking corpus identity are diagnostics.
