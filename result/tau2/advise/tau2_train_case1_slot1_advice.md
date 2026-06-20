# Tau2 train case1 slot1 advice

## Best record / commit id

- Best result code commit: `e3d14047 Guard failed write experience branches`
- Advice artifact commit: `9207e3e0 Advise tau2 train case1 100pct result`
- Best run: `result/tau2/train_1/run_airline_20260619_230836`
- Best final score: `8/8 = 100%`
- Baseline for that run: `2/8 = 25%`
- Note: This is the rollback commit for the generic failed-write guard that produced the best observed case1 result.

## Scope

- Dataset/domain: `tau2` / `airline`
- Split/index: `train` case `1`
- Launcher: `benchmark/tau2/train/restart_vikingbot_train_eval.sh`
- Required slot: `--slot 1`
- Exit metric: train for 2 epochs, then evaluate the same train case with 8 trials.

## Runtime requirement

Do not use `OPENVIKING_PROMPT_TEMPLATES_DIR` for this case. Run from the worktree and use the worktree virtualenv so bundled prompt templates resolve to this worktree:

```bash
cd /Users/bytedance/workspace/openviking-tau2-case0-slot1
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
unset OPENVIKING_PROMPT_TEMPLATES_DIR
```

Expected checks:

- `which python` -> `$PWD/.venv/bin/python`
- `which openviking-server` -> `$PWD/.venv/bin/openviking-server`
- `openviking.__file__` under this worktree
- `PromptManager._get_bundled_templates_dir()` -> `$PWD/openviking/prompts/templates`

## Best tested result

- Best commit in this worktree: `e3d14047 Guard failed write experience branches`
- Best run: `result/tau2/train_1/run_airline_20260619_230836`
- Command shape:
  ```bash
  bash benchmark/tau2/train/restart_vikingbot_train_eval.sh \
    --slot 1 \
    --epochs 2 \
    --train-index 1 \
    --eval-split train \
    --eval-index 1 \
    --trials 8 \
    --skip-final-eval \
    --keep-recent-results 20 \
    --force-baseline-recompute
  ```
- Baseline: `2/8 = 25.00%`
- Epoch 0 eval: `3/8 = 37.50%`
- Final after epoch 1: `8/8 = 100.00%`
- Delta: `+75.00pp`
- Previous best: `f4b911bd`, `4/8 = 50.00%`; keep `e3d14047` as the new rollback point.

## Current generic YAML strategy

Use the committed changes in:

- `openviking/prompts/templates/memory/trajectories.yaml`
- `openviking/prompts/templates/memory/experiences.yaml`

The changes are intentionally generic, not case-specific:

- When read-only action checks and DB mismatch expose a forbidden state-changing write, do not turn the write into a positive post-write workflow.
- Do not generate experiences whose applicability depends on future agents seeing hidden evaluation metadata such as `action_checks`, rubric, or CaseSpec.
- Map evaluation-only failures to agent-visible gates when possible: object status, ownership, exact timestamp arithmetic, cabin/membership/insurance/refund prerequisites, confirmation, and target binding.
- If an observable gate forbids a write, produce a narrow refusal/done or transfer boundary; the Approach must not call the forbidden state-changing tool.
- For failure/partial/unfinished trajectories, default to no-write gate/refusal/communication/done/transfer guardrails. Do not include a state-changing tool in Approach unless visible evaluation explicitly says that state-changing tool was missing and the actual trace did not already execute it unsuccessfully.
- Terminal-action completeness for writes applies to successful trajectories or failures where evaluation explicitly says the write was missing; it must not override failure write-branch restrictions.

## Observed memory behavior in the best run

Run `run_airline_20260619_230836` produced the desired rollout behavior:

- Baseline still often called `cancel_reservation` and failed DB match.
- Epoch 0 created a no-write cancellation eligibility experience, improving eval to `3/8`.
- Epoch 1 retained/refined the no-write gate/refusal pattern; all 8 eval trials avoided `cancel_reservation` and ended via `transfer_to_human_agents`/`done`, reaching `8/8`.
- The retrieved experience in epoch 1 allowed query/communication/done and explicitly forbade the state-changing cancellation tool unless eligibility gates are satisfied.

## Caution for mainline

The YAML itself is generic. One generated experience in the best run still contained instance-specific timestamp/attribute examples in `Reflect`; this came from the memory extractor output, not from hard-coded YAML. If mainline wants stricter artifact cleanliness, add a separate generic rule that examples in generated experience memories must be abstracted unless they are stable policy constants.
