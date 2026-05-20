# Vaka Benchmark

Evaluates long-memory recall using two datasets:

- **`data/vaka_locomo.csv`** — 405 rows of real work conversations, session 1-100. Sessions 1-70 are imported as memory; sessions 71-100 are not used directly.
- **`data/vaka_judge.csv`** — 66 evaluation questions about user preferences/behaviour patterns, each with a `standard_answer` gold label.

## Case Split

Rows are grouped by global `session_id` into cases of 10 sessions each:

- `session_id` 1-10 → `case_0001`
- `session_id` 11-20 → `case_0002`
- `session_id` 21-30 → `case_0003`
- …

## Configuration
We use the following configuration for evaluating Vaka dataset, please notice that when run
the run_eval.py script, we use gpt-5.4 for generating the answer.


```bash
{
  "storage": {
    "vectordb": {
      "backend": "local",
      "dimension": 2048,
      "sparse_weight": 0.5
    }
  },
  "embedding": {
    "hybrid": {
      "model": "doubao-embedding-vision-251215",
      "api_key": "-",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "provider": "volcengine",
      "dimension": 2048,
      "input": "multimodal"
    },
    "max_concurrent": 10
  },
  "vlm": {
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "-",
    "provider": "volcengine",
    "model": "doubao-seed-2-0-pro-260215",
    "max_concurrent": 20
  },
  "memory": {
    "custom_templates_dir": "benchmark/vaka/vikingbot/custom_memory_templates"
  },
  "rerank": {
    "provider": "vikingdb",
    "ak": "-",
    "sk": "-",
    "host": "api-vikingdb.vikingdb.cn-beijing.volces.com",
    "model_name": "doubao-seed-rerank",
    "model_version": "251028",
    "threshold": 0.1
  }
}
```


## Pipeline

All commands are run from the project root (`OpenViking/`). Results are written to `benchmark/vaka/vikingbot/result/`.

### Step 1 — Import memory (session 1-70)

Default identity is `account=default`, `user_id=default`, `agent_id=default`. The `--memory-sessions` default is `1-70`.

```bash
# Session granularity (default): one OpenViking session per global_session_id
caffeinate -i uv run python benchmark/vaka/vikingbot/import_to_ov.py \
    --input benchmark/vaka/vikingbot/data/vaka_locomo.csv

# Case granularity (for comparison): all sessions in a case merged into one OpenViking session
caffeinate -i uv run python benchmark/vaka/vikingbot/import_to_ov.py \
    --input benchmark/vaka/vikingbot/data/vaka_locomo.csv \
    --ingest-mode case
```

The two modes use different success keys and do not conflict — you can run both for side-by-side comparison without clearing any records.

Import is resumable: each session is checkpointed immediately to `result/import_success.csv` and `result/.ingest_record.json`. Re-running skips already-imported sessions automatically. To force a full re-import, add `--force-ingest`.

Use a custom identity when needed:

```bash
caffeinate -i uv run python benchmark/vaka/vikingbot/import_to_ov.py \
    --input benchmark/vaka/vikingbot/data/vaka_locomo.csv \
    --user-id vaka --agent-id vaka
```

After import, verify completeness:

```bash
python3 -c "
import csv
with open('benchmark/vaka/vikingbot/result/import_success.csv') as f:
    rows = list(csv.DictReader(f))
session_rows = [r for r in rows if r['global_session_id'].isdigit()]
ids = sorted(int(r['global_session_id']) for r in session_rows)
missing = [i for i in range(1, 71) if i not in ids]
print(f'Imported: {len(ids)}, missing: {missing or \"none\"}')
"
```

### Step 2 — Generate answers

Calls OpenViking `/bot/v1/chat` for each question in `vaka_judge.csv`. The `--user-id` and `--account` defaults are `default`. Resume by re-running the same command — already-answered questions are skipped automatically.

```bash
caffeinate -i uv run python benchmark/vaka/vikingbot/run_eval.py \
    benchmark/vaka/vikingbot/data/vaka_judge.csv \
    --output benchmark/vaka/vikingbot/result/vaka_qa_result.csv \
    --parallel 3
```

### step 3 - Clean up lines that failed (optional, execute before Judge).

If there are rows in `vaka_qa_result.csv` where `response_input_tokens` is 0, 
it means that although these issues were written to the results file, 
the actual bot call failed or there were no valid token statistics. 
These failed rows should be cleaned up and rerun the run_eval.py script before using the 
Judge function.

```bash
uv run python benchmark/vaka/vikingbot/clean_failed_eval_rows.py --input benchmark/vaka/vikingbot/result/vaka_qa_result.csv
```


### Step 4 — Judge answers

Requires `ARK_API_KEY` (Ark platform, three evaluation models). Set it via `~/.openviking_benchmark_env`, the `ARK_API_KEY` env var, or `--token`. Re-running skips already-judged rows; add `--force` to re-judge everything.
You can also use openai-supported models for evaluating (e.g., gpt-5.4).

```bash
uv run python benchmark/vaka/vikingbot/judge.py \
    --input benchmark/vaka/vikingbot/result/vaka_qa_result.csv \
    --parallel 10
```

### Step 5 — Statistics

```bash
uv run python benchmark/vaka/vikingbot/stat_judge_result.py \
    --input benchmark/vaka/vikingbot/result/vaka_qa_result.csv
```