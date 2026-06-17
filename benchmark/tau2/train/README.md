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

## 1. Start the Tau2 service

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
| `--rollout-backend` | `native` | Rollout implementation backend. `native` for fast Python executor, `vikingbot` for full VikingBot AgentLoop. |
| `--native-thread-workers` | `128` | Thread pool size for native rollout executor. |
| `--no-kill-existing` | off | Don't kill existing process on the same port. |

### Using vikingbot backend

To run rollouts through the full VikingBot agent loop instead of the native fast executor:

```bash
bash benchmark/tau2/train/run_service.sh \
  --host 127.0.0.1 \
  --port 1944 \
  --rollout-backend vikingbot
```

The batch runner does **not** send a backend choice — it always uses whatever
the service is configured with.

## 2. Pre-run test score only

Use `--epochs 0` to run final test evaluation without training:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 0 \
  --eval-limit 25 \
  --trials 8
```

## 3. Train with a cached pre-training test score

The runner evaluates the test split before training automatically. For the same
dataset/domain, `--eval-limit`, `--trials`, and rollout options, this baseline is
cached under `result/tau2/train/cache/baseline/` and reused by later runs. Use
`--force-baseline-recompute` to refresh it. The Tau2 wrapper also runs a test
rollout after each training epoch so you can track held-out score progression.

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 4 \
  --train-limit 25 \
  --eval-limit 25 \
  --trials 8
```

## 4. Defaults and options

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
- `--clean-result` is enabled by default and clears previous `result/tau2/train/` run artifacts before each run, while preserving `result/tau2/train/cache/`. Use `--no-clean-result` to keep previous runs.
- Streaming JSONL events are written to `result/tau2/train/<domain>_<timestamp>/events.jsonl`; train commit events include `trace_id` for live `tail -f` debugging. Use `--events-output` to override the path.

### Common options

| Option | Default | Description |
|--------|---------|-------------|
| `--domain` | `airline` | Benchmark domain to run |
| `--epochs` | `1` | Number of training epochs. Use `0` for eval-only. |
| `--batch-size` | whole split | Train/eval batch size (cases per batch) |
| `--concurrency` | `150` | Max concurrent rollout executions |
| `--commit-concurrency` | `100` | Max concurrent `session.commit` submissions during training |
| `--trials` | `8` | Run each eval case N times and aggregate scores |
| `--train-limit` | unlimited | Cap train split size (for smoke tests) |
| `--eval-limit` | unlimited | Cap eval split size (for smoke tests) |
| `--max-iterations` | `30` | Max steps per rollout |
| `--force-baseline-recompute` | off | Recompute cached pre-training test baseline instead of reusing it |
| `--eval-each-epoch` | on in Tau2 wrapper | Run held-out eval after every training epoch |
| `--clean-result` / `--no-clean-result` | clean | Whether to wipe previous result artifacts |
| `--output` | auto | JSON report output path |
| `--events-output` | auto | Streaming JSONL event output path |
| `--benchmark-service-url` | `http://127.0.0.1:1944` | Benchmark runtime service URL |
| `--config` | `~/.openviking/ov.conf` | ov.conf path |
| `--server-url` | from config | OpenViking server URL |
| `--api-key` | from config | OpenViking API key |
| `--account-id` | `default` | OpenViking trusted account id |
| `--user-id` | `default` | OpenViking trusted user id |

### Examples

Quick smoke test (1 train, 1 eval, 1 trial):

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 1 \
  --trials 1 \
  --train-limit 1 \
  --eval-limit 1
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

## 5. Result and rollout artifacts

By default each run writes artifacts under the repository-level result directory:

```text
result/tau2/train/<domain>_<timestamp>/
  report.json
  rollouts_index.json
  rollouts/
```

`result/tau2/train/latest_rollouts` points to the most recent rollouts directory.
Each rollout artifact group is one original task; each rollout has its own subdirectory
with `memory_context.md`, `messages.json`, `tool_calls.json`, `evaluation.json`,
and `commit_messages.json`. These files, plus `rollouts_index.json`, are written
as soon as each remote rollout finishes. Train rollouts are enriched later with
`commit_result.json` and `memory_diff.json` as commit progress becomes available.
