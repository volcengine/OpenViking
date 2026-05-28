# Hermes LoCoMo Benchmark

This directory runs LoCoMo QA through Hermes Agent with three memory paths:

- `native`: imports transcripts into Hermes native memory, then evaluates through Hermes.
- `e2e`: imports transcripts through Hermes with the OpenViking memory plugin enabled, commits the resulting OpenViking sessions, then evaluates through Hermes.
- `preingest`: imports transcripts directly into OpenViking, then evaluates through Hermes against the preloaded OpenViking state.

Use `run_full_eval.sh` for normal runs:

```bash
cd benchmark/locomo/hermes
./run_full_eval.sh --suite native
./run_full_eval.sh --suite e2e
./run_full_eval.sh --suite preingest
```

## Required Services

- Hermes gateway running at `HERMES_URL` (default: `http://127.0.0.1:8642`).
- OpenViking server running at `OPENVIKING_URL` (default: `http://127.0.0.1:1933`) for `e2e` and `preingest`.
- LoCoMo dataset at `../data/locomo10.json`, or set `LOCOMO_JSON=/path/to/locomo10.json`.
- Judge model credentials through `JUDGE_TOKEN` or `ARK_API_KEY`.

The default OpenViking benchmark setup is local and does not require an API key. For non-default namespaces or authenticated servers, set `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_AGENT`, and `OPENVIKING_API_KEY` consistently for import and eval.

## Fresh Runs

For OpenViking suites, start from a fresh OpenViking workspace when using `--force-ingest`:

```bash
rm -rf ~/.openviking/data/*
./run_full_eval.sh --suite e2e --force-ingest --force-eval
```

`--force-ingest` resets benchmark CSV/token files. It does not delete OpenViking server state. If old OpenViking archives remain, deterministic benchmark session IDs can reuse stale extracted memories.

Use `-cp` or `--checkpoint` on `e2e` and `preingest` runs to copy the OpenViking state after import:

```bash
./run_full_eval.sh --suite e2e -cp
```

## Results And Resume Behavior

Each run writes to a timestamped `result_*` directory unless `RESULT_DIR` or `--result-dir` is provided. Important files:

- `import_success.csv`: successful ingest sessions and Hermes ingest usage where applicable.
- `qa_results.csv`: answers, Hermes QA usage, tool calls, judge result, and reasoning.
- `import_true_tokens.csv`: OpenViking model-token delta observed after import.
- `eval_true_tokens.csv`: OpenViking model-token delta observed after QA.
- `stats.log`: final score and token summary.

Interrupted runs can be resumed in the same result directory. OpenViking token delta CSVs append one row per completed import/eval pass, so final stats sum all rows rather than reading only the last row.

Hermes token accounting prefers `state.db` when available through `HERMES_STATE_DB` or `HERMES_HOME/state.db`; otherwise stats fall back to the benchmark CSV usage fields.
