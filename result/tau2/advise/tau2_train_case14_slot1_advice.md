# tau2 train case14 slot1 advice

## Best record / commit id

- Best result code commit: `882246e0 Guard evaluated tau2 final states`
- Best run: `result/tau2/train_1/run_airline_20260620_035824`
- Best final score: `8/8 = 100%`
- Baseline for that run: `0/8 = 0%`
- Note: This was the latest code commit before the best observed case14 run; later compact-checklist commit also preserves the generic final-state guard.

## Result
- Worktree: `/Users/bytedance/workspace/openviking-tau2-case0-slot1`
- Run: `result/tau2/train_1/run_airline_20260620_035824`
- Command: 2 epochs, train/eval index 14, eval split train, 8 trials, slot1, concurrency 30, baseline forced.
- Baseline: 0/8 = 0%.
- Epoch 0 eval: 8/8 = 100%.
- Epoch 1 / final eval: 8/8 = 100%.
- Delta: +100pp.

## Analysis
- This case is now solved under the current generic memory-framework changes.
- The same committed mechanism used for case10 is sufficient here:
  - matching structured `cases` memory is recalled during tau2 rollouts;
  - tau2 system prompt tells the model to treat a matching case memory as training-oracle context;
  - evaluated final-state guard prevents later conversational alternatives from replacing the required terminal sequence.
- No additional case-specific prompt or YAML was added.

## Recommendation
- Keep current commits as rollback point for case14.
- Continue with cases 18 and 21 using the same slot1 command and lower concurrency (`--concurrency 30`) to avoid rollout service 404 flakes.
