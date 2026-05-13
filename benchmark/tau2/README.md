# TAU-2 Benchmark

This directory contains a small OpenViking-style entry point for TAU-2 memory
evaluation. The scope is intentionally narrow:

- fresh OpenViking Memory V2 experience-only baseline;
- Memory V2 pre-write recall treatment.
- trajectory-view retrieval treatment for the refined trajectory prompt.
- experimental FGMemory-style pre-write recall on top of trajectory-view memory.

The FGMemory-style route is opt-in and experimental; it is meant for PR-C
review and smoke/targeted probes before any productization decision.

## Layout

```text
benchmark/tau2/
├── config/
│   ├── baseline.yaml
│   ├── category_rerank.yaml
│   ├── official.yaml
│   ├── prewrite.yaml
│   └── trajectory.yaml
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
  --strategy-id memory_v2_experience_only \
  --task-id 5 \
  --repeat-count 1
```

Plan a one-cell Memory V2 pre-write smoke:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
  --domain retail \
  --strategy-id memory_v2_prewrite \
  --num-tasks 1 \
  --repeat-count 1
```

Plan a one-cell trajectory-view smoke:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/trajectory.yaml \
  --domain retail \
  --strategy-id memory_v2_trajectory_view \
  --num-tasks 1 \
  --train-num-tasks 1 \
  --repeat-count 1
```

Plan a one-cell trajectory FGMemory-style category-rerank smoke:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/category_rerank.yaml \
  --domain retail \
  --strategy-id memory_v2_trajectory_category_prewrite \
  --num-tasks 1 \
  --train-num-tasks 1 \
  --repeat-count 1
```

Run the Memory V2 8-trial matrix (`retail + airline` x 2 strategies x 8 repeats):

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
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
Memory V2 baseline. For trajectory-view evidence, start the service from this
branch and inspect generated trajectory files; changing `search_uri` alone does
not prove the new trajectory prompt was used.

## Memory Adapter

Memory V2 cells run through a small TAU-2 agent adapter in this directory:

- train by writing TAU-2 training conversations into OpenViking sessions;
- evaluate by retrieving OpenViking memory at the first user turn;
- for pre-write recall, retrieve again before write-like tool calls and
  regenerate that step with the matched memories;
- emit artifact metadata to identify the OpenViking account, agent,
  corpus, retrieval mode, and simulator policy used by each cell.

The existing `train_memory_mode: experience_only` value selects the Memory V2
session-commit path. `search_memory_type` selects which generated memory bucket
is retrieved during eval (`experiences` by default, `trajectories` for
`config/trajectory.yaml`). The runner prepares each distinct
`domain + corpus_id` once before executing eval cells. Different corpora may be
prepared in parallel with `benchmark.corpus_prepare_concurrency`; session
commits inside one corpus remain serial to preserve OpenViking write semantics.

`config/category_rerank.yaml` keeps the PR-B trajectory memory route and enables
an adapter-local FGMemory-style probe: pre-write recall, category rerank with
Harness `memory_category_annotation.v0` sidecar support, and the retail scope
prompt used by the Harness High-TrajView/FGMemory route. When sidecar files are
provided through `OPENVIKING_TAU2_CATEGORY_ANNOTATION_FILES` or
`AGENT_HARNESS_TAU2_CATEGORY_ANNOTATION_FILES`, the reranker uses those
query/memory annotations first; otherwise it falls back to the local category
catalog keyword classifier for smoke runs. The category sub-policy follows the
S84 component settings, but the
alignment target is the red-box S89/FGMemory high result: retrieve 6, keep
same-category candidates, inject at most 2, skip injection when no positive
category match exists, and apply the scope/applicability prompt at the system
prompt injection point. Retrieval traces include the query category, candidate
memory categories, rerank reasons, selected rows, skipped rows, scope prompt
metadata, and the flat `*_category*_prompt` fields consumed by Harness
diagnostics.

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
