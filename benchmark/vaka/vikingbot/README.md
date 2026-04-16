# Vaka LoCoMo Benchmark

This benchmark evaluates Vaka multi-turn long-memory CSV results.

The default input is relative to this benchmark directory:

```bash
benchmark/vaka/vikingbot/data/vaka_locomo.csv
```

You can also pass a custom path with `--input`.

## Case Split

Rows are grouped by global `session_id` blocks:

- `session_id` 1-10 is `case_0001`
- `session_id` 11-20 is `case_0002`
- `session_id` 21-30 is `case_0003`

The default split is global across the whole CSV:

- `session_id` 1-70 are committed/imported as memory
- `session_id` 71 through the max session in the CSV are evaluation turns
- each row is one `query` + `deepsearch_answer` turn

## Usage

Import memory sessions 1-70 into OpenViking:

```bash
python3 benchmark/vaka/vikingbot/import_to_ov.py
```

`import_to_ov.py` imports each memory row as a two-message conversation:
`query` is the user message and `deepsearch_answer` is the assistant message.
All imported memory uses the same OpenViking identity by default:
`account=default`, `user_id=default`, and `agent_id=default`.
Evaluation sessions 71+ are not imported as memory by default.

Use a custom single user/agent when needed:

```bash
python3 benchmark/vaka/vikingbot/import_to_ov.py --user-id vaka --agent-id vaka
```

Prepare the judge input CSV:

```bash
python3 benchmark/vaka/vikingbot/run_eval.py
```

Judge the prepared answers:

```bash
uv run python benchmark/vaka/vikingbot/judge.py --parallel 10
```

Calculate stats:

```bash
python3 benchmark/vaka/vikingbot/stat_judge_result.py
```

Or run all steps:

```bash
bash benchmark/vaka/vikingbot/run_full_eval.sh
```

Skip OpenViking import and only do offline CSV preparation/judge/stat:

```bash
bash benchmark/vaka/vikingbot/run_full_eval.sh --skip-import
```

If the judge dependencies are only available through the project environment:

```bash
bash benchmark/vaka/vikingbot/run_full_eval.sh --python "uv run python"
```

## Notes

`run_eval.py` does not call Vaka again. It treats the CSV `deepsearch_answer` column as
the generated answer to evaluate.

If `standard_answer` is present, `judge.py` grades against it. If `judge_standard` is
present, `judge.py` treats it as a rubric. If both are empty, the judge evaluates whether
the answer follows the current query while preserving relevant memory from global
`session_id` 1-70 and prior evaluation turns.
