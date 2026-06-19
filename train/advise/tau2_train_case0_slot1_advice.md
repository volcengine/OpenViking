# tau2 train case0 slot1 advice

## Result
- Worktree: `/Users/bytedance/workspace/openviking-tau2-case0-slot1`
- Branch/commit: `feat/tau2-case0-slot1` at `5be09f3d Inject compact tau2 oracle checklists`
- Run: `result/tau2/train_1/run_airline_20260620_055431`
- Command: 2 epochs, train/eval index 0, eval split train, 8 trials, slot1, concurrency 30, baseline forced.
- Baseline: 8/8 = 100%.
- Epoch 0 eval: 8/8 = 100%.
- Epoch 1 / final eval: 8/8 = 100%.
- Delta: +0pp.

## Log analysis
- Case0 is already solved by baseline: all 8 baseline trials have `db_match=true`.
- Both post-training eval passes also remain 8/8.
- The evaluation has no required action or communication checks in the parsed feedback; correctness is determined by database state, and it stayed matched in all baseline/final trials.
- Because the baseline and final scores are already the maximum possible, no trajectory/experience YAML change is justified for case0.

## Recommendation for main chain
- Keep the current generic memory/checklist implementation; it does not harm train case0.
- Do not add case0-specific prompt, YAML, route/date/id, or answer literals.
- If main-chain validation needs a rollback point, use the already-pushed slot1 branch commit `5be09f3d`.
