# Tau2 Train Pipeline

Tau2 training uses the generic OpenViking session/train batch pipeline.  The
Tau2-specific code in this directory only starts the Tau2 dataset service and
provides thin defaults for the generic runner.

## 0. Prerequisites: Start OpenViking server

The vikingbot rollout backend needs a running OpenViking server for memory
recall (experience search, user profile read, etc.).

```bash
# Quick restart (kills existing, cleans data, starts fresh with bot API)
bash bot/scripts/restart_openviking_server.sh
```

Default server URL is `http://127.0.0.1:1933`, configured in `~/.openviking/ov.conf`.

## 1. One-click vikingbot train/eval

For the common full VikingBot path, use the one-click launcher. It restarts
OpenViking, waits for the bot proxy health endpoint, starts the Tau2 rollout
service with `--rollout-backend vikingbot`, waits for service `/health`, and
then starts batch train/eval.

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh
```

Default train/eval arguments are:

```bash
--commit-concurrency 100 --epochs 2 --trials 8 --skip-final-eval
```

Any arguments passed to `restart_vikingbot_train_eval.sh` replace those
default train/eval arguments. For example, to keep 10 previous run directories:

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --epochs 2 \
  --trials 8 \
  --skip-final-eval \
  --keep-recent-results 10
```


### Running multiple slots concurrently

The one-click launcher accepts a launcher-only `--slot N` before the normal
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
  --skip-baseline-eval \
  --skip-final-eval
```

Environment variables such as `OPENVIKING_PORT`, `OPENVIKING_BOT_PORT`,
`TAU2_SERVICE_PORT`, `OPENVIKING_CONFIG_FILE`, `OPENVIKING_DATA_DIR`,
`RESULT_DIR_NAME`, and `LOG_DIR` can still override the slot-derived defaults.
For non-zero slots, the launcher copies base `~/.openviking/*.conf*` config
files when needed and rewrites the slot config's `storage.workspace`,
`server.port`, `server.bot_api_url`, and `bot.ov_server.server_url`.

The launcher writes service logs and pid files under:

```text
result/tau2/<result-dir-name>/service_logs/
```

OpenViking readiness is checked at `http://127.0.0.1:<ov-port>/bot/v1/health`;
the Tau2 service readiness check is `http://127.0.0.1:<tau2-port>/health`.

## 2. Start the Tau2 service manually

```bash
bash benchmark/tau2/train/run_service.sh --host 127.0.0.1 --port 1944
```

### Service options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Service listen address |
| `--port` | `1944` | Service listen port |
| `--data-root` | auto-detect / `$TAU2_DATA_ROOT` | Path to `tau2-bench/data/tau2` |
| `--config` | `~/.openviking/ov.conf` | ov.conf for VikingBot / OpenViking access |
| `--rollout-language` | `default` | Rollout response language. Use `zh` for Chinese user-facing replies. |
| `--rollout-backend` | `vikingbot` | Rollout implementation backend. `native` for fast Python executor, `vikingbot` for full VikingBot AgentLoop. |
| `--native-thread-workers` | `128` | Thread pool size for native rollout executor. |
| `--no-kill-existing` | off | Don't kill existing process on the same port. |

### Using native backend

`vikingbot` is the default. To use the fast native executor instead:

```bash
bash benchmark/tau2/train/run_service.sh \
  --host 127.0.0.1 \
  --port 1944 \
  --rollout-backend native
```

The batch runner does **not** send a backend choice — it always uses whatever
the service is configured with.

## 3. Pre-run test score only

Use `--epochs 0` to run final test evaluation without training:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 0 \
  --eval-index 24 \
  --trials 8
```

## 4. Train with a cached pre-training test score

The runner evaluates the test split before training automatically. For the same
dataset/domain, `--eval-index` value(s), `--trials`, and rollout options, this baseline is
cached under `result/tau2/train/cache/baseline/` and reused by later runs. Normal
runs do not recompute the baseline when this cache hits; pass
`--force-baseline-recompute` only when you intentionally want to refresh it. Use
`--skip-baseline-eval` to skip this pre-training baseline entirely. The Tau2
wrapper also runs an eval rollout after each training epoch so you can track
score progression. By default that eval uses the held-out `test` split; pass
`--eval-split train` to evaluate on train tasks instead, or `--eval-split none`
to disable eval.

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 4 \
  --trials 8
```

Train one task and evaluate the same train task for 8 trials after each epoch
(no test split, no pre-training baseline, no extra final eval):

```bash
bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
  --epochs 2 \
  --train-index 14 \
  --eval-split train \
  --eval-index 14 \
  --trials 8 \
  --skip-baseline-eval \
  --skip-final-eval
```

## 5. Defaults and options

`benchmark/tau2/train/run_batch_train_eval.sh` is a Tau2 convenience wrapper for:

```bash
bash openviking/session/train/run_batch_train_eval.sh \
  --dataset tau2 \
  --domain airline \
  --benchmark-service-url http://127.0.0.1:1944
```

Default concurrency and output behavior:

- rollout concurrency: `150`
- session.commit concurrency: `100`
- eval trials: `8`
- `--clean-result` is enabled by default and keeps the most recent 5 `result/tau2/<result-dir-name>/run_<domain>_<timestamp>/` run directories while preserving `result/tau2/<result-dir-name>/cache/` and all non-`run_` directories such as `result/tau2/<result-dir-name>/opt/`. Use `--keep-recent-results N` to change the retention count, or `--no-clean-result` to keep all previous runs.
- `--skip-final-eval` skips the extra final held-out eval pass. The one-click launcher enables this by default because the Tau2 wrapper already enables `--eval-each-epoch`.
- Streaming JSONL events are written to `result/tau2/<result-dir-name>/run_<domain>_<timestamp>/events.jsonl`; train commit events include `trace_id` for live `tail -f` debugging. Use `--events-output` to override the path.

### Common options

| Option | Default | Description |
|--------|---------|-------------|
| `--domain` | `airline` | Benchmark domain to run |
| `--epochs` | `1` | Number of training epochs. Use `0` for eval-only. |
| `--batch-size` | whole split | Train/eval batch size (cases per batch) |
| `--concurrency` | `150` | Max concurrent rollout executions |
| `--commit-concurrency` | `100` | Max concurrent `session.commit` submissions during training |
| `--trials` | `8` | Run each eval case N times and aggregate scores |
| `--train-index` | all | Run train sample(s) at 0-based split index/indices, e.g. `7` or `1,5,6` |
| `--eval-split` | `test` | Split used for baseline/per-epoch/final eval: `test`, `train`, or `none` |
| `--eval-index` | all | Run eval sample(s) at 0-based split index/indices within `--eval-split`, e.g. `14` or `1,5,6` |
| `--max-iterations` | `30` | Max steps per rollout |
| `--force-baseline-recompute` | off | Recompute cached pre-training baseline instead of reusing it |
| `--skip-baseline-eval` | off | Skip pre-training baseline eval/cache entirely |
| `--eval-each-epoch` | on in Tau2 wrapper | Run eval after every training epoch using `--eval-split` |
| `--skip-final-eval` | off; on in one-click launcher | Skip the extra final eval pass |
| `--clean-result` / `--no-clean-result` | clean | Whether to prune previous result artifacts |
| `--keep-recent-results` | `5` | Number of recent default `run_` directories to keep when cleaning; cache and non-`run_` directories are preserved |
| `--output` | auto | JSON report output path |
| `--events-output` | auto | Streaming JSONL event output path |
| `--result-dir-name` | `train` | Result subdirectory under `result/<dataset>/`; one-click slots set this to `train_N` |
| `--benchmark-service-url` | `http://127.0.0.1:1944` | Benchmark runtime service URL |
| `--config` | `~/.openviking/ov.conf` | ov.conf path |
| `--server-url` | from config | OpenViking server URL |
| `--api-key` | from config | OpenViking API key |
| `--account-id` | `default` | OpenViking trusted account id |
| `--user-id` | `default` | OpenViking trusted user id |

### Examples

Quick smoke test (1 train, 1 test eval, 1 trial):

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 1 \
  --trials 1 \
  --train-index 0 \
  --eval-index 0
```

Full training run:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --domain airline \
  --epochs 4 \
  --concurrency 150 \
  --commit-concurrency 100 \
  --trials 8
```

## 6. Result and rollout artifacts

By default each run writes artifacts under the repository-level result directory:

```text
result/tau2/<result-dir-name>/run_<domain>_<timestamp>/
  report.json
  rollouts_index.json
  rollouts/
```

`result/tau2/<result-dir-name>/latest_rollouts` points to the most recent rollouts directory.
Each rollout artifact group is one original task; each rollout has its own subdirectory
with `memory_context.md`, `messages.json`, `tool_calls.json`, `evaluation.json`,
and `commit_messages.json`. These files, plus `rollouts_index.json`, are written
as soon as each remote rollout finishes. Train rollouts are enriched later with
`commit_result.json` and `memory_diff.json` as commit progress becomes available.
