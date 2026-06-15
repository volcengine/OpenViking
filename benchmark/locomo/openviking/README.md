# LoCoMo OpenViking Benchmark

This directory contains the OpenViking evaluation flow for LoCoMo:

1. import each conversation into an isolated OpenViking user space;
2. run one retrieval call per question;
3. optionally rerank the retrieved memories;
4. answer from the selected memory context;
5. judge and summarize the result CSV.

The benchmark expects an OpenViking server to already be running. The commands
below use the default local OpenViking client configuration. If you need a
different endpoint, pass `--openviking-url` to both import and eval.

## Data

Use the prepared LoCoMo JSON file:

```bash
DATA=result/locomo.json
```

LoCoMo samples are imported under `viking://user/sample_{idx}/memories`.
Evaluation uses the same `sample_{idx}` user id, so import and eval must use the
same dataset order.

LoCoMo image evidence is imported as text from `blip_caption` by default. This
keeps benchmark runs compatible with older OpenViking servers and avoids making
results depend on whether the configured VLM can download external image URLs.
Use `--use-image-url` when you explicitly want to import structured
`image_url` parts and let OpenViking's VLM describe images during memory
extraction.

## Import

```bash
python benchmark/locomo/openviking/import_to_ov.py \
  --input "$DATA" \
  --parallel-samples 16 \
  --success-csv result/locomo_openviking_import_success.csv \
  --error-log result/locomo_openviking_import_errors.log
```

Use `--force-ingest` only when intentionally re-importing existing samples.
Use `--use-image-url` only for multimodal import experiments; the default uses
LoCoMo's `blip_caption` text.

## Smoke Test

Run one question before a full evaluation:

```bash
python benchmark/locomo/openviking/run_eval.py "$DATA" \
  --output result/locomo_openviking_smoke.csv \
  --sample 0 \
  --question-index 0 \
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
OUT=result/locomo_openviking_search50_rerank10_chars30000.csv

python benchmark/locomo/openviking/run_eval.py "$DATA" \
  --output "$OUT" \
  --threads 8 \
  --single-search-context-limit 50 \
  --single-search-rerank-limit 10 \
  --single-search-max-context-chars 30000
```

Set `--single-search-rerank-limit 0` to disable rerank. Set
`--single-search-max-context-chars 0` to disable the character budget.

## Judge And Stat

```bash
python benchmark/locomo/openviking/judge.py \
  --input "$OUT" \
  --parallel 40

python benchmark/locomo/openviking/stat_judge_result.py \
  --input "$OUT"
```

The judge writes `result` values back into the same CSV. Use `--force` to
re-grade rows that already have a result. Use `--strict-prompt` when you want the
stricter LoCoMo judge prompt instead of the default lenient prompt.

Category 5 adversarial questions are skipped by the LoCoMo judge/stat flow.
