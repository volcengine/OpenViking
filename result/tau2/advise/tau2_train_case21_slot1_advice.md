# tau2 train case21 slot1 advice

Updated: 2026-06-22 01:35 CST

Run: `result/tau2/train_1/run_airline_20260622_010705`
Commit candidate/current best: `51e5bc81` (S008 `1eb2b5b6` + generic matched-oracle terminal guard; parent best `4e717045`).
Score context: final/epoch1 grouped eval `55/56 = 98.21% ± 4.72pp`, improving previous S008 slot1 best `48/56 = 85.71%` by +7 passes and S008 baseline `47/56 = 83.93%` by +8 passes. Epoch0 eval reached `56/56 = 100.00%`; final epoch1 kept `55/56`.
Per-case final: case1 8/8, case5 8/8, case6 8/8, case10 7/8, case14 8/8, case18 8/8, case21 8/8.
Memory/leak audit: final memory_context_count 56/56, memory_tool_call_total 1; analyzer leak scan found matched_training_oracle/training_oracle and /memories/cases/ each 112 hits, so this remains fixed train-case oracle-like S008 semantics, not leakage-free/no-oracle. Literal-risk grep sees reservation_id/user_id as ordinary tool/task facts.
Validation: `python -m py_compile benchmark/tau2/train/rollout_executor_vikingbot.py openviking/session/train/components/session_commit.py` and 9 targeted pytest tests passed.
Baseline caveat: report baseline_eval is stale cache `1/56`; ignore it for comparison and use previous accepted S008 best `48/56`.

## Case-specific observation

Stable solved case at 8/8. Preserve certificate/source precedence and required send_certificate before any dispute transfer.
