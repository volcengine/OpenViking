# TAU-2 Benchmark

This directory contains a small OpenViking-style entry point for TAU-2 memory
evaluation. The scope is intentionally narrow:

- fresh OpenViking Memory V2 experience-only baseline;
- Memory V2 pre-write recall treatment.
- trajectory memory retrieval treatment for the refined extraction prompt and
  retrieval-anchor embedding text.

Category rerank and other harness-only diagnostics are intentionally left out.

## Layout

```text
benchmark/tau2/llm/
├── config/
│   ├── baseline.yaml
│   ├── official.yaml
│   ├── prewrite.yaml
│   └── trajectory.yaml
├── scripts/
│   ├── run_eval.py
│   ├── setup_tau2_repo.sh
│   └── tau2_common.py
└── run_full_eval.sh
```

Generated eval artifacts are written to `benchmark/tau2/llm/result/<run_id>/`.
Memory corpus artifacts are cached outside the run id at
`benchmark/tau2/llm/result/memory_corpora/` by default.

## Quick Start

This benchmark delegates task simulation and scoring to an external TAU-2
checkout. Point the runner at that checkout and CLI explicitly when they are not
on the default path:

```bash
export TAU2_REPO=/path/to/tau2-bench
export TAU2_CLI=/path/to/tau2
```

The default OpenViking TAU-2 memory evidence protocol is
`fixed_first_user_full8`: retail + airline, 8 repeats, same seeds, confirmation
aware user simulator, and fixed first user fixtures for both domains. Later user
simulator turns remain live. Set the fixture paths before running the default
configs:

```bash
export TAU2_RETAIL_FIXED_FIRST_USER_FILE=/path/to/retail/fixed_first_user_fixture.json
export TAU2_AIRLINE_FIXED_FIRST_USER_FILE=/path/to/airline/fixed_first_user_fixture.json
```

`--strict-preflight` fails when `eval.require_fixed_first_user=true` and either
fixture is missing. Use `config/official.yaml` for an explicit non-fixed,
official-live-user control.

For a local one-command setup, clone and install TAU-2 into ignored benchmark
directories:

```bash
benchmark/tau2/llm/scripts/setup_tau2_repo.sh
source benchmark/tau2/llm/.env.tau2
```

For PR-B-compatible reproduction, pin the TAU-2 checkout to a ref that includes
the confirmation-aware text-user-simulator prompt. The original PR-B evidence
used the open TAU-2 fix PR head (`79dbf0c18ac7637aedf869cb3122babcd57aaf17`):

```bash
benchmark/tau2/llm/scripts/setup_tau2_repo.sh \
  --ref refs/pull/297/head
source benchmark/tau2/llm/.env.tau2
```

Reference: [sierra-research/tau2-bench#297](https://github.com/sierra-research/tau2-bench/pull/297).

Plan the default benchmark without running TAU-2:

```bash
python benchmark/tau2/llm/scripts/run_eval.py --config benchmark/tau2/llm/config/baseline.yaml --plan-only
```

Add `--preflight` or `--strict-preflight` when you want the runner to write a
small environment/config check next to the run plan.

After setup, verify the local TAU-2 link and write a one-cell run plan:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/baseline.yaml \
  --strict-preflight \
  --domain retail \
  --strategy-id memory_v2_experience_only \
  --task-id 5 \
  --repeat-count 1
```

Plan a one-cell Memory V2 pre-write smoke:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/baseline.yaml \
  --domain retail \
  --strategy-id memory_v2_prewrite \
  --num-tasks 1 \
  --repeat-count 1
```

Plan a one-cell trajectory memory smoke:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/trajectory.yaml \
  --domain retail \
  --strategy-id memory_v2_trajectory_view \
  --num-tasks 1 \
  --train-num-tasks 1 \
  --repeat-count 1
```

Run the Memory V2 8-trial matrix (`retail + airline` x 2 strategies x 8 repeats):

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/baseline.yaml \
  --execute
```

## Reproduce the PR-B Evidence

The PR-B headline and content-shape ablation use
`config/prb_content_matrix_new_prompt.yaml`. It runs the no-memory control plus
trajectory first-user top4 / pre-write top2, experience top2, and representative
4000-character budget ablation routes across `retail + airline` with 8 repeats.
The current trajectory prompt indexes `trajectory_name + retrieval_anchor`
instead of the full procedure body. This keeps retrieval focused on the positive
operation boundary and reduces broad terminal-handoff / cancellation-like memory
matches.

### 1. Bootstrap fixed-first-user fixtures

The default PR-B configs require fixed-first-user fixtures, so a fresh checkout
needs one live-user bootstrap pass before strict reproduction. This pass uses
the same confirmation-aware simulator policy but does not require fixed fixtures.

Run one bootstrap pass per domain:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/fixed_first_user_bootstrap.yaml \
  --domain retail \
  --run-id fixed_first_user_bootstrap_retail \
  --strict-preflight \
  --execute

benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/fixed_first_user_bootstrap.yaml \
  --domain airline \
  --run-id fixed_first_user_bootstrap_airline \
  --strict-preflight \
  --execute
```

Then convert each bootstrap `results.json` into a fixture:

```bash
RETAIL_RESULTS=benchmark/tau2/llm/result/fixed_first_user_bootstrap_retail/memory_cells/fixed_first_user_bootstrap_retail_retail_no_memory_r1/fixed_first_user_bootstrap_retail_retail_no_memory_r1.json
AIRLINE_RESULTS=benchmark/tau2/llm/result/fixed_first_user_bootstrap_airline/memory_cells/fixed_first_user_bootstrap_airline_airline_no_memory_r1/fixed_first_user_bootstrap_airline_airline_no_memory_r1.json

python benchmark/tau2/llm/scripts/build_fixed_first_user_fixture.py \
  --repo "$TAU2_REPO" \
  --results-json "$RETAIL_RESULTS" \
  --domain retail \
  --task-split-name test \
  --output benchmark/tau2/llm/result/fixed_first_user_fixtures/retail/fixed_first_user_fixture.json \
  --require-full-split

python benchmark/tau2/llm/scripts/build_fixed_first_user_fixture.py \
  --repo "$TAU2_REPO" \
  --results-json "$AIRLINE_RESULTS" \
  --domain airline \
  --task-split-name test \
  --output benchmark/tau2/llm/result/fixed_first_user_fixtures/airline/fixed_first_user_fixture.json \
  --require-full-split
```

Export the generated fixture paths for subsequent strict runs:

```bash
export TAU2_RETAIL_FIXED_FIRST_USER_FILE="$PWD/benchmark/tau2/llm/result/fixed_first_user_fixtures/retail/fixed_first_user_fixture.json"
export TAU2_AIRLINE_FIXED_FIRST_USER_FILE="$PWD/benchmark/tau2/llm/result/fixed_first_user_fixtures/airline/fixed_first_user_fixture.json"
```

### 2. Run smoke and full PR-B matrix

First run one tiny end-to-end smoke against a clean local OpenViking service:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/prb_content_matrix_new_prompt.yaml \
  --domain retail \
  --strategy-id new_traj_fixed_first_user_prewrite \
  --num-tasks 1 \
  --train-num-tasks 1 \
  --repeat-count 1 \
  --strict-preflight \
  --execute
```

Then run the full PR-B matrix:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/prb_content_matrix_new_prompt.yaml \
  --run-id prb_content_matrix_new_prompt_full8 \
  --strict-preflight \
  --execute
```

The main result is written to
`benchmark/tau2/llm/result/prb_content_matrix_new_prompt_full8/scoreboard.json`.
Per-cell execution records live under `cell_results/`, raw TAU-2 result JSON
lives under `memory_cells/`, and corpus identity / generated memory checks live
under `memory_corpora/`.

For the strongest trajectory-only treatment, inspect the
`new_traj_fixed_first_user_prewrite` row. It uses trajectory top4 at the first
user turn, trajectory top4 retrieval before writes, pre-write injection top2,
fixed-first-user fixtures, and the generic scope prompt.

For a small E2E smoke, keep both the eval and train slices tiny:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/baseline.yaml \
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
`OPENVIKING_URL` explicitly so local template overrides do not pollute the
Memory V2 baseline. For trajectory memory evidence, start the service from this
branch and inspect generated trajectory files; changing `search_uri` alone does
not prove the new trajectory prompt was used.

## Memory Adapter

Memory V2 cells run through a small TAU-2 agent adapter in this directory:

- train by writing TAU-2 training conversations into OpenViking sessions;
- evaluate by retrieving OpenViking memory at the first user turn;
- for pre-write recall, retrieve again before write-like tool calls and
  regenerate that step with the matched memories. The default benchmark
  retrieves 6 pre-write candidates and injects 2, which keeps extra candidates
  visible in traces without expanding the prompt budget;
- optionally run an explicit generic scope-prompt treatment that keeps retrieved
  memories advisory and asks the agent to preserve the current task scope before
  write-like tool calls. The benchmark configs use a single benchmark-neutral
  `scope_prompt_file`; the runner still accepts `scope_prompt_files` for custom
  local experiments;
- emit artifact metadata to identify the OpenViking account, agent,
  corpus, retrieval mode, and simulator policy used by each cell.

For exploratory gates, prefer a bounded run with `--cell-timeout-seconds`.
Timed-out cells are recorded with return code `124`, `timed_out=true`, and are
excluded from scoreboard metrics, which keeps smoke runs from silently becoming
long-running evidence jobs.

The existing `train_memory_mode: experience_only` value selects the Memory V2
session-commit path. `search_memory_type` selects which generated memory bucket
is retrieved during eval (`experiences` by default, `trajectories` for
`config/trajectory.yaml`). The runner prepares each distinct
`domain + corpus_id` once and reuses it across eval run ids when the cached
`corpus_manifest.json` is present. Different corpora may be prepared in
parallel with `benchmark.corpus_prepare_concurrency`; session commits inside one
corpus remain serial to preserve OpenViking write semantics.

By default, trajectory extraction is transcript-only: the runner replays TAU-2
messages into an OpenViking session and does not expose held-out reward or
assertion results to the extractor. The PR-B evidence config can also use a
structured role/tool transcript, include the domain policy in the training
session, skip failed train sessions when building positive procedure memory, and
cap injected memory by total character budget for content-shape ablations.

Eval cells run in parallel with `benchmark.strategy_concurrency` by default and
can be overridden with `--strategy-concurrency`. This only parallelizes read-only
TAU-2 eval cells; corpus writes inside one corpus are still serialized by the
prepare step.

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

Optional fixed-first-user fixtures keep the first simulated user turn stable
while preserving live simulator behavior after that turn:

```bash
export TAU2_RETAIL_FIXED_FIRST_USER_FILE=/path/to/retail_fixture.json
export TAU2_AIRLINE_FIXED_FIRST_USER_FILE=/path/to/airline_fixture.json
```

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
