# LoCoMo OpenViking Evaluation

This directory contains the OpenViking single-search evaluation flow for
LoCoMo. It imports each conversation into an isolated OpenViking user space,
runs one `find` retrieval per question, optionally reranks the retrieved
memories, answers from the selected context, then judges and summarizes the
CSV.

The commands below assume OpenViking is already running on the default local
endpoint used by the CLI configuration. If your server is on a non-default port,
add `--openviking-url http://127.0.0.1:<port>` to the import and eval commands.

## Data

Use the prepared LoCoMo JSON:

```bash
export DATA=result/locomo.json
```

LoCoMo memories are imported under `viking://user/sample_{idx}/memories`.
The eval script uses the same `sample_{idx}` user id when searching, so import
and eval must use the same dataset order.

## Import Memories

For a fresh full import:

```bash
python benchmark/locomo/openviking/import_to_ov.py \
  --input "$DATA" \
  --parallel-samples 16 \
  --success-csv result/locomo_import_success.csv \
  --error-log result/locomo_import_errors.log
```

When intentionally re-importing the same data, add `--force-ingest`. If you
also want to ignore old import success records, add `--clear-ingest-record`.

## Smoke Test

Run one question before starting a full sweep:

```bash
python benchmark/locomo/openviking/run_eval.py "$DATA" \
  --output result/locomo_openviking_smoke.csv \
  --sample 0 \
  --question-index 0 \
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
OUT=result/locomo_openviking_limit50_rerank10.csv

python benchmark/locomo/openviking/run_eval.py "$DATA" \
  --output "$OUT" \
  --threads 8 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10
```

## Judge And Stat

```bash
python benchmark/locomo/openviking/judge.py \
  --input "$OUT" \
  --parallel 40

python benchmark/locomo/openviking/stat_judge_result.py \
  --input "$OUT"
```

The judge writes results back into the same CSV. Category 5 adversarial
questions are excluded by the LoCoMo judge/stat flow.
