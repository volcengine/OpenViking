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

See [PRELIMINARY_RESULTS.md](PRELIMINARY_RESULTS.md) for the first public-data
engineering checkpoint and its limitations.

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

To scan the main CAGRA search-quality knob without rebuilding the graph for
every value, use `--cagra-itopk-sizes`. Other search parameters are shared by
all variants:

```bash
python benchmark/cuvs/run_index_benchmark.py \
  --backends cuvs_brute_force,cuvs_cagra \
  --cagra-search-params '{"search_width":1}' \
  --cagra-itopk-sizes 32,64,128,256
```

If `itopk_size` alone does not reach the target recall, add a `search_width`
sweep. The harness evaluates the Cartesian product while still building CAGRA
only once:

```bash
python benchmark/cuvs/run_index_benchmark.py \
  --backends cuvs_cagra \
  --ann-benchmarks-hdf5 /data/glove-100-angular.hdf5 \
  --metric cosine \
  --cagra-itopk-sizes 64,128,256,512 \
  --cagra-search-widths 1,2,4,8
```

## Public ANN datasets

Random Gaussian vectors are useful for exact-search scaling, but CAGRA recall
should be measured on a public dataset with ground truth. The harness accepts
the HDF5 format published by
[ann-benchmarks](https://github.com/erikbern/ann-benchmarks):

```bash
curl -fL \
  https://ann-benchmarks.com/glove-100-angular.hdf5 \
  -o /data/glove-100-angular.hdf5

python benchmark/cuvs/run_index_benchmark.py \
  --data-root /data/openviking-cuvs \
  --ann-benchmarks-hdf5 /data/glove-100-angular.hdf5 \
  --metric cosine \
  --backends cuvs_brute_force,cuvs_cagra \
  --query-batch-size 1 \
  --search-repetitions 5 \
  --cagra-itopk-sizes 32,64,128,256 \
  --cagra-search-widths 1,2,4
```

The first run converts `train`, `test`, and `neighbors` into reusable NumPy
memory maps. Angular datasets are normalized once so inner product has cosine
ranking. The result records the source SHA-256 but not the local source path.

`--ann-vector-limit` and `--ann-query-limit` can shorten exploratory runs. A
vector limit disables the supplied ground truth because neighbor IDs may refer
to omitted rows; include `native` or `cuvs_brute_force` in that case. A query
limit retains the corresponding prefix of supplied ground truth.

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
