# Hermes LoCoMo Benchmark

This directory runs LoCoMo QA through Hermes Agent with the following memory and retrieval paths:

- `native`: imports transcripts into Hermes native memory, then evaluates through Hermes.
- `e2e`: imports transcripts through Hermes with the OpenViking memory plugin enabled, commits the resulting OpenViking sessions, then evaluates through Hermes.
- `preingest`: imports transcripts directly into OpenViking, then evaluates through Hermes against the preloaded OpenViking state.
- `agentic-rag`: indexes raw conversation passages and exposes a search tool to Hermes during QA.

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

The default OpenViking benchmark setup is local and does not require an API key. For non-default namespaces or authenticated servers, set `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_API_KEY` consistently for import and eval.

## Agentic RAG baseline

By default, the runner embeds windows of up to four consecutive dialogue turns
with one turn of overlap. Set `LOCOMO_RAG_CHUNK_CHARS=512` to build windows of up
to 512 Unicode characters including the session header, with up to 64
characters of overlap. Both modes keep the dialog text, timestamps and visual
captions within session boundaries. Each lookup searches only the assigned
sample and returns up to five raw passages. Hermes writes the first query and
can reformulate it after seeing the results. The index contains only
conversation data. QA answers and evidence annotations stay with the evaluator
and judge.

Start a dedicated Hermes gateway with an empty profile. Install the benchmark
plugin into that profile so the gateway can load it.

```bash
# From the repository root.
export HERMES_HOME=/absolute/path/to/isolated-hermes-profile
mkdir -p "$HERMES_HOME/plugins"
ln -s "$PWD/benchmark/locomo/hermes/locomo_rag" "$HERMES_HOME/plugins/locomo-rag"
```

Set the profile's backend model and credentials separately and use these
profile settings to make the history search tool the available retrieval path.

```yaml
plugins:
  enabled: [locomo-rag]
platform_toolsets:
  api_server: [locomo-rag, no_mcp]
memory:
  provider: builtin
  memory_enabled: false
  user_profile_enabled: false
```

Start the gateway with the following variables available to both the gateway
and the benchmark runner. Use the same file path from both processes.

| Variable | Purpose |
| --- | --- |
| `LOCOMO_RAG_INDEX` | Absolute path for the generated JSON index. |
| `LOCOMO_RAG_EMBEDDING_MODEL` | Embedding model ID or deployment ID usable by the endpoint. |
| `LOCOMO_RAG_EMBEDDING_URL` | OpenAI-compatible base URL, or the full ModelArk `/embeddings/multimodal` URL for the 1024-dimensional Doubao vision embedding model. |
| `LOCOMO_RAG_EMBEDDING_KEY` | Credential for the embedding endpoint. |
| `LOCOMO_RAG_CHUNK_CHARS` | Optional maximum character count per chunk, including the session header. Use a distinct index path when changing it. |
| `LOCOMO_RAG_EMBEDDING_WORKERS` | ModelArk embedding concurrency during indexing. Defaults to 4. |
| `HERMES_URL`, `HERMES_TOKEN` | Dedicated Hermes API URL and matching gateway key. The gateway separately needs the API key for its QA model backend. |
| `JUDGE_BASE_URL`, `JUDGE_MODEL`, `JUDGE_TOKEN` | Judge endpoint, model and credential. |

For a comparison with a published LoCoMo run, match the QA model in the Hermes
profile, judge model, prompts, and dataset with the control. The
[results PR](https://github.com/volcengine/OpenViking/pull/2275) identifies
`gpt-4o-mini` as the judge for its LoCoMo table. Set the judge and its endpoint
explicitly for that comparison. The [benchmark report](../../README.md) summarizes the published settings.
For a dated Azure OpenAI endpoint, set `JUDGE_API_VERSION`, use the Azure
resource URL in `JUDGE_BASE_URL`, and set `JUDGE_MODEL` to the deployment ID.

```bash
cd benchmark/locomo/hermes
./run_full_eval.sh --suite agentic-rag --sample 0 --count 1
```

The first run embeds the entire input dataset. `--sample` and `--count` limit QA.
For additional runs with the same dataset, embedding model and chunk size, reuse the index.
Use a new result directory or `--force-eval` after rebuilding it.

The existing judge and stats pipeline scores the same questions as the other
Hermes suites. `qa_results.csv` adds `rag_search_trace` with ordered search
queries and result chunk IDs, together with query embedding token counts when
provided by the endpoint. A later call after a result is visible in this
sequence, including whether its query changed. The index separately records
the embedding model and ingestion token count.

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
