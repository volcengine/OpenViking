# LongMemEval OpenViking Evaluation

This directory contains the OpenViking single-search evaluation flow for
LongMemEval. It imports each user's history into OpenViking, retrieves memory
with one `find` call, optionally reranks the retrieved memories, answers from
the selected context, then judges and summarizes the CSV.

The commands below assume OpenViking is already running on the default local
endpoint used by the CLI configuration. If your server is on a non-default port,
add `--openviking-url http://127.0.0.1:<port>` to the import and eval commands.

## Data

Set the dataset path once:

```bash
export DATA=/path/to/longmemeval_s_cleaned.json
```

## Import Memories

For a fresh full import:

```bash
mkdir -p result/longmemeval_import

seq 0 499 | xargs -P 10 -I {} sh -c '
  python benchmark/longmemeval/openviking/import_to_ov.py \
    --input "$DATA" \
    --sample "$1" \
    --wait-mode deferred \
    --submit-parallel 16 \
    --success-csv "result/longmemeval_import/sample_${1}_success.csv" \
    --error-log "result/longmemeval_import/sample_${1}_errors.log"
' sh {}
```

When intentionally re-importing the same data, add `--force-ingest`. If you
also want to ignore old import success records, add `--clear-ingest-record`.

## Smoke Test

Run one question before starting a full sweep:

```bash
python benchmark/longmemeval/openviking/run_eval.py "$DATA" \
  --output result/longmemeval_openviking_smoke.csv \
  --count 1 \
  --threads 1 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10 \
  --debug-print-model-input
```

The debug CSV includes `model_input_prompt`, memory token counts, and
`retrieved_uris_by_iteration`. Check that `rerank_enabled` is true,
`rerank_error` is empty, and `context_uris` has the expected rerank limit.

## Full Eval

This example retrieves 50 memories, keeps the top 10 after rerank, and runs
evaluation with 8 worker threads:

```bash
OUT=result/longmemeval_openviking_search50_rerank10.csv

python benchmark/longmemeval/openviking/run_eval.py "$DATA" \
  --output "$OUT" \
  --threads 8 \
  --timeout 900 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10
```

## Judge And Stat

```bash
python benchmark/longmemeval/openviking/judge.py \
  --input "$OUT" \
  --parallel 40

python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input "$OUT"
```

The judge writes results back into the same CSV. `stat_judge_result.py` prints
overall accuracy and per-question-type accuracy.
