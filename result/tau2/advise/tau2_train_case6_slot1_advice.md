# tau2 train case6 slot1 advice

## Best record / commit id

- Best result code commit: `22778c47 Recall trajectory diagnostics for tau2 rollouts`
- Best run: `result/tau2/train_1/run_airline_20260620_023637`
- Best final score: `8/8 = 100%`
- Baseline for that run: `1/8 = 12.5%`
- Note: This was the latest code commit before the best observed case6 run; later commits retain the generic mechanism.

## Best observed result
- Worktree: `/Users/bytedance/workspace/openviking-tau2-case0-slot1`
- Branch: `feat/tau2-case0-slot1`
- Run: `result/tau2/train_1/run_airline_20260620_023637`
- Baseline: `1/8 = 12.5%`
- Epoch 0 eval: `5/8 = 62.5%`
- Final / epoch 1 eval: `8/8 = 100%`
- Delta: `+87.5pp`

## Useful changes already in branch
- `22a92b4e Guard tau2 oracle training memories`
- `22778c47 Recall trajectory diagnostics for tau2 rollouts`

## Notes
- This case reached 100% without additional code changes beyond the generic oracle/trajectory-recall framework.
- Keep these changes generic; do not add case-specific prompt content.
