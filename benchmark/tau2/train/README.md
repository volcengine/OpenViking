# Tau2 Train/Eval Pipeline

Tau2 training/evaluation uses the generic OpenViking session/train batch
pipeline. In day-to-day runs, use
`benchmark/tau2/train/restart_vikingbot_train_eval.sh` as the main entrypoint:
it restarts the required services, points them at the same slot/config, waits for
health checks, and then launches the batch runner.

`benchmark/tau2/train/run_batch_train_eval.sh` is only the lower-level Tau2
wrapper. Use it when you have already started OpenViking and the Tau2 rollout
service yourself.

## 1. Main entrypoint: restart VikingBot train/eval

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh
```

What the launcher does:

1. prepares the OpenViking config/data directory for the selected slot;
2. restarts OpenViking and the VikingBot API;
3. waits for `http://127.0.0.1:<ov-port>/bot/v1/health`;
4. restarts the Tau2 rollout service with `--rollout-backend vikingbot`;
5. waits for `http://127.0.0.1:<tau2-port>/health`;
6. runs `benchmark/tau2/train/run_batch_train_eval.sh` with the matching
   `--config`, `--server-url`, `--benchmark-service-url`, and result directory.

Default train/eval arguments, when no custom train/eval args are passed, are:

```bash
--commit-concurrency 200 --epochs 2 --trials 8 --train-trials 1 --skip-final-eval
```

If you pass any train/eval arguments to `restart_vikingbot_train_eval.sh`, that
custom argument list replaces the launcher's default list, so include the options
you still want, such as `--skip-final-eval`.

Example: train one task and evaluate the same train task for 8 trials after each
epoch, without pre-training baseline or extra final eval:

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --epochs 2 \
  --train-index 14 \
  --eval-split train \
  --eval-index 14 \
  --trials 8 \
  --train-trials 1 \
  --skip-baseline-eval \
  --skip-final-eval
```

Example: reuse the cached epoch-0/no-memory train rollout if it already exists;
on cache miss, run the rollout normally and write the cache:

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --epochs 3 \
  --train-index 5 \
  --eval-split train \
  --eval-index 5 \
  --trials 8 \
  --train-trials 4 \
  --skip-baseline-eval \
  --skip-final-eval \
  --reuse-train-rollout-cache
```

`--reuse-train-rollout-cache` is off by default and only affects training
rollouts for epoch `0`, before memory training has changed the policy. Later
training epochs and eval rollouts are always executed normally.

## 2. Evaluation modes from the restart launcher

The restart launcher always runs the full VikingBot path. Evaluation behavior is
controlled by the train/eval args passed after any launcher-only options.

### Eval-only score

Use `--epochs 0` to restart services and run evaluation without training:

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --epochs 0 \
  --eval-index 24 \
  --trials 8
```

By default, eval uses the `test` split. Use `--eval-split train` to evaluate on
train tasks, or `--eval-split none` to disable eval.

### Training with baseline and per-epoch eval

The Tau2 wrapper enables `--eval-each-epoch`, so a normal training run evaluates
after every epoch using `--eval-split` and `--eval-index`.

Before training, the runner also computes a baseline eval unless
`--skip-baseline-eval` is set. For the same dataset/domain, eval indices, trials,
and rollout options, the baseline is cached under
`result/tau2/<result-dir-name>/cache/baseline/` and reused by later runs. Use
`--force-baseline-recompute` only when you intentionally want to refresh it.

For quick train-split iteration, the common pattern is:

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --epochs 3 \
  --train-index 5 \
  --eval-split train \
  --eval-index 5 \
  --trials 8 \
  --train-trials 4 \
  --skip-baseline-eval \
  --skip-final-eval \
  --reuse-train-rollout-cache
```

Use `--skip-final-eval` to avoid the extra final eval pass. This is common with
Tau2 because per-epoch eval is already enabled.

## 3. Multiple isolated slots

The restart launcher accepts a launcher-only `--slot N` before the normal
train/eval arguments. Slot `0` is the default legacy setup. Slot `N > 0` uses
independent ports, OpenViking config/data, logs, and result directory so multiple
experiments can run at the same time:

| Slot value | OpenViking port | VikingBot port | Tau2 service port | OpenViking root | Result directory |
|------------|-----------------|----------------|-------------------|-----------------|------------------|
| `0` | `1933` | `18790` | `1944` | `~/.openviking` | `result/tau2/train` |
| `1` | `1934` | `18791` | `1945` | `~/.openviking_1` | `result/tau2/train_1` |
| `N` | `1933 + N` | `18790 + N` | `1944 + N` | `~/.openviking_N` | `result/tau2/train_N` |

Example: run slot 1 without touching slot 0 services or data:

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --slot 1 \
  --epochs 2 \
  --train-index 14 \
  --eval-split train \
  --eval-index 14 \
  --trials 8 \
  --train-trials 1 \
  --skip-baseline-eval \
  --skip-final-eval
```

Environment variables such as `OPENVIKING_PORT`, `OPENVIKING_BOT_PORT`,
`TAU2_SERVICE_PORT`, `OPENVIKING_CONFIG_FILE`, `OPENVIKING_DATA_DIR`,
`RESULT_DIR_NAME`, and `LOG_DIR` can still override the slot-derived defaults.
For non-zero slots, the launcher copies base `~/.openviking/*.conf*` config files
when needed and rewrites the slot config's `storage.workspace`, `server.port`,
`server.bot_api_url`, and `bot.ov_server.server_url`.

The launcher writes service logs and pid files under:

```text
result/tau2/<result-dir-name>/service_logs/
```

## 4. Options

### Launcher-only options

| Option | Default | Description |
|--------|---------|-------------|
| `--slot N` | `0` | Run an isolated experiment slot. Must appear before train/eval args. |

### Common train/eval options

| Option | Default | Description |
|--------|---------|-------------|
| `--domain` | `airline` | Benchmark domain to run |
| `--epochs` | `1`; restart default `2` | Number of training epochs. Use `0` for eval-only. |
| `--batch-size` | whole split | Train/eval batch size (cases per batch) |
| `--concurrency` | `200` in Tau2 wrapper | Max concurrent rollout executions |
| `--commit-concurrency` | `200` in Tau2 wrapper | Max concurrent `session.commit` submissions during training |
| `--trials` | `8` | Run each eval case N times and aggregate scores |
| `--train-trials` | `1` | Run each train case N times per epoch |
| `--train-index` | all | Run train sample(s) at 0-based split index/indices, e.g. `7` or `1,5,6` |
| `--eval-split` | `test` | Split used for baseline/per-epoch/final eval: `test`, `train`, or `none` |
| `--eval-index` | all | Run eval sample(s) at 0-based split index/indices within `--eval-split`, e.g. `14` or `1,5,6` |
| `--max-iterations` | `30` | Max steps per rollout |
| `--force-baseline-recompute` | off | Recompute cached pre-training baseline instead of reusing it |
| `--skip-baseline-eval` | off | Skip pre-training baseline eval/cache entirely |
| `--eval-each-epoch` | on in Tau2 wrapper | Run eval after every training epoch using `--eval-split` |
| `--skip-final-eval` | off; restart default on | Skip the extra final eval pass |
| `--reuse-train-rollout-cache` | off | Reuse cached epoch-0/no-memory train rollouts when present; write cache on miss |
| `--clean-result` / `--no-clean-result` | clean | Whether to prune previous result artifacts |
| `--keep-recent-results` | `5` | Number of recent default `run_` directories to keep when cleaning; cache and non-`run_` directories are preserved |
| `--output` | auto | JSON report output path |
| `--events-output` | auto | Streaming JSONL event output path |
| `--result-dir-name` | `train`; slots use `train_N` | Result subdirectory under `result/<dataset>/` |
| `--benchmark-service-url` | set by restart launcher | Benchmark runtime service URL |
| `--config` | set by restart launcher | ov.conf path |
| `--server-url` | set by restart launcher | OpenViking server URL |
| `--api-key` | from config | OpenViking API key |
| `--account-id` | `default` | OpenViking trusted account id |
| `--user-id` | `default` | OpenViking trusted user id |

## 5. Manual service mode

Use this only when you want to manage services yourself instead of using
`restart_vikingbot_train_eval.sh`.

Start the Tau2 service manually:

```bash
bash benchmark/tau2/train/run_service.sh --host 127.0.0.1 --port 1944
```

Service options:

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Service listen address |
| `--port` | `1944` | Service listen port |
| `--data-root` | auto-detect / `$TAU2_DATA_ROOT` | Path to `tau2-bench/data/tau2` |
| `--config` | `~/.openviking/ov.conf` | ov.conf for VikingBot / OpenViking access |
| `--rollout-language` | `default` | Rollout response language. Use `zh` for Chinese user-facing replies. |
| `--rollout-backend` | `vikingbot` | Rollout implementation backend. `native` for fast Python executor, `vikingbot` for full VikingBot AgentLoop. |
| `--native-thread-workers` | `128` | Thread pool size for native rollout executor. |
| `--rollout-thread-workers` | `200` | Worker threads used to host rollout executions off the uvicorn event loop. Use `0` to disable threaded hosting. |
| `--max-rollout-concurrency` | `200` | Maximum concurrent rollout executions accepted by the service. |
| `--no-kill-existing` | off | Don't kill existing process on the same port. |

Then run the lower-level Tau2 wrapper:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 4 \
  --trials 8
```

The wrapper expands to the generic runner with Tau2 defaults:

```bash
bash openviking/session/train/run_batch_train_eval.sh \
  --dataset tau2 \
  --domain airline \
  --eval-each-epoch \
  --concurrency 200 \
  --commit-concurrency 200 \
  --benchmark-service-url http://127.0.0.1:1944
```

The batch runner does **not** send a backend choice — it always uses whatever the
Tau2 service is configured with.

## 6. Result and rollout artifacts

By default each run writes artifacts under the repository-level result directory:

```text
result/tau2/<result-dir-name>/run_<domain>_<timestamp>/
  report.json
  rollouts_index.json
  rollouts/
```

`result/tau2/<result-dir-name>/latest_rollouts` points to the most recent
rollouts directory. Each rollout artifact group is one original task; each
rollout has its own subdirectory with `memory_context.md`, `messages.json`,
`tool_calls.json`, `evaluation.json`, and `commit_messages.json`. These files,
plus `rollouts_index.json`, are written as soon as each remote rollout finishes.
Train rollouts are enriched later with `commit_result.json` and
`memory_diff.json` as commit progress becomes available.

Streaming JSONL events are written to
`result/tau2/<result-dir-name>/run_<domain>_<timestamp>/events.jsonl`; train
commit events include `trace_id` for live `tail -f` debugging. Use
`--events-output` to override the path.
