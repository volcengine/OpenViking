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

One step sets up everything. `setup_env.sh` creates a fresh `.venv` at the OpenViking repo
root, clones tau2-bench into `./tau2-bench` (external dependency, gitignored), installs
openviking + vikingbot (`pip install -e .`, which also runs the Cargo build) and tau2-bench,
installs smolagents, then activates the venv and exports the runtime env vars:

```bash
source benchmark/tau2/vikingbot/setup_env.sh              # first run: install, then activate + export
source benchmark/tau2/vikingbot/setup_env.sh --reinstall  # rebuild the .venv from scratch
```

Safe to `source` in every new shell: the install phase runs only when the venv is missing;
later sources just activate and re-export.

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


---

## One-click full run (recommended)

Run one epoch — **1 train run + 8 test runs in parallel** (`--test-repeats`, default 8) — then
evaluate (test accuracy is averaged over the repeats) and commit the train trajectories to memory:

```bash
bash benchmark/tau2/vikingbot/run_full_test.sh --domain airline --epoch 0 --result-dir result
```

The async memory commit happens on the server, so wait for it to
finish before starting the next epoch. The per-domain report is appended to
`full_test_report_<domain>.txt`.

Multi-epoch examples (cold start → memory-augmented epochs):

```bash
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
  --result-dir result --concurrency 5 --use-continue --agent-id airline_v0

# test: 8 independent runs (try 0..7)
for t in 0 1 2 3 4 5 6 7; do
  bash scripts/run_tau2_domain.sh \
    --domain airline --split test --epoch 0 --try-no "$t" \
    --result-dir result --concurrency 5 --use-continue --agent-id airline_v0
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
  --domain airline_v0 \
  --pattern "*_0_0_trajectory.json" \
  --include-eval-result
```

---

## How the runner adapts VikingBot for tau2

There are **two layers** of adaptation:

1. **Runner-level** (this folder only, no core edits) — swap the agent's tool set over to the tau2
   environment tools, gate OpenViking memory by epoch (cold start vs. memory-augmented), and commit
   train trajectories between epochs. Detailed below.
2. **Core `bot/vikingbot`** edits — required for per-domain workspace isolation and for reading
   *agent* (experience) memory instead of *user* memory. See
   [Core `bot/vikingbot` changes for tau2](#core-botvikingbot-changes-for-tau2) below.

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


### Core `bot/vikingbot` changes for tau2

Two behaviours in the VikingBot core
(`bot/vikingbot`) had to be changed for tau2:

1. **Per-domain workspace isolation via `agent_id`** 
   By default VikingBot do not support agent id
    isolation in local mode. In order to easily test in local, changes are made so that each tau2 domain (airline, retail, …) read/write its own OpenViking namespace so experiences learned on one domain don't leak into another.
2. **Read *agent* (experience) memory at system-prompt build time** 
    By default VikingBot retrieves *user* memory when assembling the system message. For tau2 self-improvement we want to isolate the effect of the agent's own accumulated **experience** agent memory instead.


### Change 1 — `agent_id` isolation in `VikingClient` (`openviking_mount/ov_server.py`)

In `local` mode the client used to hardcode `agent_id = "default"` for every caller, so all
domains shared one namespace. It now reads the incoming `agent_id` and only falls back to
`"default"` when the id is a normal session key (which contains `__`, e.g. `cli__default`):

```diff
 if openviking_config.mode == "local":
-    self.client = ov.AsyncHTTPClient(url=openviking_config.server_url)
-    self.agent_id = "default"
+    if agent_id is None or "__" in agent_id:
+        self.client = ov.AsyncHTTPClient(url=openviking_config.server_url)
+        self.agent_id = "default"
+    else:
+        self.client = ov.AsyncHTTPClient(
+            url=openviking_config.server_url,
+            agent_id=agent_id,
+        )
+        self.agent_id = agent_id
     self.account_id = "default"
     self.user_id = "default"
     self.admin_user_id = "default"
     return
```

(The `agent_id is None` guard is needed because `agent_id` defaults to `None`, and `"__" in None`
would raise `TypeError`.)

`search_experiences` then resolves the experience URI from this per-instance `agent_id` (a clean
domain id like `airline_v1` contains a single `_`), instead of always using the global config
`agent_id`:

```diff
 async def search_experiences(self, query: str, limit: int = 5) -> list[Any]:
     """用 query 检索 agent experience 记忆。"""
     effective_agent_id = self.openviking_config.agent_id or "default"
+    if self.agent_id and "_" in self.agent_id:
+        effective_agent_id = self.agent_id
     exp_uri = f"viking://agent/{effective_agent_id}/memories/experiences/"
     result = await self.search(query=query, target_uri=exp_uri, limit=limit)
     return result.get("memories", [])
```


#### How `agent_id` flows from the runner into the core

The domain id is threaded all the way down into the OpenViking client:

```
run_full_test.sh                AGENT_ID=${DOMAIN}_v1            # e.g. airline_v1, retail_v1
  └─ run_tau2_domain.sh         --agent-id airline_v1
       └─ vikingbot_tau2_runner.py
            build_messages(..., memory_users=agent_id)          # memory_users == "airline_v1"
              └─ context.py  _build_user_memory
                   workspace_id = memory_users                  # workspace_id == "airline_v1"
                   └─ memory.py  get_viking_experience_context(query, workspace_id)
                        └─ _create_client(workspace_id)
                             └─ VikingClient.create(agent_id=workspace_id)
                                  └─ ov_server.py  VikingClient.__init__ / search_experiences
                                       viking://agent/airline_v1/memories/experiences/
```

So `--agent-id airline_v1` ⇒ the agent reads/writes `viking://agent/airline_v1/...`, giving each
domain an isolated workspace.

### Change 2 — system prompt reads *agent experience* memory (`agent/context.py`)

`_build_user_memory` (called by `build_messages` when assembling the system prompt) used to call
`get_viking_memory_context`, which retrieves **user** memory. For tau2 it now binds `workspace_id`
to the passed-in `memory_users` (the domain `agent_id`) and calls `get_viking_experience_context`
to retrieve the agent's **experience** memory:

```diff

         # Viking agent memory (only if ov tools are enabled)
         if ov_tools_enable:
             start = _time.time()
             # Use provided memory_users or fall back to [sender_id]
             search_user_ids = memory_users if memory_users else [sender_id]
-            viking_memory = await self.memory.get_viking_memory_context(
-                current_message=current_message,
-                workspace_id=workspace_id,
-                sender_id=sender_id,
-                user_ids=search_user_ids,
-            )
+            workspace_id = memory_users
+
+            viking_memory = await self.memory.get_viking_experience_context(
+                query=current_message,
+                workspace_id=workspace_id,
+            )
             logger.info(f"viking_memory={viking_memory}")
             cost = round(_time.time() - start, 2)
             logger.info(
-                f"[READ_USER_MEMORY]: cost {cost}s, memory={viking_memory[:50] if viking_memory else 'None'}"
+                f"[READ_AGENT_MEMORY]: cost {cost}s, memory={viking_memory[:50] if viking_memory else 'None'}"
             )
             if viking_memory:
                 self.latest_relevant_memories = viking_memory
-                parts.append(f"## openviking_search(query=[user_query])\n{viking_memory}")
+                parts.append(f"## openviking_search(query=[user_query])\n ## Agent Experience (relevant to this task)\n {viking_memory}")
             else:
                 self.latest_relevant_memories = None
```

The key line is `workspace_id = memory_users`: the runner passes the domain id as `memory_users`,
and it becomes the `agent_id` used to open the (isolated) OpenViking client downstream.

### Change 3 — supporting edits in `agent/memory.py`

`get_viking_experience_context` is the experience-retrieval path that Change 2 now calls. 

Retrieval limits in `get_viking_experience_context` were also tuned for tau2 (fewer experiences,
more characters per experience):

```diff
-            experiences = await client.search_experiences(query, limit=5)
+            experiences = await client.search_experiences(query, limit=2)
 ...
             return await self._parse_viking_memory(
-                experiences, client, min_score=0.3, max_chars=2000
+                experiences, client, min_score=0.3, max_chars=10000
             )
```


---

## Layout

- `setup_env.sh` — environment setup (PYTHONPATH, tau2 data root, simulator LLM)
- `run_full_test.sh` — full pipeline for one epoch (run → eval → commit)
- `run_airline_2epochs.sh` — multi-epoch example (cold start → memory-augmented epochs)
- `tau2_env/` — tau2 environment integration (`tau2_environment.py`, `tau2_tool_provider.py`)
- `scripts/`
  - `vikingbot_tau2_runner.py` — runs a single tau2 task through the VikingBot agent loop
  - `run_tau2_domain.sh` — runs all tasks in a `{domain}_{split}` slice with bounded concurrency
  - `run_eval_reward.sh` — average reward over a result folder
  - `commit_trajectory_to_memory.py` — commit trajectories into OpenViking memory
