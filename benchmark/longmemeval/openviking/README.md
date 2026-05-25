# LongMemEval OpenViking Benchmark

This directory contains the OpenViking evaluation flow for LongMemEval.

The minimal reproduction flow is:

1. Import LongMemEval conversations into OpenViking.
2. Run single-search evaluation.
3. Judge model answers.
4. Print accuracy statistics.

Before running the commands, start OpenViking and make sure the local OV CLI
configuration points to that server.

## Import

```bash
mkdir -p result/longmemeval_import_logs

seq 0 499 | xargs -P 10 -I {} sh -c '
python benchmark/longmemeval/openviking/import_to_ov.py \
  --input /path/to/longmemeval_s_cleaned.json \
  --sample "$1" \
  --wait-mode deferred \
  --submit-parallel 16 \
  --force-ingest \
  --success-csv "result/longmemeval_import_logs/sample_${1}_success.csv" \
  --error-log "result/longmemeval_import_logs/sample_${1}_errors.log"
' sh {}
```

## Eval

The example below retrieves 50 memories and keeps the top 10 after reranking.

```bash
python benchmark/longmemeval/openviking/run_eval.py \
  /path/to/longmemeval_s_cleaned.json \
  --output result/longmemeval_openviking_search50_rerank10.csv \
  --threads 8 \
  --timeout 900 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10
```

## Judge

```bash
python benchmark/longmemeval/openviking/judge.py \
  --input result/longmemeval_openviking_search50_rerank10.csv \
  --parallel 40
```

## Stat

```bash
python benchmark/longmemeval/openviking/stat_judge_result.py \
  --input result/longmemeval_openviking_search50_rerank10.csv
```
