# LongMemEval OpenViking Benchmark

This directory contains the OpenViking evaluation flow for LongMemEval:

1. import each user's haystack sessions into OpenViking;
2. run one retrieval call per question;
3. optionally rerank the retrieved memories;
4. answer from the selected memory context;
5. judge and summarize the result CSV.

The benchmark expects an OpenViking server to already be running. The commands
below use the default local OpenViking client configuration. If you need a
different endpoint, pass `--openviking-url` to both import and eval.

## Data

Set the dataset path once:

```bash
DATA=/path/to/longmemeval_s_cleaned.json
```

The importer uses each sample's original `question_id`-derived user id, such as
`lm_user_<id>`, so the eval script searches the same user space created during
import.

## Import

```bash
python benchmark/longmemeval/openviking/import_to_ov.py \
  --input "$DATA" \
  --parallel 16 \
  --submit-parallel 16 \
  --wait-mode deferred \
  --success-csv result/longmemeval_openviking_import_success.csv \
  --error-log result/longmemeval_openviking_import_errors.log
```

Use `--force-ingest` only when intentionally re-importing existing sessions.

## Smoke Test

Run one question before a full evaluation:

```bash
python benchmark/longmemeval/openviking/run_eval.py "$DATA" \
  --output result/longmemeval_openviking_smoke.csv \
  --count 1 \
  --threads 1 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000 \
  --debug-print-model-input
```

With `--debug-print-model-input`, the CSV includes the full answer prompt and
retrieval trace. Check `retrieved_uris_by_iteration` to confirm rerank is enabled
and `context_uris` contains the expected number of memories.

## Full Eval

This example retrieves 50 memories, keeps the top 10 after rerank, and caps the
memory text passed to the answer model at 30000 characters.

```bash
OUT=result/longmemeval_openviking_search50_rerank10_chars30000.csv

python benchmark/longmemeval/openviking/run_eval.py "$DATA" \
  --output "$OUT" \
  --threads 8 \
  --timeout 900 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000
```

Set `--single-search-rerank-limit 0` to disable rerank. Set
`--single-search-max-context-chars 0` to disable the character budget.

## Judge And Stat

```bash
python benchmark/longmemeval/openviking/judge.py \
  --input "$OUT" \
  --parallel 40

python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input "$OUT"
```

The judge writes `result` values back into the same CSV. Use `--force` to
re-grade rows that already have a result. Use `--strict-prompt` when you want the
stricter LongMemEval judge prompt instead of the default lenient prompt.

`stat_judge_result.py` prints overall accuracy, average memory token/character
usage, and accuracy grouped by LongMemEval question type.
