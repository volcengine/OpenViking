# LoCoMo Benchmark — mem0 Evaluation

Evaluate mem0 memory retrieval on the [LoCoMo](https://github.com/snap-stanford/locomo) benchmark using OpenClaw as the agent.

## Overview

Two-phase pipeline:

1. **Ingest** — Import LoCoMo conversations into mem0 (one `user_id` per sample)
2. **Eval** — Send QA questions to OpenClaw agent (which recalls from mem0), then judge answers with an LLM

## Prerequisites

- [OpenClaw](https://openclaw.ai) installed and configured
- `openclaw-mem0` plugin installed (`~/.openclaw/extensions/openclaw-mem0`)
- `~/.openclaw/openclaw.json` with `plugins.slots.memory = "openclaw-mem0"`
- API keys in `~/.openviking_benchmark_env`:

```env
MEM0_API_KEY=m0-...
ARK_API_KEY=...         # Volcengine ARK, used for judge LLM
```

- Python dependencies:

```bash
uv sync --frozen --extra dev
```

## Data

LoCoMo 10-sample dataset at `benchmark/locomo/data/locomo10.json`:

- 10 samples (conversations between two people)
- 1986 QA pairs across 5 categories:
  - 1: single-hop
  - 2: multi-hop
  - 3: temporal
  - 4: world-knowledge
  - 5: adversarial (skipped by default)

## Step 1 — Ingest

Import conversations into mem0. Each sample is stored under `user_id = sample_id` (e.g. `conv-26`).

```bash
# Ingest all 10 samples
python ingest.py

# Ingest a single sample
python ingest.py --sample conv-26

# Force re-ingest (ignore existing records)
python ingest.py --sample conv-26 --force-ingest

# Clear all ingest records and start fresh
python ingest.py --clear-ingest-record
```

Key options:

| Option | Description |
|--------|-------------|
| `--sample` | Sample ID (e.g. `conv-26`) or index (0-based). Default: all |
| `--sessions` | Session range, e.g. `1-4` or `3`. Default: all |
| `--limit` | Max samples to process |
| `--force-ingest` | Re-ingest even if already recorded |
| `--clear-ingest-record` | Clear `.ingest_record.json` before running |

Ingest records are saved to `result/.ingest_record.json` to avoid duplicate ingestion.

## Step 2 — Eval

Send QA questions to OpenClaw agent and optionally judge answers.

Before each sample, `eval.py` automatically:
1. Updates `~/.openclaw/openclaw.json` to set `openclaw-mem0.config.userId = sample_id`
2. Restarts the OpenClaw gateway to pick up the new config
3. Verifies the correct `userId` is active via a dummy request

```bash
# Run QA + judge for all samples (6 concurrent threads)
python eval.py --threads 6 --judge

# Single sample
python eval.py --sample conv-26 --threads 6 --judge

# First 12 questions only
python eval.py --sample conv-26 --count 12 --threads 6 --judge

# Judge-only (grade existing responses in CSV)
python eval.py --judge-only
```

Key options:

| Option | Description |
|--------|-------------|
| `--sample` | Sample ID or index. Default: all |
| `--count` | Max QA items to process |
| `--threads` | Concurrent threads per sample (default: 10) |
| `--judge` | Auto-judge each response after answering |
| `--judge-only` | Skip QA, only grade ungraded rows in existing CSV |
| `--no-skip-adversarial` | Include category-5 adversarial questions |
| `--openclaw-url` | OpenClaw gateway URL (default: `http://127.0.0.1:18789`) |
| `--openclaw-token` | Auth token (or `OPENCLAW_GATEWAY_TOKEN` env var) |
| `--judge-base-url` | Judge API base URL (default: Volcengine ARK) |
| `--judge-model` | Judge model (default: `doubao-seed-2-0-pro-260215`) |
| `--output` | Output CSV path (default: `result/qa_results.csv`) |

Results are written to `result/qa_results.csv`. Failed (`[ERROR]`) rows are automatically removed at the start of each run and retried.

## Output

`result/qa_results.csv` columns:

| Column | Description |
|--------|-------------|
| `sample_id` | Conversation sample ID |
| `question_id` | Unique question ID (e.g. `conv-26_qa0`) |
| `question` / `answer` | Question and gold answer |
| `category` / `category_name` | Question category |
| `response` | Agent response |
| `input_tokens` / `output_tokens` / `total_tokens` | LLM token usage (all turns summed) |
| `time_cost` | End-to-end latency (seconds) |
| `result` | `CORRECT` or `WRONG` |
| `reasoning` | Judge's reasoning |

## Summary Output

After eval completes:

```
=== Token & Latency Summary ===
  Total input tokens : 123456
  Avg time per query : 18.3s

=== Accuracy Summary ===
  Overall: 512/1540 = 33.25%
  By category:
    multi-hop           : 120/321 = 37.38%
    single-hop          : 98/282 = 34.75%
    temporal            : 28/96  = 29.17%
    world-knowledge     : 266/841 = 31.63%
```

## Delete mem0 Data

```bash
# Delete a specific sample
python delete_user.py conv-26

# Delete all samples from the dataset
python delete_user.py --from-data

# Delete first N samples
python delete_user.py --from-data --limit 3
```
