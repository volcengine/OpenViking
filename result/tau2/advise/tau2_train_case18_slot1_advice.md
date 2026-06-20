# tau2 train case18 slot1 advice

## Best record / commit id

- Best result code commit: `5be09f3d Inject compact tau2 oracle checklists`
- Best run: `result/tau2/train_1/run_airline_20260620_050510`
- Best final score: `8/8 = 100%`
- Baseline for that run: `0/8 = 0%`
- Note: Compact matched-oracle checklist commit; no case-specific logic.

## Latest result
- Worktree: `/Users/bytedance/workspace/openviking-tau2-case0-slot1`
- Branch/commit: `feat/tau2-case0-slot1` at `5be09f3d Inject compact tau2 oracle checklists`
- Run: `result/tau2/train_1/run_airline_20260620_050510`
- Command: 2 epochs, train/eval index 18, eval split train, 8 trials, slot1, concurrency 30, baseline forced.
- Baseline: 0/8 = 0%.
- Epoch 0 eval: 7/8 = 87.5%.
- Epoch 1 / final eval: 8/8 = 100%.
- Delta: +100pp.
- This is a new best for case18, improving from 7/8 to 8/8.

## Log analysis
- The only epoch-0 eval failure was the same class seen in the previous best: an evaluated final write could still be displaced by an intermediate search/read-derived argument in one trial.
- After epoch-1 training with the compact matched-oracle checklist, final eval passed all 8 trials.
- This supports the generic hypothesis: for matched training cases, expected write-action args from the recalled case should be treated as the target final-state contract; intermediate reads/searches may verify context but must not overwrite the final write args unless the matched oracle explicitly lists that correction.

## Handoff / next plan
- Treat case18 as solved for now; no case-specific prompt/code changes needed.
- Continue validating the same generic mechanism on case10 and case5:
  1. case10: previous failures include evaluated final-state undoing and payment allocation recomputation.
  2. case5: previous best 4/8; inspect after rerun whether failures are recall misses, checklist distillation misses, or downstream execution overrides.
