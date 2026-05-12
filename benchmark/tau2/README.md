# TAU-2 Benchmark

This directory contains a small OpenViking-style entry point for TAU-2 memory
evaluation. The first version is intentionally narrow:

- no-memory control;
- fresh OpenViking Memory V2 experience-only baseline;
- trajectory / procedure-view treatment;
- optional pre-write recall.

Category rerank and other harness-only diagnostics are not migrated here yet.
The Memory V2 baseline is wired end to end; trajectory / procedure-view remains
visible in the plan but adapter-pending.

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

Run the Memory V2 8-trial baseline (`retail + airline` x 4 repeats):

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
  --strategy-id memory_v2_experience_only \
  --execute
```

For a small E2E smoke, keep both the eval and train slices tiny:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
  --domain retail \
  --strategy-id memory_v2_experience_only \
  --num-tasks 1 \
  --train-num-tasks 1 \
  --repeat-count 1 \
  --execute
```

When using Doubao through an OpenAI-compatible endpoint, set `OPENAI_API_KEY`
and `OPENAI_API_BASE` for LiteLLM before running upstream TAU-2.

Start the OpenViking service before executing memory cells, and verify it with
`ov status`. For evidence runs, use a clean OpenViking workspace/config and set
`OPENVIKING_URL` explicitly so local custom memory templates do not pollute the
Memory V2 baseline.

## Memory Adapter Boundary

`no_memory` cells run through the external TAU-2 CLI. `memory_v2_experience_only`
cells run through a small TAU-2 agent adapter in this directory:

- train by writing TAU-2 training conversations into OpenViking sessions;
- evaluate by retrieving OpenViking experience memory at the first user turn;
- emit artifact metadata to identify the OpenViking account, agent,
  corpus, retrieval mode, and simulator policy used by each cell.

The trajectory / procedure-view treatment is kept in the same plan but remains
`adapter_status: pending`; `--execute` fails fast if that strategy is selected
before its adapter is implemented.

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
Executed runs write per-cell JSON under `cell_results/` and a strategy/domain
aggregate under `scoreboard.json`. Memory training artifacts are shared by
domain and strategy under `memory_corpora/`, so repeated eval cells reuse the
same fresh corpus instead of rewriting it.
