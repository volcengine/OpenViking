# VikingBot × tau2-bench Runner

This folder runs the **full VikingBot agent** (`bot/vikingbot` `AgentLoop`) end-to-end
on [tau2-bench](https://github.com/sierra-research/tau2-bench) tasks, then commits the
resulting trajectories back into OpenViking memory so the agent can **self-improve across
epochs** (cold start → memory-augmented runs).

It is a sibling to the memory-retrieval harness in [`../`](../README.md):

| | [`benchmark/tau2/`](../README.md) | `benchmark/tau2/vikingbot/` (this folder) |
|---|---|---|
| What runs | OpenViking Memory V2 adapter (retrieval-only) | The full VikingBot agent loop |
| Tools the agent sees | n/a (transcript replay) | tau2 env tools (+ optional OV memory tools) |
| Loop | single retrieval at first user turn | multi-iteration agent loop, multi-epoch memory commit |
| Use it for | measuring memory-retrieval quality | measuring end-to-end task success with self-improving memory |

The pipeline is: **run tasks → evaluate reward → commit train trajectories to memory** (see
`run_full_test.sh`).

---

## Install & Config

### 1) OpenViking + VikingBot

The runner imports both `openviking` and `vikingbot`. The simplest setup puts the OpenViking
repo root and its `bot/` directory on `PYTHONPATH` (no pip install of `bot` needed):

```bash
source benchmark/tau2/vikingbot/setup_env.sh
```

`setup_env.sh` activates `./.venv` if present, exports `PYTHONPATH` for `openviking` + `bot/vikingbot`,
and sets `TAU2_DATA_ROOT`, `OPENVIKING_CONFIG_FILE`, and the user-simulator LLM env vars.
Override any of them by exporting before sourcing (e.g. `TAU2_BENCH_ROOT`, `VIKINGBOT_ROOT`,
`ARK_API_KEY`).

Then start the OpenViking server with the bot enabled:

```bash
openviking-server --config "${OPENVIKING_CONFIG_FILE}" --with-bot
```

### 2) tau2-bench (external dependency)

tau2-bench is **not vendored** here. Clone it into this folder (`./tau2-bench`, gitignored)
or anywhere and point `TAU2_BENCH_ROOT` / `TAU2_DATA_ROOT` at it:

```bash
git clone https://github.com/sierra-research/tau2-bench benchmark/tau2/vikingbot/tau2-bench
pip install -e benchmark/tau2/vikingbot/tau2-bench
pip install smolagents
export TAU2_DATA_ROOT=benchmark/tau2/vikingbot/tau2-bench/data/tau2
```

The tau2 **user simulator** talks to an OpenAI-compatible endpoint. Provide credentials via
`OPENAI_API_KEY` / `OPENAI_API_BASE` (e.g. Doubao through volcengine ARK — set `ARK_API_KEY`,
which `setup_env.sh` maps to `OPENAI_API_KEY`). The user-simulator model is configured in
[`tau2_env/tau2_environment.py`](tau2_env/tau2_environment.py).

> Note: the parent harness ([`../README.md`](../README.md)) pins a tau2-bench ref with a
> confirmation-aware user-simulator prompt (sierra-research/tau2-bench#297). Use a comparable
> checkout if you want results aligned with that protocol.

---

## One-click full run (recommended)

Run one epoch (train + test in parallel), evaluate, then commit train trajectories to memory:

```bash
bash benchmark/tau2/vikingbot/run_full_test.sh --domain airline --epoch 0 --result-dir result
```

Run one domain at a time. The async memory commit happens on the server, so wait for it to
finish before starting the next epoch. The per-domain report is appended to
`full_test_report_<domain>.txt`.

Multi-epoch examples (cold start → memory-augmented epochs):

```bash
bash benchmark/tau2/vikingbot/run_airline_2epochs.sh
bash benchmark/tau2/vikingbot/run_retail_3epochs.sh
```

---

## Run each step separately

### 1) Run tasks (train / test)

```bash
bash scripts/run_tau2_domain.sh \
  --domain airline --split train --epoch 0 --try-no 0 \
  --result-dir result --concurrency 5 --use-continue --agent-id airline_v2
```

Results are written to `result/<domain>_<split>/task_<n>_<epoch>_<try>_trajectory.json`,
and full message dumps to the mirrored `trajectory/...` path.

### 2) Evaluate rewards

```bash
bash scripts/run_eval_reward.sh result/airline_train 0 0
bash scripts/run_eval_reward.sh result/airline_test 0 0
```

### 3) Commit trajectories to memory

```bash
python scripts/commit_trajectory_to_memory.py \
  --input result/airline_train \
  --domain airline_v2 \
  --pattern "*_0_0_trajectory.json" \
  --include-eval-result
```

Helper utilities: `scripts/stat_trajectory.py` (token / tool-call stats) and
`scripts/check_openviking_tool_calls.py` (count runs that invoked `openviking_*` tools).

---

## How the runner adapts VikingBot for tau2

These adaptations live entirely in this folder's runner — **no OpenViking core changes are
required**:

- **Tool registry swap** — by default the agent's built-in tools are unregistered and only the
  tau2 environment tools (plus `communicate_with_user`) are registered; pass `--keep-default-tools`
  to also keep VikingBot/OpenViking tools (e.g. memory).
- **Simulated time** — `_patch_sim_time` rewrites the agent's "Current Time" to tau2's fixed
  simulation time so behavior is reproducible.
- **Advisory memory scope guard** — an inline guard prompt (`SCOPE_PROMPT`) is appended to keep
  retrieved memories advisory and prevent the agent from broadening the user's requested scope
  before write-like tool calls. (The parent harness ships an equivalent treatment as
  `../config/scope_prompts/generic_memory_scope.md`.)
- **Epoch-based memory commit** — `commit_trajectory_to_memory.py` writes train trajectories
  (optionally only failed ones, via `--only-wrong`) into OpenViking memory between epochs.

---

## Layout

- `setup_env.sh` — environment setup (PYTHONPATH, tau2 data root, simulator LLM)
- `run_full_test.sh` — full pipeline for one epoch (run → eval → commit)
- `run_airline_2epochs.sh`, `run_retail_3epochs.sh` — multi-epoch examples
- `tau2_env/` — tau2 environment integration (`tau2_environment.py`, `tau2_tool_provider.py`)
- `scripts/`
  - `vikingbot_tau2_runner.py` — runs a single tau2 task through the VikingBot agent loop
  - `run_tau2_domain.sh` — runs all tasks in a `{domain}_{split}` slice with bounded concurrency
  - `run_eval_reward.sh` — average reward over a result folder
  - `commit_trajectory_to_memory.py` — commit trajectories into OpenViking memory
  - `stat_trajectory.py`, `check_openviking_tool_calls.py` — analysis helpers
