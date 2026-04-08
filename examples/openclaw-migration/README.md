# OpenClaw → OpenViking Memory Migration

Imports your existing [OpenClaw](https://github.com/openclaw-ai/openclaw) memory files directly into OpenViking's memory system.

**Zero LLM calls.** Content is preserved verbatim; only embeddings are generated.

---

## What gets migrated

| OpenClaw file | OpenViking category | Why |
|---|---|---|
| `MEMORY.md`, `memory.md` | `entities` | Curated durable knowledge — projects, people, concepts |
| `YYYY-MM-DD.md` (daily logs) | `events` | Time-stamped records, decisions, milestones |
| `YYYY-MM-DD-slug.md` (session summaries) | `cases` | Problem + solution context from specific sessions |
| Everything else | `entities` | Safe fallback for arbitrary markdown files |

---

## Requirements

```bash
pip install openviking
```

A valid `~/.openviking/ov.conf` (or equivalent) with an embedding model configured is required for the real run.

---

## Usage

```bash
# Preview — no data written
python migrate.py --dry-run

# Migrate with defaults
#   OpenClaw dir : ~/.openclaw/workspace
#   OV data dir  : ./data
#   identity     : account=default, user=default, agent=default
python migrate.py

# Custom paths and identity
python migrate.py \
  --openclaw-dir ~/myworkspace \
  --ov-data-dir  ./ov-data \
  --account-id   myaccount \
  --user-id      myuser \
  --agent-id     myagent

# Force all files into a single category
python migrate.py --category events
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--openclaw-dir PATH` | `~/.openclaw/workspace` | Path to OpenClaw workspace |
| `--ov-data-dir PATH` | `./data` | OpenViking data directory |
| `--account-id TEXT` | `default` | Account ID (alphanumeric/underscore/hyphen) |
| `--user-id TEXT` | `default` | User ID |
| `--agent-id TEXT` | `default` | Agent ID |
| `--category TEXT` | *(auto)* | Override category for all files |
| `--dry-run` | off | Preview without writing |

---

## Dry-run example

```
OpenClaw → OpenViking Migration (DRY RUN)
Found 3 file(s) in /Users/alice/.openclaw/workspace

  MEMORY.md                    →  entities      (3,421 chars)
  memory/2026-03-15.md         →  events        (1,832 chars)
  memory/2026-03-15-bug-fix.md →  cases         (942 chars)

Would import: 3 file(s) | 0 LLM calls | ~3 embedding job(s) queued | 6,195 chars total
Run without --dry-run to proceed.
```

---

## How it works

For each file the script:

1. Reads the Markdown content.
2. Classifies it into an OV memory category (or uses `--category`).
3. Builds a one-line `abstract` (first non-empty line, ≤ 100 chars).
4. Calls `MemoryExtractor.create_memory()` — writes the file to VikingFS.
5. Calls `SessionCompressor._index_memory()` — enqueues an embedding job.

Memories appear in OV's memory-specific retrieval (`viking://user/<user>/memories/`) as soon as the embedding worker processes the queue.

---

## Running tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_openclaw_migration.py -v --no-cov
```

---

## Verifying the import

After a real run, confirm memories were written:

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
results = client.find("viking://user/default/memories/entities/")
print(results)
```
