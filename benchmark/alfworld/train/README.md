# ALFWorld Train/Eval Service

This directory adds an ALFWorld remote rollout service using the same generic
OpenViking dataset-service contract as `benchmark/tau2/train/service_app.py`.
It uses the official `alfworld` package directly; SkillOpt was only used as a
reference for the rollout shape.

Start the service:

```bash
export ALFWORLD_DATA=/path/to/alfworld/data
export ALFWORLD_REPO=~/workspace/alfworld  # optional; run_service adds this by default
bash benchmark/alfworld/train/run_service.sh --host 127.0.0.1 --port 1954
```


## One-click restart launcher

For the same workflow shape as Tau2's `restart_vikingbot_train_eval.sh`, use:

```bash
export ALFWORLD_DATA=/path/to/alfworld/data
export ALFWORLD_REPO=~/workspace/alfworld  # optional; run_service adds this by default
bash benchmark/alfworld/train/restart_alfworld_train_eval.sh \
  --epochs 1 \
  --eval-split test \
  --max-iterations 50
```

The launcher restarts OpenViking, starts the ALFWorld rollout service, waits for
health checks, and then invokes `benchmark/alfworld/train/run_batch_train_eval.sh`.
Use `--slot N` before train/eval args for isolated ports/config/data/result dirs.

Then run the generic batch pipeline through the wrapper:

```bash
bash benchmark/alfworld/train/run_batch_train_eval.sh \
  --epochs 1 \
  --eval-split test \
  --max-iterations 50 \
  --concurrency 8
```

Notes:

- `--dataset` is `alfworld`.
- `--domain` may be `all` or one ALFWorld task type such as `pick_and_place`.
- Generic split `test` maps to `eval_out_of_distribution`; `train` maps
  to train; `eval_in_distribution` is also accepted by the service API.
- The service discovers `game.tw-pddl` / `*.tw-pddl` under `$ALFWORLD_DATA`. If
  no files are discoverable it exposes pseudo env-slot cases so the ALFWorld env
  can sample episodes dynamically.
