# VikingDB BM25 Grep Benchmark

Benchmark suite for evaluating OpenViking's grep retrieval with VikingDB BM25 engine.

## Directory Structure

```
vikingdb_bm25/
├── ai_wiki.txt              # Source text for synthetic data generation
├── effectiveness/            # Retrieval effectiveness (recall/precision/F1)
│   ├── step1_add_resource.py
│   └── step2_quality.py
└── performance/              # Retrieval performance (latency + returned match count at scale)
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
| 1 | `step1_add_resource.py` | Import code repos (with indexing, single import) |
| 2 | `step2_quality.py` | Compare grep results vs ground truth (fs engine, cached) |

### Usage

```bash
# Step 1: Import repos (with VLM/embedding, single import)
cd effectiveness/
python3 step1_add_resource.py --source ~/.openviking/data/benchmark/OpenViking-main

# Step 2: Evaluate retrieval quality
#   First run MUST use engine=fs in ov.conf to generate ground truth cache:
#     1. Set ov.conf: "grep": {"engine": "fs"}
#     2. Restart server
python3 step2_quality.py --keywords grep reindex SyncHTTPClient

#   Subsequent runs can use any engine (ground truth is read from cache):
#     1. Set ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
#     2. Restart server
python3 step2_quality.py --keywords grep reindex SyncHTTPClient

# Optional: --regenerate-ground-truth  (force recompute, requires engine=fs)
```

## Performance — Latency at Scale

Tests grep speed and returned match count on a large synthetic dataset (default: 200K files).

**Data source:** Generated from `ai_wiki.txt` with target words injected at known probabilities.

| Step | Script | Description |
|------|--------|-------------|
| 0 | `step0_prepare_data.py` | Generate synthetic dataset (dir_xxx/wiki_xxx.txt) |
| 1 | `step1_add_resource.py` | Import data (no VLM/embedding, fast) |
| 2 | `step2_reindex.py` | Async reindex via openviking-server (concurrency=16, polling) |
| 3 | `step3_benchmark.py` | Measure latency and returned match count with `node_limit=256` |

### Target Words

15 words across 5 probability tiers:

These word groups are defined in `performance/step0_prepare_data.py` and reused by `performance/step3_benchmark.py`.

| Probability | Words | Expected hits (per 200K files) |
|-------------|-------|-------------------------------|
| 1% | heliofract, prismcache, fluxkernel | ~2,000 |
| 0.1% | auroracode, kiteshade, glyphvector | ~200 |
| 0.1% | cortexmint, latticewave, spiralsync | ~200 |
| 0.05% | ripplehash, embertrace, novaframe | ~100 |
| 0.01% | zephyrloom, quartzrelay, nebulaindex | ~20 |

### Usage

```bash
cd performance/

# Step 0: Generate data (default: 200 dirs x 1000 files = 200K files)
python3 step0_prepare_data.py

# Optional: append more data for scale-out without overwriting existing dirs
python3 step0_prepare_data.py --start-dir 100 --num-dirs 100

# Step 1: Import without indexing (fast)
python3 step1_add_resource.py

# Step 2: Build vector indexes (requires openviking-server running)
python3 step2_reindex.py
# Optional: --concurrency N  (default: 16)

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

- **Effectiveness** tests compare grep results against ground truth from fs-engine grep (cached locally)
- **Performance** tests compare grep latency and returned match counts between engine configs; no ground truth is generated
- **Effectiveness** imports real repos with indexing in a single step, then evaluates quality
- **Performance** imports synthetic data without indexing, builds vector indexes asynchronously, then benchmarks latency
- **Performance** import/reindex steps support resumable execution via progress files
- Change grep engine via `ov.conf` and restart the server between benchmark runs
- To horizontally scale the synthetic dataset, run Step 0 again with a new `--start-dir`,
  then rerun Step 1 and Step 2.
