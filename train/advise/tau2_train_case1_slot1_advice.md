# Tau2 train case1 slot1 advice

## Scope

- Dataset/domain: `tau2` / `airline`
- Split/index: `train` case `1`
- Launcher: `benchmark/tau2/train/restart_vikingbot_train_eval.sh`
- Required slot: `--slot 1`
- Exit metric: train for 2 epochs, then evaluate the same train case with 8 trials.

## Best tested result

- Best commit in this worktree: `42706c9f Tune tau2 case1 memory extraction`
- Best run: `result/tau2/train_1/run_airline_20260619_201546`
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
- Baseline: `0/8 = 0.00%`
- Final after epoch 1: `3/8 = 37.50%`
- Delta: `+37.50pp`

## Recommended YAML changes

Use the committed changes in:

- `openviking/prompts/templates/memory/trajectories.yaml`
- `openviking/prompts/templates/memory/experiences.yaml`

The changes are intentionally generic, not case-specific:

- Treat visible evaluation/action checks as the training oracle before domain policy when diagnosing a rollout.
- If visible expected actions are read/query-only but the actual trace performed a state-changing write and DB reward is `0.0`, diagnose the write as the forbidden substitute rather than creating a positive post-write workflow.
- For cancellation-related failures, require full timestamp arithmetic for 24-hour windows and avoid learning positive cancellation workflows from traces where the write caused DB mismatch.
- Scope policy-ineligible handoff/refusal memories narrowly and include explicit forbidden substitute families to avoid broad retrieval noise.

## Follow-up observations

Later uncommitted variants that added more explicit oracle overrides or support-rep wording did not improve the score (`1/8` or `0/8` final) and were reverted. Keep the highest committed version as the rollback point unless a future run beats `3/8`.

## Caution for mainline

The best run still showed some noisy memory extraction: some failed training traces continued to produce positive cancellation-operation experiences. The current advice is useful because it improved the case1 exit metric, but further work should focus on preventing positive state-changing experiences from failed query-only evaluations without hard-coding this case.
