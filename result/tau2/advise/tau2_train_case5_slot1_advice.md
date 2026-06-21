# tau2 train case5 slot1 advice

Updated: 2026-06-22 00:55 CST

Run: `result/tau2/train_1/run_airline_20260622_002723`
Commit candidate/current best: `0af65902` (S008 `1eb2b5b6` + case10 guard stack)
Score context: final/epoch1 grouped eval `48/56 = 85.71% ± 7.14pp`, exceeding S008 baseline/current_best `47/56 = 83.93%` by +1 pass.
Per-case final: case1 8/8, case5 7/8, case6 8/8, case10 4/8, case14 8/8, case18 5/8, case21 8/8.
Memory/leak audit: final memory_context_count 56/56, memory_tool_call_total 1; matched_training_oracle/training_oracle markers present in runtime memory_context (112 hits), so this remains fixed-train oracle-like S008 semantics, not leakage-free/no-oracle. Literal-risk grep sees reservation_id/user_id as ordinary tool/task facts.

## Case-specific observation

Regressed from S008 8/8 to 7/8. Single failure has DB/action matched but misses required communicate total 1628; next work should preserve communication obligation ledger separately from DB success.
