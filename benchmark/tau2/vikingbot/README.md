# VikingBot × tau2-bench Runner

This folder runs the **full VikingBot agent** (`bot/vikingbot` `AgentLoop`) end-to-end
on [tau2-bench](https://github.com/sierra-research/tau2-bench) tasks, then commits the
resulting trajectories back into OpenViking memory so the agent can **self-improve across
epochs** (cold start → memory-augmented runs).

> **Memory is extracted only from the `train` split.** Each epoch runs both splits, but only
> `train` trajectories are committed to OpenViking memory. The `test` split is **held out** and
> used purely to measure the task-success improvement once that (train-derived) memory is injected
> — so the reported gains reflect learning transferred from train to test, with no test-set leakage.

It is a sibling to the harness in [`../llm/`](../llm/README.md). Both are multi-turn and exercise
OpenViking memory extraction + retrieval; they differ in *which agent* drives the tasks:

- **[`../llm/`](../llm/README.md)** uses tau2-bench's **native ReAct agent**, wired to OpenViking
  memory, to measure the effect of that memory on task performance.
- **`vikingbot/`** (this folder) is an **end-to-end, self-improving agent** evaluation: it runs the
  full VikingBot agent loop on the tasks and commits trajectories back into memory so the agent
  improves across epochs.

The pipeline is: **run tasks → evaluate reward → commit train trajectories to memory** (see
`run_full_test.sh`).

---

## Install & Config

> **Prerequisite — Python 3.12 or 3.13.** tau2-bench requires `>=3.12,<3.14`. `setup_env.sh`
> builds the venv with `python3 -m venv`, so `python3` must resolve to 3.12/3.13. If your
> `python3` is older (e.g. 3.11), pre-create the venv with a matching interpreter *before*
> sourcing — `python3.13 -m venv .venv` — otherwise the tau2-bench install step fails with
> `ERROR: Package 'tau2' requires a different Python`.

One step sets up everything. `setup_env.sh` creates a `.venv` at the OpenViking repo root
(if one isn't already present), clones tau2-bench into `./tau2-bench` (external dependency,
gitignored), installs openviking + vikingbot with the **`[bot]` extra**
(`pip install -e .[bot]`, which also runs the Cargo build) plus tau2-bench (`[gym]` extra) and
smolagents, then activates the venv and exports the runtime env vars:

```bash
source benchmark/tau2/vikingbot/setup_env.sh              # first run: install, then activate + export
source benchmark/tau2/vikingbot/setup_env.sh --reinstall  # rebuild the .venv from scratch
```

Safe to `source` in every new shell: the install phase runs only when the venv is missing;
later sources just activate and re-export. (`--reinstall` rebuilds with `python3 -m venv`, so
the same 3.12/3.13 requirement applies.)

It exports `PYTHONPATH` for `openviking` + `bot/vikingbot`, `TAU2_DATA_ROOT`
(defaults to `./tau2-bench/data/tau2`), `OPENVIKING_CONFIG_FILE`, and the user-simulator LLM
env vars. Override any of these by exporting before sourcing:

- `TAU2_BENCH_ROOT` — tau2-bench checkout location (if it lives elsewhere)
- `TAU2_BENCH_REPO` / `TAU2_BENCH_REF` — git URL / ref to clone (e.g. pin a specific checkout)
- `VIKINGBOT_ROOT`
- `ARK_API_KEY` (mapped to `OPENAI_API_KEY`), `OPENAI_API_BASE`

The tau2 **user simulator** talks to an OpenAI-compatible endpoint — set `ARK_API_KEY` (e.g.
Doubao through volcengine ARK) before sourcing, or the simulator will fail. The user-simulator
model is configured in [`tau2_env/tau2_environment.py`](tau2_env/tau2_environment.py).

> Note: the sibling `llm/` harness ([`../llm/README.md`](../llm/README.md)) pins a tau2-bench ref
> with a confirmation-aware user-simulator prompt (sierra-research/tau2-bench#297). Set
> `TAU2_BENCH_REF` to a comparable checkout if you want results aligned with that protocol.

Then start the OpenViking server with the bot enabled:

```bash
openviking-server --config "${OPENVIKING_CONFIG_FILE}" --with-bot
```

### Provision a benchmark user

The benchmark runner should not use a root/admin key for runtime memory access. Use a
provisioning key only to create a synthetic benchmark user and generate a user-key config:

```bash
export OPENVIKING_PROVISION_API_KEY="<root-or-admin-key>"

python benchmark/tau2/vikingbot/scripts/provision_openviking_user.py \
  --account default \
  --user tau2_airline_v0 \
  --base-config "${OPENVIKING_CONFIG_FILE}" \
  --out benchmark/tau2/vikingbot/.generated/tau2_airline_v0.ov.conf
```

The generated config stores the returned user key in `bot.ov_server.root_api_key` with
`bot.ov_server.api_key_type="user"`. That field name is historical VikingBot config shape; at
runtime it is a user key, not a root key. The provision key is not written into the runtime config.

Use a different generated config for each isolated benchmark user. If all domains run with the
same user-key config, they intentionally share the same OpenViking user memory.

---

## One-click full run (recommended)

Run one epoch — **1 train run + 8 test runs in parallel** (`--test-repeats`, default 8) — then
evaluate (test accuracy is averaged over the repeats) and commit the train trajectories to memory:

```bash
bash benchmark/tau2/vikingbot/run_full_test.sh \
  --domain airline \
  --epoch 0 \
  --result-dir result \
  --config benchmark/tau2/vikingbot/.generated/tau2_airline_v0.ov.conf
```

The async memory commit happens on the server, so wait for it to
finish before starting the next epoch. The per-domain report is appended to
`full_test_report_<domain>.txt`.

Multi-epoch examples (cold start → memory-augmented epochs):

```bash
TAU2_VIKINGBOT_CONFIG=benchmark/tau2/vikingbot/.generated/tau2_airline_v0.ov.conf \
bash benchmark/tau2/vikingbot/run_airline_2epochs.sh
```

---

## Run each step separately

### 1) Run tasks (train / test)

**train** runs **once per epoch** — one trajectory per task, which is what gets committed to memory
as the experience corpus. A single pass mirrors real usage, where the agent learns from one attempt
at each task.

**test** runs **8 times per epoch (in parallel) and is averaged**. Agent execution is stochastic, so
averaging several independent repeats gives a more confident accuracy estimate. Each repeat is a
separate `--try-no`; `run_eval_reward.sh` scores one repeat, and the per-repeat accuracies are
averaged. The one-click `run_full_test.sh` does all of this for you (`--test-repeats`, default 8).

```bash
# train: a single run (try 0)
bash scripts/run_tau2_domain.sh \
  --domain airline --split train --epoch 0 --try-no 0 \
  --result-dir result --concurrency 5 --use-continue \
  --config .generated/tau2_airline_v0.ov.conf

# test: 8 independent runs (try 0..7)
for t in 0 1 2 3 4 5 6 7; do
  bash scripts/run_tau2_domain.sh \
    --domain airline --split test --epoch 0 --try-no "$t" \
    --result-dir result --concurrency 5 --use-continue \
    --config .generated/tau2_airline_v0.ov.conf
done
```

Results are written to `result/<domain>_<split>/task_<n>_<epoch>_<try>_trajectory.json`,
and full message dumps to the mirrored `trajectory/...` path.

### 2) Evaluate rewards

```bash
bash scripts/run_eval_reward.sh result/airline_train 0 0
bash scripts/run_eval_reward.sh result/airline_test 0 0
```

### 3) Commit trajectories to memory

> VikingBot natively commits a task's trajectory into memory automatically as soon as that task finishes.
> For these experiments that auto-commit is **disabled**, so that all
> tasks within an epoch (train + test, run in parallel) execute under identical memory conditions — no run sees memory written by a sibling run mid-experiment.
> Instead, the commit is performed explicitly as a separate, controlled step via the script below (run once between epochs).

```bash
python scripts/commit_trajectory_to_memory.py \
  --input result/airline_train \
  --config .generated/tau2_airline_v0.ov.conf \
  --pattern "*_0_0_trajectory.json" \
  --include-eval-result
```

---

## How the runner adapts VikingBot for tau2

Adaptation happens in two places:

1. **Runner-level** (this folder only) — swap the agent's tool set over to the tau2 environment
   tools, gate OpenViking memory by epoch (cold start vs. memory-augmented), and commit train
   trajectories between epochs. Detailed below.
2. **`ov.conf` flags** — three OpenViking config flags switch VikingBot core into the
   experience-memory recall mode tau2 needs. **No core code edits required.** See
   [Required `ov.conf` flags for tau2](#required-ovconf-flags-for-tau2) below.

VikingBot runtime does not receive deprecated agent identity, peer identity, or a synthetic user
override; it only consumes the configured authenticated user. Per-domain isolation is achieved by
provisioning a separate user-key config before the run.

### Runner-level adaptations:

- **Tool registry swap** — the tau2 environment tools are injected into VikingBot's `ToolRegistry`
  via `agent.tools.register(Tau2Tool(...))`, so the agent drives the task through tau2's own tools
  (plus `communicate_with_user` and `done`). `openviking_memory_commit` is **always** unregistered
  here — that is the mechanism that disables VikingBot's per-task auto-commit (see step 3 above).
  - **`--keep-default-tools` controls memory availability, tied to the epoch.** The flag decides
    whether VikingBot's built-in memory tools — **OpenViking memory tools** — stay
    registered, and whether agent-experience memory is retrieved into the system prompt
    (`ov_tools_enable`). `run_full_test.sh` sets it by epoch: **epoch 0 omits the flag**, so all
    built-in memory tools are unregistered (only tau2 tools remain) and no memory is injected — a clean
    **cold-start / no-memory** run; **epoch > 0 passes the flag**, so the memory tools and retrieved
    experiences are available (memory-augmented).
- **Epoch-based memory commit** — `commit_trajectory_to_memory.py` writes train trajectories
  (optionally only failed ones, via `--only-wrong`) into OpenViking memory between epochs.

### Identity model

The benchmark does not pass identity through VikingBot. A provisioned user-key config determines
the OpenViking runtime identity:

```
provision_openviking_user.py  ->  .generated/tau2_airline_v0.ov.conf
run_full_test.sh --config .generated/tau2_airline_v0.ov.conf
  -> VikingBot uses that user key
  -> OpenViking resolves user=tau2_airline_v0
  -> memory is stored under viking://user/tau2_airline_v0/memories/
```

The provision key is control-plane only. It creates or refreshes the benchmark user key through the
Admin API, then leaves the runtime path.

### Required `ov.conf` flags for tau2

Two VikingBot behaviours need to change for tau2 self-improvement:

1. **Per-domain workspace isolation** — each tau2 domain (airline, retail, …) must read and
   write its own OpenViking namespace so experiences learned on one domain don't leak into
   another.
2. **Recall experience memory once per task** — by default VikingBot pulls user memory into every
   turn. For tau2 we want accumulated **experience** memory pulled once per task, with a larger
   character budget per experience.

Both are now controlled by config — set these three flags in the `bot.ov_server` section of the
`ov.conf` pointed to by `OPENVIKING_CONFIG_FILE`:

```jsonc
{
  "bot": {
    "ov_server": {
      "recall_exp_first_round_only": true,  // skip per-turn recall; inject exp once on the first user turn
      "exp_recall_limit": 2,                // fetch 2 experiences per task (default: 5)
      "exp_recall_max_chars": 10000         // character budget for the injected experience block (default: 2000)
    }
  }
}
```

What each flag does:

- **`recall_exp_first_round_only`** — when `true`, `ContextBuilder._build_user_memory` skips the
  default per-turn memory recall and instead calls `get_viking_experience_context` once, on the
  first user-turn of the session. The runner only ever sends one user turn per task, so this
  becomes "fetch experience once per task."
- **`exp_recall_limit`** — how many experiences to retrieve. tau2 prefers **fewer but longer**
  (2) over many shallow hits (5).
- **`exp_recall_max_chars`** — total character budget for the formatted experience block. Bumped
  to **10000** so each of the 2 experiences gets room for full context (default 2000 truncates).

Workspace isolation does not need a legacy namespace flag. The generated user-key config is the
runtime identity, so each domain reads and writes `viking://user/<domain_user>/...`.


---

## Layout

- `setup_env.sh` — environment setup (PYTHONPATH, tau2 data root, simulator LLM)
- `run_full_test.sh` — full pipeline for one epoch (run → eval → commit)
- `run_airline_2epochs.sh` — multi-epoch example (cold start → memory-augmented epochs)
- `tau2_env/` — tau2 environment integration (`tau2_environment.py`, `tau2_tool_provider.py`)
- `scripts/`
  - `provision_openviking_user.py` — create/refresh a benchmark user and write a user-key config
  - `vikingbot_tau2_runner.py` — runs a single tau2 task through the VikingBot agent loop
  - `run_tau2_domain.sh` — runs all tasks in a `{domain}_{split}` slice with bounded concurrency
  - `run_eval_reward.sh` — average reward over a result folder
  - `commit_trajectory_to_memory.py` — commit trajectories into OpenViking memory
