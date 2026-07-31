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

GPU-memory fields are `cudaMemGetInfo` snapshots around build. The delta
excludes the CUDA runtime/context already present in the pre-build snapshot and
is a retained-build measurement, not a sampled peak. Report that distinction
when using the values for capacity planning.

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

Do not use within-process repetitions as a substitute for independent runs.
After producing result files from separate processes, aggregate their medians
and median absolute deviations with:

```bash
python benchmark/cuvs/summarize_index_runs.py \
  /data/results/run-{1,2,3,4,5}.json \
  --output /data/results/summary.json
```

The summarizer requires matching dataset metadata, parameters, and
backend/search variants. It records input basenames rather than local paths.

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

- `native` is OpenViking's C++ flat index and is exact within its configured
  representation.
- `cuvs_brute_force` is GPU exact search over its retained representation.
- `cuvs_brute_force_fp16` uses the same exact algorithm after casting dataset
  and queries to float16; report Recall@K against float32.
- `cuvs_cagra` is approximate; the result reports Recall@K against an exact
  backend from the same run.
- `cuvs_cagra_fp16` combines CAGRA approximation with float16 storage/query
  casts and must be compared on both recall and retained VRAM.

The harness rejects any FP16 or CAGRA-only run that has neither supplied
ground truth nor an exact `native`/`cuvs_brute_force` backend. A missing recall
value is displayed as `N/A`; it is never treated as perfect recall.

Run the dtype frontier in one clean process with:

```bash
python benchmark/cuvs/run_index_benchmark.py \
  --backends cuvs_brute_force,cuvs_brute_force_fp16,cuvs_cagra,cuvs_cagra_fp16 \
  --vector-count 100000 \
  --dimension 768 \
  --query-count 1000 \
  --k 10
```

The index-only harness constructs the native index without an explicit
`Quant`, so both native and cuVS brute-force use float32 there. The collection
and async service harnesses deliberately retain normal application behavior:
the native CPU index uses its default per-vector-scale int8 quantization while
the cuVS device dataset and queries use the configured dtype (float32 by
default, or explicitly selected float16). The host record shadow retains
prepared Python floating-point values; only the device dataset and queries are
cast to the configured dtype when each is created. `dtype` does not rewrite the
host shadow or the native index metadata. Collection/service results are
therefore application-path comparisons, not equal-dtype or equal-memory
comparisons, and must be reported with Recall@K and the dtype caveat.

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

`query_batch_size=1` most closely represents one public OpenViking API request
and the default configuration, where request micro-batching is disabled.
Opt-in brute-force micro-batching can internally coalesce compatible concurrent
single-row API requests into one matrix-query call; that behavior must be
measured through the collection/service concurrency path rather than inferred
from this index harness. Larger explicit batches measure vector-index capacity:
cuVS executes the batch on the GPU, while the current native wrapper processes
each query sequentially. Do not present explicit batch results as server
throughput without also running the collection/server benchmarks and reporting
whether request micro-batching was enabled.

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

## Collection, filter, and lifecycle benchmark

`run_collection_benchmark.py` exercises the OpenViking collection adapter
rather than calling cuVS directly. It includes scalar-filter evaluation,
label-to-record lookup, result normalization, and lazy rebuild after mutation
or restart:

```bash
python benchmark/cuvs/run_collection_benchmark.py \
  --vector-count 100000 \
  --dimension 768 \
  --query-count 50 \
  --backends native,cuvs_brute_force,auto_cuvs \
  --mutation-sizes 1,100,1000,10000 \
  --filter-cache-size 16 \
  --auto-filter-native-threshold 2000 \
  --auto-path-filter-native-threshold 200 \
  --data-root /data/openviking-cuvs
```

The filter matrix covers unfiltered, 10%, 1%, and 0.1% selectivity with both
uniform and clustered scalar fields, plus hierarchical URI prefixes with the
same target selectivities. Lifecycle output keeps write latency, the
write-after first query, warm query, and restart first query separate so a lazy
rebuild is not hidden in steady-state search latency.

`--filter-cache-size` controls the cuVS LRU of prepared GPU bitsets. The
per-scenario `first_query_ms` includes construction of a new filter mask;
timed warm latency reuses the cached mask. Set it to zero to reproduce the
uncached path.

`auto_cuvs` additionally measures the memory-aware backend's per-query routing.
It keeps unfiltered and wider filters on cuVS while routing small scalar and
path candidate sets to native recall according to the two configurable
thresholds. Set a threshold to zero to disable that part of the policy.
Before the normal scenario matrix, the auto backend also records
`prebuild_selective_query` with a 0.1% scalar filter. This query runs while the
GPU index is still dirty, so its latency and GPU-memory delta verify that
native routing happens before GPU admission and rebuild. The subsequent
unfiltered scenario remains responsible for measuring the lazy GPU build.

Aggregate independent process runs with median and median absolute deviation:

```bash
python benchmark/cuvs/summarize_collection_runs.py \
  results/collection-run-{1,2,3,4,5}.json \
  --output results/collection-summary.json
```

## Async service concurrency benchmark

`run_service_concurrency_benchmark.py` uses OpenViking's
`VikingVectorIndexBackend` and its `asyncio.to_thread` scheduling boundary. It
keeps query vectors precomputed, so this is a service-facade benchmark rather
than an embedding or HTTP benchmark. It covers repeated tenant filters, a
repeated 10% filter, a new filter per request, and concurrent readers after a
single-record mutation:

```bash
python benchmark/cuvs/run_service_concurrency_benchmark.py \
  --vector-count 100000 \
  --dimension 768 \
  --query-count 64 \
  --concurrency 1,4,16,32,64 \
  --cached-request-count 200 \
  --unique-request-count 32 \
  --data-root /data/openviking-cuvs
```

Add `--backends auto_cuvs_background` to isolate the optional coalescing
background-rebuild path. The harness waits for a warm GPU snapshot before each
mutation, then measures the immediate read burst while the dirty auto-cuVS
index routes queries to native search. Rebuild waiting is kept outside the
reported request wall time; the JSON records that wait separately.

The normal tenant scope means every public service-facade query includes an
`account_id` filter. The benchmark reports p50/p95/p99, QPS, errors, and the
post-mutation burst separately. A later end-to-end server benchmark should add
HTTP, authentication, embedding, and reranking rather than folding those costs
into this vector scheduling result.

Aggregate independent service processes with:

```bash
python benchmark/cuvs/summarize_service_runs.py \
  results/service-run-{1,2,3,4,5}.json \
  --output results/service-summary.json
```
