# OpenViking cuVS index benchmark

This benchmark compares the existing OpenViking native flat index with cuVS
brute-force and CAGRA. It is intentionally limited to vector-index work and
does not include embedding, HTTP, record lookup, reranking, or LLM inference.

The output contains:

- index build time and first-search batch/per-query latency;
- warm p50/p95/p99 latency and QPS;
- Recall@K against the first exact backend;
- current process RSS and GPU-memory deltas;
- raw per-batch latency samples and runtime metadata.

## Quick smoke test

Run from the repository root in the cuVS development image:

```bash
python benchmark/cuvs/run_index_benchmark.py \
  --vector-count 10000 \
  --dimension 128 \
  --query-count 100 \
  --k 10 \
  --query-batch-size 1 \
  --backends native,cuvs_brute_force,cuvs_cagra
```

For large query batches, use `--search-repetitions` so percentile and QPS
statistics contain multiple timed batches without generating another base
dataset. Neighbor/recall output is retained from the first repetition.

Large datasets are generated as NumPy memory maps. Put `--data-root` on a
volume with enough capacity rather than in the Git worktree:

```bash
python benchmark/cuvs/run_index_benchmark.py \
  --data-root /shared/benchmark-data/openviking-cuvs \
  --vector-count 1000000 \
  --dimension 1024 \
  --query-count 1000 \
  --k 10 \
  --query-batch-size 1
```

The generated dataset is deterministic for a given shape, metric, and seed and
is reused by later runs. Pass `--force-generate` to replace it.

By default the harness reads the complete memory-mapped dataset once before
timing any backend build. This removes page-cache order bias when several
backends run in one process. Use `--no-preload-dataset` only when intentionally
measuring cold storage reads.

## Backend interpretation

- `native` is OpenViking's C++ flat index and is exact.
- `cuvs_brute_force` is GPU exact search.
- `cuvs_cagra` is approximate; the result reports Recall@K against an exact
  backend from the same run.

The native measurement uses the current OpenViking single-query call path; it
is not a claim about the maximum throughput of a separately tuned,
multi-threaded CPU ANN library. Record CPU and GPU hardware with every result
and describe this scope when publishing comparisons.

For CAGRA parameter sweeps, pass JSON objects:

```bash
python benchmark/cuvs/run_index_benchmark.py \
  --backends cuvs_brute_force,cuvs_cagra \
  --cagra-build-params '{"graph_degree":64,"intermediate_graph_degree":96}' \
  --cagra-search-params '{"itopk_size":128}'
```

`query_batch_size=1` reflects the current OpenViking integration most closely.
Larger batches measure the vector-index capacity: cuVS executes the batch on
the GPU, while the current native wrapper processes each query sequentially.
Do not present batch results as current server throughput without also running
the collection/server benchmarks.

## First comparison matrix

Start with 1024-dimensional cosine vectors at 100K and 1M rows:

1. batch size 1, K=10, all three backends;
2. batch size 128, K=10, all three backends;
3. CAGRA `itopk_size` 32/64/128, reporting QPS only at matched Recall@10;
4. five independent timed runs after one dataset-generation pass.

Add 5M only after the harness and memory measurements are stable.

Random high-dimensional Gaussian vectors are useful for exact-search scaling,
but are not representative enough for CAGRA quality claims. Use a public ANN
dataset or real embedding corpus before reporting a CAGRA recall/QPS frontier.
