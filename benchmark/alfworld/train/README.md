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

Rollout backends:

- `vikingbot` (default): ALFWorld is exposed to VikingBot as tools (`alfworld_step`,
  `done`) and can use the Tau2-style `experience_loader` skill with
  `search_experience` / `read_experience`.
- `direct`: service calls the configured VLM directly with
  observation/admissible-command prompts.

Example VikingBot backend:

```bash
ALFWORLD_ROLLOUT_BACKEND=vikingbot \
ALFWORLD_EXPERIENCE_LOADER_MODE=skill \
bash benchmark/alfworld/train/run_service.sh \
  --host 127.0.0.1 \
  --port 1954 \
  --rollout-backend vikingbot \
  --loader-mode skill
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

To run the one-click flow with the default VikingBot backend:

```bash
ALFWORLD_EXPERIENCE_LOADER_MODE=skill \
bash benchmark/alfworld/train/restart_alfworld_train_eval.sh \
  --epochs 1 \
  --train-index 0 \
  --eval-index 0 \
  --trials 1 \
  --train-trials 1 \
  --concurrency 2 \
  --max-iterations 50
```

The launcher restarts OpenViking, starts the ALFWorld rollout service, waits for
health checks, and then invokes `benchmark/alfworld/train/run_batch_train_eval.sh`.
Use `--slot N` before train/eval args for isolated ports/config/data/result dirs.
Use `--auto-commit` to commit pending changes before launch and append the
redacted command plus each completed stage summary to `git notes show HEAD`.
The note and code commit remain local; the launcher does not push either one.
Slot routing is hermetic: inherited environment variables such as
`OPENVIKING_PORT`, `OPENVIKING_CONFIG_FILE`, `OPENVIKING_DATA_DIR`,
`ALFWORLD_SERVICE_PORT`, `RESULT_DIR_NAME`, and `LOG_DIR` do not override the
slot-derived defaults.

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
