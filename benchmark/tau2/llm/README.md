# TAU-2 Benchmark

This directory contains the OpenViking TAU-2 LLM benchmark entry point. The
reproduction surface is intentionally narrow:

- `no_memory`: same-seed TAU-2 baseline without OpenViking memory injection;
- `template_indexed_trajectory_top4_prewrite_top2`: the current best
  template-indexed trajectory memory treatment.

The template-indexed trajectory treatment trains OpenViking Memory V2 from
TAU-2 train conversations, retrieves generated `trajectories`, and uses the
trajectory embedding template `{{ trajectory_name }}\n\n{{ retrieval_anchor }}`
instead of broad procedure bodies for retrieval. It injects trajectory top4 at
the first user turn and top2 before write-like tool calls, with the generic
memory scope prompt enabled.

Category rerank, experience-memory routes, fixed-count-only ablations,
character-budget ablations, and official-user parity controls are intentionally
left out of this README and config set so reproduction agents do not mistake
diagnostic routes for current evidence.

## Layout

```text
benchmark/tau2/llm/
├── config/
│   ├── baseline.yaml
│   ├── fixed_first_user_bootstrap.yaml
│   ├── no_memory.yaml
│   ├── scope_prompts/
│   │   └── generic_memory_scope.md
│   └── template_indexed_trajectory.yaml
├── scripts/
│   ├── build_fixed_first_user_fixture.py
│   ├── run_eval.py
│   ├── setup_tau2_repo.sh
│   └── tau2_common.py
└── run_full_eval.sh
```

`baseline.yaml` is a shared protocol/defaults file, not a runnable evidence
cell by itself. Use `no_memory.yaml` for the baseline-only run and
`template_indexed_trajectory.yaml` for the paired no-memory + trajectory run.

Generated eval artifacts are written to `benchmark/tau2/llm/result/<run_id>/`.
Memory corpus artifacts are cached outside the run id at
`benchmark/tau2/llm/result/memory_corpora/` by default.

## Setup

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
benchmark/tau2/llm/scripts/setup_tau2_repo.sh
source benchmark/tau2/llm/.env.tau2
```

The default OpenViking TAU-2 memory evidence protocol is
`fixed_first_user_full8`: retail + airline, 8 repeats, same seeds,
confirmation-aware user simulator, and fixed first-user fixtures for both
domains. Later user simulator turns remain live.

The confirmation-aware simulator behavior is available from
[sierra-research/tau2-bench#297](https://github.com/sierra-research/tau2-bench/pull/297).
Pin the local TAU-2 checkout to a ref that includes that behavior when
reproducing these numbers:

```bash
benchmark/tau2/llm/scripts/setup_tau2_repo.sh \
  --ref refs/pull/297/head
source benchmark/tau2/llm/.env.tau2
```

When using Doubao through an OpenAI-compatible endpoint, set `OPENAI_API_KEY`
and `OPENAI_API_BASE` for LiteLLM before running upstream TAU-2.

## Fixed-First-User Fixtures

Strict reproduction requires fixed first-user fixtures:

```bash
export TAU2_RETAIL_FIXED_FIRST_USER_FILE=/path/to/retail/fixed_first_user_fixture.json
export TAU2_AIRLINE_FIXED_FIRST_USER_FILE=/path/to/airline/fixed_first_user_fixture.json
```

`--strict-preflight` fails when `eval.require_fixed_first_user=true` and either
fixture is missing.

For a fresh checkout, run one live-user bootstrap pass per domain:

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

## Run Plans And Smoke Checks

Plan the no-memory baseline without running TAU-2:

```bash
python benchmark/tau2/llm/scripts/run_eval.py \
  --config benchmark/tau2/llm/config/no_memory.yaml \
  --plan-only
```

Plan the paired current-evidence config without running TAU-2:

```bash
python benchmark/tau2/llm/scripts/run_eval.py \
  --config benchmark/tau2/llm/config/template_indexed_trajectory.yaml \
  --plan-only
```

Run a tiny no-memory smoke:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/no_memory.yaml \
  --domain retail \
  --strategy-id no_memory \
  --num-tasks 1 \
  --repeat-count 1 \
  --strict-preflight \
  --execute
```

Run a tiny template-indexed trajectory smoke against a clean local OpenViking
service:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/template_indexed_trajectory.yaml \
  --domain retail \
  --strategy-id template_indexed_trajectory_top4_prewrite_top2 \
  --num-tasks 1 \
  --train-num-tasks 1 \
  --repeat-count 1 \
  --strict-preflight \
  --execute
```

Start the OpenViking service before executing memory cells, and verify it with
`ov status`. For trajectory memory evidence, start the service from this branch
and inspect generated trajectory files; changing `search_uri` alone does not
prove the template-indexed trajectory prompt was used.

## Full Reproduction

Run the no-memory full8 baseline:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/no_memory.yaml \
  --run-id no_memory_full8 \
  --strict-preflight \
  --execute
```

Run the paired no-memory + current trajectory evidence config:

```bash
benchmark/tau2/llm/run_full_eval.sh \
  --config benchmark/tau2/llm/config/template_indexed_trajectory.yaml \
  --run-id template_indexed_trajectory_full8 \
  --strict-preflight \
  --execute
```

The main result is written to
`benchmark/tau2/llm/result/template_indexed_trajectory_full8/scoreboard.json`.
Per-cell execution records live under `cell_results/`, raw TAU-2 result JSON
lives under `memory_cells/`, and corpus identity / generated memory checks live
under `memory_corpora/`.

## Memory Adapter

Memory cells run through a small TAU-2 agent adapter in this directory:

- train by writing TAU-2 training conversations into OpenViking sessions;
- retrieve OpenViking memory at the first user turn;
- for pre-write recall, retrieve again before write-like tool calls and
  regenerate that step with the matched memories;
- optionally apply a generic scope prompt that keeps retrieved memories
  advisory and asks the agent to preserve the current task scope before
  write-like tool calls;
- emit artifact metadata identifying the OpenViking account, agent, corpus,
  retrieval mode, search memory type, and simulator policy used by each cell.

The current trajectory config uses:

- `train_memory_mode: experience_only`, which selects the Memory V2
  session-commit path that writes generated memory artifacts;
- `train_transcript_format: role_tool_blocks`, which preserves role-prefixed
  messages plus tool-call/tool-response blocks during training;
- `train_include_system_prompt: true`, which includes the domain policy in the
  training session;
- `train_skip_failed_sessions: true`, which avoids learning from failed train
  sessions;
- `search_memory_type: trajectories`, which retrieves generated trajectory
  memory during eval.

The runner prepares each distinct `domain + corpus_id` once and reuses it across
eval run ids when the cached `corpus_manifest.json` is present. Different
corpora may be prepared in parallel with `benchmark.corpus_prepare_concurrency`;
session commits inside one corpus remain serial to preserve OpenViking write
semantics.

By default, trajectory extraction is transcript-only: the runner replays TAU-2
messages into an OpenViking session and does not expose held-out reward or
assertion results to the extractor.

Eval cells run in parallel with `benchmark.strategy_concurrency` by default and
can be overridden with `--strategy-concurrency`. This only parallelizes read-only
TAU-2 eval cells; corpus writes inside one corpus are still serialized by the
prepare step.

For exploratory gates, prefer a bounded run with `--cell-timeout-seconds`.
Timed-out cells are recorded with return code `124`, `timed_out=true`, and are
excluded from scoreboard metrics, which keeps smoke runs from silently becoming
long-running evidence jobs.

## User Simulator Policy

The runner default is the official TAU-2 user simulator if
`eval.user_simulator_policy` is omitted. The bundled OpenViking memory benchmark
configs set `confirmation_aware`, because a memory benchmark should not treat
user confirmation as task completion before the backend write has happened.

`confirmation_aware` applies a small idempotent prompt patch to the configured
TAU-2 checkout before planning or running. The patch appends only the behavioral
confirmation boundary to the TAU-2 user simulator guidelines; metadata such as
the upstream PR link is kept in run artifacts, not in the simulator prompt.

Optional fixed-first-user fixtures keep the first simulated user turn stable
while preserving live simulator behavior after that turn.

## Evidence Boundary

Only completed `retail + airline` runs with the same config, same seeds/repeats,
and non-empty artifacts should be read as benchmark evidence. Partial runs,
single-task probes, or missing OpenViking corpus identity are diagnostics.
Executed runs write per-cell JSON under `cell_results/` and a strategy/domain
aggregate under `scoreboard.json`. Memory training artifacts are shared by
domain and strategy under `memory_corpora/`, so repeated eval cells reuse the
same fresh corpus instead of rewriting it.
