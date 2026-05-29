# VikingDB BM25 Grep Benchmark

Benchmark suite for evaluating OpenViking's grep retrieval with VikingDB BM25 engine.

## Directory Structure

```
vikingdb_bm25/
├── ai_wiki.txt              # Source text for synthetic data generation
├── effectiveness/            # Retrieval effectiveness (recall/precision/F1)
│   ├── step1_add_resource.py
│   ├── step2_reindex.py
│   └── step3_quality.py
└── performance/              # Retrieval performance (latency + recall at scale)
    ├── step0_prepare_data.py
    ├── step1_add_resource.py
    ├── step2_reindex.py
    └── step3_benchmark.py
```

## Effectiveness — Retrieval Quality

Tests whether grep can find **all** matching files in real code repositories.

**Data source:** Real code repos (download manually, place under `~/.openviking/data/benchmark/`).

| Step | Script | Description |
|------|--------|-------------|
| 1 | `step1_add_resource.py` | Import code repos (no indexing, fast) |
| 2 | `step2_reindex.py` | Async reindex via openviking-server (concurrency=2, polling) |
| 3 | `step3_quality.py` | Compare SDK grep vs local regex ground truth |

### Usage

```bash
# Step 1: Import repos (no VLM/embedding)
cd effectiveness/
python3 step1_add_resource.py --source ~/.openviking/data/benchmark/OpenViking-main

# Step 2: Build vector indexes (requires openviking-server running)
python3 step2_reindex.py
# Optional: --concurrency N  (default: 2)

# Step 3: Edit KEYWORDS list in step3_quality.py, then run
python3 step3_quality.py
```

## Performance — Latency & Recall at Scale

Tests grep speed and recall on a large synthetic dataset (default: 100K files).

**Data source:** Generated from `ai_wiki.txt` with target words injected at known probabilities.

| Step | Script | Description |
|------|--------|-------------|
| 0 | `step0_prepare_data.py` | Generate synthetic dataset (dir_xxx/wiki_xxx.txt) |
| 1 | `step1_add_resource.py` | Import data (no VLM/embedding, fast) |
| 2 | `step2_reindex.py` | Async reindex via openviking-server (concurrency=2, polling) |
| 3 | `step3_benchmark.py` | Measure latency and recall |

### Target Words

12 words across 4 probability tiers:

| Probability | Words | Expected hits (per 100K files) |
|-------------|-------|-------------------------------|
| 50% | quantumnexus, synapseflow, deepvector | ~50,000 |
| 10% | bm25engine, vikingcore, retrievex | ~10,000 |
| 0.1% | zephyrhash, cryptolattice, nebulalink | ~100 |
| 0.01% | xenoform, quarkpulse, omegabind | ~10 |

### Usage

```bash
cd performance/

# Step 0: Generate data (default: 100 dirs x 1000 files = 100K files)
python3 step0_prepare_data.py

# Step 1: Import without indexing (fast)
python3 step1_add_resource.py

# Step 2: Build vector indexes (requires openviking-server running)
python3 step2_reindex.py
# Optional: --concurrency N  (default: 2)

# Step 3: Benchmark — run with different engine configs
#   Run A: fs engine
#     1. Set ov.conf: "grep": {"engine": "fs"}
#     2. Restart server
python3 step3_benchmark.py --engine-label fs

#   Run B: auto engine (bm25)
#     1. Set ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
#     2. Restart server
python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json
```

## Key Concepts

- **Effectiveness** tests use real code and measure exact recall/precision against ground truth
- **Performance** tests use synthetic data with known injection probabilities for approximate recall
- Both follow the same workflow: import (no indexing) → reindex → benchmark/evaluate
- Both support **resumable** execution via progress files (separate for import and reindex)
- Change grep engine via `ov.conf` and restart the server between benchmark runs
