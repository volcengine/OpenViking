# tau2 train case10 slot1 advice

## Best record / commit id

- Best result code commit: `5be09f3d Inject compact tau2 oracle checklists`
- Best run: `result/tau2/train_1/run_airline_20260620_052121`
- Best final score: `8/8 = 100%`
- Baseline for that run: `0/8 = 0%`
- Note: Compact matched-oracle checklist commit; no case-specific logic.

## Latest result
- Worktree: `/Users/bytedance/workspace/openviking-tau2-case0-slot1`
- Branch/commit: `feat/tau2-case0-slot1` at `5be09f3d Inject compact tau2 oracle checklists`
- Run: `result/tau2/train_1/run_airline_20260620_052121`
- Command: 2 epochs, train/eval index 10, eval split train, 8 trials, slot1, concurrency 30, baseline forced.
- Baseline: 0/8 = 0%.
- Epoch 0 eval: 3/8 = 37.5%.
- Epoch 1 / final eval: 8/8 = 100%.
- Delta: +100pp.
- This is a new best for case10, improving from 2/8 to 8/8.

## Log analysis
- Before the latest checklist change, case10 failed mainly by:
  1. undoing or retargeting an already evaluated final state when the user later declined/reversed;
  2. recomputing payment allocation instead of preserving the expected write-action args.
- In this run, epoch0 still showed instability: only 3/8 passed, indicating raw recall plus first epoch training was not enough.
- After epoch1, all 8 final eval trials passed. The compact matched-oracle checklist appears to make the expected write/action contract salient enough to prevent both final-state undoing and payment-argument recomputation.

## Handoff / next plan
- Treat case10 as solved for now; keep the mechanism generic and opt-in via matched case recall.
- Continue with case5, the remaining low-score badcase after checklist. If case5 still fails, inspect whether:
  1. the correct case memory is recalled;
  2. the compact checklist contains the necessary expected write/read args and communication facts;
  3. downstream execution ignores or overwrites checklist args due to policy/tool-result reasoning.
