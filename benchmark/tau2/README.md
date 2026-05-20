# TAU-2 Benchmark

This directory contains a small OpenViking-style entry point for TAU-2 memory
evaluation. The scope is intentionally narrow:

- fresh OpenViking Memory V2 experience-only baseline;
- Memory V2 pre-write recall treatment.
- trajectory-view retrieval treatment for the refined trajectory prompt.

Category rerank and other harness-only diagnostics are intentionally left out.

## Layout

```text
benchmark/tau2/
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

Generated eval artifacts are written to `benchmark/tau2/result/<run_id>/`.
Memory corpus artifacts are cached outside the run id at
`benchmark/tau2/result/memory_corpora/` by default.

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

Run the Memory V2 8-trial matrix (`retail + airline` x 2 strategies x 8 repeats):

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/baseline.yaml \
  --execute
```

## Reproduce the PR-B Evidence

The PR-B headline and content-shape ablation use
`config/prb_content_matrix_new_prompt.yaml`. It runs the no-memory control plus
trajectory top4, experience top2, and 4000-character budget variants across
`retail + airline` with 8 repeats.

First run one tiny end-to-end smoke against a clean local OpenViking service:

```bash
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/prb_content_matrix_new_prompt.yaml \
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
benchmark/tau2/run_full_eval.sh \
  --config benchmark/tau2/config/prb_content_matrix_new_prompt.yaml \
  --run-id prb_content_matrix_new_prompt_full8 \
  --strict-preflight \
  --execute
```

The main result is written to
`benchmark/tau2/result/prb_content_matrix_new_prompt_full8/scoreboard.json`.
Per-cell outputs live under `cell_results/`; corpus identity and generated
memory checks live under `memory_corpora/`.

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
  regenerate that step with the matched memories. The default benchmark
  retrieves 6 pre-write candidates and injects 2, which keeps extra candidates
  visible in traces without expanding the prompt budget;
- optionally run an eval-time read selector that asks the agent LLM which
  search candidates should be read and injected, then traces
  `candidate_seen`, `selected_to_read`, `skipped_reason`, and `injected`;
- optionally run a pre-write drift retry: if the regenerated write-like tool
  calls differ from the tool calls used for the pre-write retrieval query, the
  adapter retrieves once more against the revised write set and regenerates once.
  Trace rows record `after_prewrite_regeneration` and
  `before_write_tool_call_drift_retry`; this is a diagnostic guard against stale
  pre-write context, not a task-specific rule;
- optionally run an explicit scope-prompt treatment that keeps retrieved
  memories advisory and asks the agent to preserve the current task scope before
  write-like tool calls. Configs may provide either `scope_prompt_files` or a
  `scope_prompt.domain_files` mapping;
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
assertion results to the extractor. Strategies may opt into
`train_outcome_mode: evaluator_report`, which appends train-split reward, DB
match, termination, and evaluator check details to each training session before
commit. Treat these strategies as oracle/evaluator-augmented variants with a
separate claim boundary. Outcome messages are guarded as labels, not
instructions to repeat the observed final action. Failed train samples are
marked with `memory_role: failure_reflection_only` in the extraction input and
tracked through `archive_uri/memory_diff.json` into
`failure_memory_sidecar.json`. Because OpenViking `memory_diff` may omit
generated search-scope trajectory files, the corpus prepare step also reads
matches visible through the eval `search_uri` and marks memories whose readable
result says the trajectory failed. Downstream retrieval can opt into
`compress_failure_memories: true`, which rewrites those matched memories into
short negative-boundary reflections at injection time instead of treating them
as positive procedures. Non-transcript corpora record
`train_outcome_message_version` so stale evaluator-augmented caches fail fast.

Diagnostic eval cells may also opt into `memory_read_selector: true`, which asks
the agent LLM to select which retrieved memories should be read and injected
instead of injecting by rank alone. `terminal_continuation_check` is a separate
diagnostic controller for handoff/refusal cases; it performs full agent
regeneration and is therefore capped by `terminal_continuation_max_checks`
(default `1`) per simulation. Treat continuation-controller runs as diagnostic
unless paired against no-memory controls and latency is reported.

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
