# LoCoMo OpenViking Benchmark

This directory contains the OpenViking evaluation flow for LoCoMo.

The minimal reproduction flow is:

1. Import LoCoMo conversations into OpenViking.
2. Run single-search evaluation.
3. Judge model answers.
4. Print accuracy statistics.

Before running the commands, start OpenViking and make sure the local OV CLI
configuration points to that server.

## Import

```bash
python benchmark/locomo/openviking/import_to_ov.py \
  --input result/locomo.json \
  --parallel-samples 16 \
  --force-ingest \
  --clear-ingest-record
```

## Eval

The example below retrieves 50 memories and keeps the top 10 after reranking.

```bash
python benchmark/locomo/openviking/run_eval.py \
  result/locomo.json \
  --output result/locomo_openviking_limit50_rerank10.csv \
  --threads 8 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10
```

## Judge

```bash
python benchmark/locomo/openviking/judge.py \
  --input result/locomo_openviking_limit50_rerank10.csv \
  --parallel 40
```

## Stat

```bash
python benchmark/locomo/openviking/stat_judge_result.py \
  --input result/locomo_openviking_limit50_rerank10.csv
```
