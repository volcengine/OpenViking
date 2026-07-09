# Smoke Train/Eval Service

A tiny deterministic rollout service for testing the generic OpenViking train service.
It mirrors the tau2/ALFWorld remote dataset-service contract but has no external data,
LLM, or simulator dependency.

Start the smoke rollout service:

```bash
bash benchmark/smoke/train/run_service.sh --host 127.0.0.1 --port 1964
```


## One-click restart launcher

For the same workflow shape as Tau2/ALFWorld, use:

```bash
bash benchmark/smoke/train/restart_smoke_train_eval.sh \
  --epochs 1 \
  --eval-split test \
  --skip-final-eval
```

The launcher restarts OpenViking, starts the smoke rollout service, waits for
health checks, and then invokes `benchmark/smoke/train/run_batch_train_eval.sh`.
Use `--slot N` before train/eval args for isolated ports/config/data/result dirs.

Run the generic train/eval pipeline against it:

```bash
bash benchmark/smoke/train/run_batch_train_eval.sh \
  --epochs 1 \
  --eval-split test \
  --skip-baseline-eval \
  --skip-final-eval
```

Notes:

- `--dataset` is `smoke`; default wrapper domain is `tickets`.
- Splits are `train` and `test`; `dev`, `eval`, and `validation` alias to `test`.
- Cases include one success and multiple failures so `session.commit` receives both
  positive and negative training signals.
- For manual rollout checks, direct experience or policy content containing
  `smoke_pass_all` or `smoke_pass:<task_id>` forces matching scripted failures to pass.
