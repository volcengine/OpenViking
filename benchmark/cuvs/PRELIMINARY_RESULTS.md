# Preliminary cuVS index results

These results are an engineering checkpoint for the OpenViking cuVS
integration. They are not a final performance claim. The measurements use one
GPU, one public ANN dataset, and deterministic synthetic vectors for exact
high-dimensional scaling. Every reported row aggregates five independent
processes as median +/- median absolute deviation (MAD).

> **Historical scope (pre-microbatch):** Every result below was collected from
> the measured revisions named in this document, before opt-in request
> micro-batching and its warm admission fast path were implemented. Statements
> about the "current integration" describe those measured revisions, not the
> later implementation. The tables are retained as historical engineering
> checkpoints and must not be used to infer current concurrent-request
> throughput.

## Public-dataset setup

- Harness revision: `5dbc6a570404c9d1e2c8b584f6447919d0fee62a`
- Dataset: [ann-benchmarks](https://github.com/erikbern/ann-benchmarks)
  `glove-100-angular`
- Source SHA-256:
  `544af1d5e84e112cd4749571dcfd8ca109818a572f850af75a3a09e093a953c4`
- Base vectors: 1,183,514 x 100D float32
- Queries: 10,000; K=10; cosine ranking via normalized inner product
- Ground truth: exact neighbors supplied by ann-benchmarks
- GPU: NVIDIA H20
- Software: cuVS 26.06, CuPy 14.1.1, CUDA runtime 12.9

The native backend is the existing OpenViking CPU flat-index call path. In
this document, **cuVS GPU exact** specifically means cuVS brute-force on the
GPU. It does not mean CAGRA. CAGRA is the approximate GPU index and uses
`graph_degree=64` and `intermediate_graph_degree=128` for the high-recall
results. Each public-dataset process times all 10,000 queries after ten
warm-up batches.

For the measurements in this document, the index-only harness used float32 for
both native and cuVS exact search. The collection and service runs intentionally
preserved normal application behavior instead: native kept its default
per-vector-scale int8 index, while cuVS was configured with `dtype=float32`.
The current integration also supports an explicitly selected float16 device
dataset/query; its host record shadow retains prepared Python floating-point
values, and only the device dataset and query are cast to the configured dtype
when each is created. Opting into cuVS does not rewrite the native index. The
recorded collection and service results are therefore not equal-dtype or
equal-memory comparisons and report native Recall@K against cuVS brute-force.

## GPU memory footprint

All cuVS measurements reported here used a float32 device dataset, so their
brute-force payload was `N * dimension * 4` bytes. In the current integration,
that device payload follows the configured `dtype`: float32 uses 4 bytes and
float16 uses 2 bytes per component. The host shadow retains prepared Python
floating-point values regardless of the device dtype; only the device dataset
and query are cast to that dtype when each is created. CAGRA also retains a
uint32 neighbor graph of approximately `N * graph_degree * 4` bytes and may
require an intermediate graph during build. A cached filter bitset adds
approximately `N / 8` bytes.

The harness records `cudaMemGetInfo` immediately before and after index build.
The following retained-build deltas are medians from five clean processes:

| Dataset | Backend | GPU memory delta |
| --- | --- | ---: |
| 100K x 768D synthetic | cuVS brute-force | 294 MiB |
| 1M x 768D synthetic | cuVS brute-force | 2.9 GiB |
| 100K x 1024D synthetic | cuVS brute-force | 392 MiB |
| 1M x 1024D synthetic | cuVS brute-force | 3.9 GiB |
| 1,183,514 x 100D GloVe | cuVS brute-force | 452 MiB |
| 1,183,514 x 100D GloVe | cuVS CAGRA | 872 MiB |

These are not peak-VRAM measurements. Allocator state, CAGRA build parameters,
query batch size, and concurrent GPU use can add transient memory. The deltas
also exclude the approximately 327 MiB CUDA runtime/context baseline observed
before build in these processes. The opt-in memory-aware auto mode initializes
that runtime first, then estimates the configured-dtype device payload, CAGRA
retained and intermediate graphs, and filter cache; multiplies the result by a
2.0 default safety factor; and preserves 1 GiB of free memory. Insufficient
budget keeps the native path for that query and allows a later retry.

## Batch size 1

This is the closest index-level approximation of the measured pre-microbatch
OpenViking single-query integration. It includes Python dispatch,
host-to-device query copy, GPU execution, and result copy back to host. It
excludes embedding, HTTP, record lookup, reranking, and LLM work.

| Backend | Recall@10 | warm p50 (ms/query) | warm p95 (ms/query) | warm QPS |
| --- | ---: | ---: | ---: | ---: |
| OpenViking native exact | 1.0000 +/- 0.0000 | 40.209 +/- 0.071 | 40.798 +/- 0.043 | 24.7 +/- 0.1 |
| cuVS GPU brute-force exact | 1.0000 +/- 0.0000 | 0.796 +/- 0.005 | 0.815 +/- 0.005 | 1,254.5 +/- 7.1 |
| cuVS CAGRA ANN, `itopk_size=512` | 0.9633 +/- 0.0003 | 1.730 +/- 0.016 | 1.995 +/- 0.008 | 562.8 +/- 1.7 |
| cuVS CAGRA ANN, `itopk_size=2048` | 0.9944 +/- 0.0002 | 1.797 +/- 0.019 | 2.036 +/- 0.006 | 549.0 +/- 0.9 |

In this low-batch regime, cuVS GPU brute-force exact delivers a 50.5x median
warm-p50 speedup and 50.7x higher median QPS than the current native CPU exact
call path. CAGRA is slower than GPU brute-force exact even before requiring
0.99 recall, so CAGRA should not be selected for this dataset and query shape
solely because it is approximate.

Before warm-up, the first batch=1 search was 85.7 +/- 9.6 ms for native exact
and 104.0 +/- 2.9 ms for cuVS GPU brute-force exact. The observed ranges were
76.2--122.9 ms and 101.1--157.3 ms, respectively. Cold-start latency therefore
remains a separate integration concern; the warm comparison above must not be
read as startup latency.

## Batch size 128

This measures vector-index throughput capacity. It is not current OpenViking
server throughput because the integration currently submits one query at a
time.

| Backend | Recall@10 | QPS | Relative to GPU exact |
| --- | ---: | ---: | ---: |
| cuVS GPU brute-force exact | 1.0000 +/- 0.0000 | 35,495 +/- 34 | 1.00x |
| cuVS CAGRA ANN, `itopk_size=512` | 0.9628 +/- 0.0004 | 43,595 +/- 280 | 1.23x |
| cuVS CAGRA ANN, `itopk_size=2048` | 0.9943 +/- 0.0002 | 21,711 +/- 79 | 0.61x |

CAGRA shows a throughput benefit only at the lower recall point in this
initial run. At approximately 0.99 recall, GPU exact remains faster.

## High-dimensional exact scaling

To approximate common embedding dimensions without making unsupported ANN
quality claims, this matrix uses deterministic normalized Gaussian vectors and
compares only the two exact paths. The batch=1 crossover runs time 1,000
queries per process through 10K vectors and 200 queries per process at 100K
and 1M vectors. All rows have Recall@10=1.0.

The speedup column is native warm p50 divided by cuVS GPU brute-force warm
p50. Values above 1.0 mean GPU exact is faster.

| Dim | Vectors | Native p50 (ms) | GPU exact p50 (ms) | Speedup |
| ---: | ---: | ---: | ---: | ---: |
| 768 | 100 | 0.031 +/- 0.000 | 0.243 +/- 0.003 | 0.13x |
| 768 | 1K | 0.122 +/- 0.001 | 0.248 +/- 0.001 | 0.49x |
| 768 | 2K | 0.219 +/- 0.001 | 0.249 +/- 0.000 | 0.88x |
| 768 | 5K | 0.487 +/- 0.013 | 0.245 +/- 0.002 | 1.99x |
| 768 | 10K | 0.915 +/- 0.014 | 0.248 +/- 0.001 | 3.69x |
| 768 | 100K | 21.991 +/- 0.177 | 0.341 +/- 0.003 | 64.5x |
| 768 | 1M | 229.728 +/- 0.580 | 1.435 +/- 0.002 | 160.1x |
| 1024 | 100 | 0.039 +/- 0.000 | 0.244 +/- 0.002 | 0.16x |
| 1024 | 1K | 0.159 +/- 0.005 | 0.247 +/- 0.001 | 0.64x |
| 1024 | 2K | 0.285 +/- 0.002 | 0.250 +/- 0.002 | 1.14x |
| 1024 | 5K | 0.633 +/- 0.000 | 0.253 +/- 0.004 | 2.50x |
| 1024 | 10K | 1.234 +/- 0.048 | 0.252 +/- 0.001 | 4.90x |
| 1024 | 100K | 28.443 +/- 0.262 | 0.376 +/- 0.001 | 75.7x |
| 1024 | 1M | 306.730 +/- 0.838 | 1.708 +/- 0.002 | 179.6x |

On this integration path, native CPU exact remains faster through 2K vectors
at 768D, while GPU exact is faster by 5K. At 1024D, the observed crossover is
between 1K and 2K vectors. These are hardware- and implementation-specific
boundaries, not universal algorithm thresholds.

This is a warm crossover. The first cuVS search remained approximately
99--110 ms across the synthetic shapes, while native first-search latency was
below 1 ms at 2K vectors and below. Short-lived or rarely queried collections
therefore need a separate residency/cold-start policy even when their warm
vector count is above the crossover.

For a stable GPU capacity measurement, batch=128 runs use 50 search
repetitions, or 10,000 timed queries per process:

| Dim | Vectors | cuVS GPU exact QPS |
| ---: | ---: | ---: |
| 768 | 100K | 67,650 +/- 160 |
| 768 | 1M | 12,479 +/- 2 |
| 1024 | 100K | 57,385 +/- 10 |
| 1024 | 1M | 9,880 +/- 0.3 |

These capacity numbers do not represent current OpenViking server throughput;
the integration still submits one query at a time.

## Build scope

| Backend | Build time (s) | Scope |
| --- | ---: | --- |
| OpenViking native exact | 10.537 +/- 0.099 | Python `DeltaRecord` creation plus native upsert |
| cuVS GPU brute-force exact | 0.219 +/- 0.001 | host-to-device matrix copy plus index wrapper |
| cuVS CAGRA high-recall graph | 4.719 +/- 0.085 | matrix copy plus graph construction |

These build times do not isolate equivalent kernels. In particular, the native
path includes the current row-oriented OpenViking ingestion interface while
the cuVS paths accept the full matrix. They should be treated as integration
costs, not as a pure CPU-versus-GPU algorithm comparison.

The same caveat applies to high-dimensional exact build time and GPU-memory
delta:

| Dim | Vectors | Native build (s) | GPU exact build (s) | GPU delta (GiB) |
| ---: | ---: | ---: | ---: | ---: |
| 768 | 100K | 4.488 +/- 0.029 | 0.203 +/- 0.000 | 0.29 |
| 768 | 1M | 44.294 +/- 0.146 | 1.597 +/- 0.006 | 2.87 |
| 1024 | 100K | 6.165 +/- 0.030 | 0.210 +/- 0.001 | 0.38 |
| 1024 | 1M | 60.990 +/- 0.281 | 1.697 +/- 0.011 | 3.82 |

## Collection adapter, filter, and lifecycle

This matrix moves one level above the index microbenchmark. It calls
`CollectionAdapter.query()` and therefore includes OpenViking filter handling,
label-to-record lookup, result normalization, persistence, and lazy index
rebuild. The initial uncached-filter run uses revision
`84f79c5f52b553561299d42730949b612f3fe29c`; the prepared-filter-cache follow-up
uses revision `087f2a280dc06031665a0bbdb1ef26a9fa2735da`. Each result aggregates
five independent processes on the same H20/software setup as the exact-scaling
runs.

- Dataset: 100,000 deterministic normalized Gaussian vectors, 768D float32
- Queries: 50 per scenario; K=10; three warm-up queries
- Filters: 10%, 1%, and 0.1% target selectivity, with both uniformly
  distributed and contiguous matching records
- Mutations: upsert 1, 100, 1,000, and 10,000 records; delete 100 records;
  close and reopen the persistent collection

This comparison deliberately used the normal collection defaults. The native
flat index used its default int8 quantization, while these cuVS brute-force runs
used `dtype=float32`. Consequently, the native path was not the float exact
baseline from the index microbenchmark. Its Recall@10 against cuVS GPU
brute-force was 0.982 without a filter and 0.978--0.994 with filters. This is
both a quality and memory semantic that the integration must make explicit.

### Initial uncached-filter result

| Scenario | Native p50 (ms) | cuVS p50 (ms) | Native Recall@10 | cuVS Recall@10 | Relative p50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unfiltered | 9.413 +/- 0.238 | 0.970 +/- 0.006 | 0.982 | 1.000 | cuVS 9.7x faster |
| Uniform 10% | 1.871 +/- 0.041 | 106.306 +/- 2.204 | 0.988 | 1.000 | cuVS 56.8x slower |
| Uniform 1% | 0.509 +/- 0.003 | 105.816 +/- 2.022 | 0.986 | 1.000 | cuVS 207.8x slower |
| Uniform 0.1% | 0.319 +/- 0.002 | 105.292 +/- 2.721 | 0.994 | 1.000 | cuVS 330.2x slower |
| Clustered 10% | 1.374 +/- 0.002 | 106.706 +/- 1.297 | 0.978 | 1.000 | cuVS 77.7x slower |
| Clustered 1% | 0.468 +/- 0.001 | 104.634 +/- 1.308 | 0.982 | 1.000 | cuVS 223.3x slower |
| Clustered 0.1% | 0.321 +/- 0.004 | 104.219 +/- 1.748 | 0.994 | 1.000 | cuVS 324.9x slower |

Unfiltered collection lookup preserves a material GPU benefit: median QPS was
967.3 +/- 2.5 for cuVS and 106.4 +/- 3.4 for native, a 9.1x throughput ratio.
That is smaller than the index-only 100K x 768D result because collection
lookup adds fixed host-side work and the native collection path uses int8
quantization.

The initial filtered result identified an integration blocker, not an inherent
cuVS limitation. That revision evaluated the predicate against every Python
record and rebuilt the GPU prefilter mask on every query. All six cuVS filter
scenarios remained near 104--106 ms regardless of selectivity or record
distribution.

### Prepared-filter-cache follow-up

The follow-up adds a bounded LRU of prepared GPU bitsets and invalidates it on
upsert or delete. The first query for each new filter still performs the host
predicate scan; subsequent searches with the same filter reuse the device
bitset.

| Scenario | First cuVS query (ms) | Cached cuVS p50 (ms) | Native p50 (ms) | Cached relative p50 |
| --- | ---: | ---: | ---: | ---: |
| Uniform 10% | 141.022 +/- 1.248 | 1.077 +/- 0.014 | 1.832 +/- 0.066 | cuVS 1.70x faster |
| Uniform 1% | 133.535 +/- 0.772 | 0.989 +/- 0.011 | 0.520 +/- 0.002 | cuVS 1.90x slower |
| Uniform 0.1% | 123.198 +/- 0.699 | 0.948 +/- 0.002 | 0.330 +/- 0.003 | cuVS 2.88x slower |
| Clustered 10% | 121.070 +/- 0.637 | 1.090 +/- 0.003 | 1.389 +/- 0.011 | cuVS 1.27x faster |
| Clustered 1% | 119.758 +/- 1.763 | 0.987 +/- 0.004 | 0.470 +/- 0.003 | cuVS 2.10x slower |
| Clustered 0.1% | 119.441 +/- 1.175 | 0.960 +/- 0.006 | 0.325 +/- 0.002 | cuVS 2.95x slower |

Repeated-filter cuVS p50 improves by 97.9--111.0x over the uncached revision.
The 10% scenarios now favor cuVS, while the native scalar index remains faster
for 1% and 0.1% selectivity. Unfiltered p50 on the follow-up was 0.965 +/- 0.019
ms for cuVS and 8.643 +/- 0.151 ms for native, an approximately 9.0x ratio.

The cache addresses repeated filters but not first-use or high-cardinality
filters: a new predicate still costs approximately 119--141 ms at 100K records.
Reusing OpenViking's scalar-index candidate labels is still worth evaluating
for those cases. The cache's configured size is 16 prepared filters, and data
mutation clears it before rebuilding the dense index.

### Native bitmap bridge and auto-routing follow-up

The next follow-up removes that Python predicate scan. Each GPU rebuild now
registers its label order with the native engine once. A new filter is evaluated
by OpenViking's existing scalar/path index and projected through the registered
native-offset layout into a cuVS-row bitset. Repeated filters retain the device
bitset; mutations invalidate both the layout and filter cache.

Auto mode also caches a routing decision. The measured defaults send scalar
filters with at most 2,000 candidates to native vector recall, keep wider scalar
filters on cuVS, and use a lower 200-candidate threshold for URI filters. The
lower URI threshold avoids repeatedly paying native Trie/subtree-bitmap work for
medium-sized path scopes. Both thresholds are configurable and apply only to
auto mode; explicit `backend=cuvs` continues to use the GPU dense path.

The following values aggregate five independent processes over the same 100K x
768D, 50-query collection workload. The URI rows add unique hierarchical paths
whose prefixes select 10%, 1%, and 0.1% of records.

| Scenario | First query (ms) | Auto warm p50 (ms) | Route |
| --- | ---: | ---: | --- |
| Unfiltered | 1,902.930 +/- 6.045 | 0.972 +/- 0.015 | cuVS; first query includes lazy GPU build |
| Uniform scalar 10% | 2.984 +/- 0.034 | 1.119 +/- 0.029 | cuVS |
| Uniform scalar 1% | 2.096 +/- 0.019 | 0.643 +/- 0.018 | native |
| Uniform scalar 0.1% | 1.486 +/- 0.014 | 0.404 +/- 0.009 | native |
| Clustered scalar 10% | 1.853 +/- 0.012 | 1.133 +/- 0.018 | cuVS |
| Clustered scalar 1% | 1.994 +/- 0.061 | 0.578 +/- 0.006 | native |
| Clustered scalar 0.1% | 1.384 +/- 0.020 | 0.399 +/- 0.002 | native |
| URI path 10% | 12.860 +/- 0.569 | 1.126 +/- 0.024 | cuVS |
| URI path 1% | 3.087 +/- 0.031 | 1.020 +/- 0.027 | cuVS |
| URI path 0.1% | 1.635 +/- 0.014 | 0.455 +/- 0.010 | native |

For the six scalar rows, first-use latency is now 1.38--2.98 ms instead of the
previous 119--141 ms, a 47--86x reduction. Cached 10% filters retain the GPU
advantage, while 1% and 0.1% scalar filters recover the native candidate-pruning
advantage. URI first-use cost depends on subtree width: the 10% prefix still
spends about 12.9 ms traversing and unioning the native path bitmaps, but that
work is avoided after its cuVS bitset is cached. The 1% URI case remains on GPU
because repeatedly rebuilding its native path bitmap was slower than cached
cuVS search; only the 0.1% URI case crosses the more conservative path threshold.

These auto-mode rows could execute over either the native int8 representation
or the cuVS device representation, which was configured as float32 for these
runs. They are latency-routing results, not an equal-dtype comparison;
applications requiring one fixed numerical representation should select an
explicit backend or disable the native-routing thresholds.

### Dirty-index selective-first follow-up

Review of the auto-routing path found that the first implementation decided
`route_native` only after `_rebuild_if_needed()`. Results remained correct, but
a selective filtered query could still pay the full GPU rebuild before falling
back to native. Revision `d2a74c1af026da0a482955ed2498c03f5f44654c`
moves native filter resolution and the candidate-count decision before GPU
memory admission and rebuild.

A targeted follow-up runs a 0.1% scalar filter as the first query while the
100K x 768D auto index is dirty. Five independent clean processes produced:

| Metric | Median +/- MAD |
| --- | ---: |
| Selective-first query latency | 11.670 +/- 0.156 ms |
| Selective-first GPU-memory delta | 0 +/- 0 bytes |
| Subsequent unfiltered first query | 1,889.281 +/- 11.155 ms |
| Unfiltered query after a one-record mutation | 1,632.840 +/- 81.424 ms |

All five selective-first runs recorded exactly zero GPU-memory delta. The
following unfiltered query still paid the lazy build, demonstrating that the
native route neither allocated the GPU dataset nor accidentally cleared dirty
state. The 11.7 ms selective-first value is higher than a new filter on an
already-built index because it includes one-time registration of the current
100K-label native-to-cuVS layout. It nevertheless avoids roughly 1.9 seconds
of unnecessary GPU construction for this query shape.

### Ingestion, mutation, and restart

| Operation | Native median | cuVS median |
| --- | ---: | ---: |
| Ingest 100K records | 29.849 s | 38.545 s |
| First unfiltered query after ingest | 23.108 ms | 1,811.843 ms |
| Upsert 1: next query | 12.167 ms | 1,410.397 ms |
| Upsert 100: next query | 12.835 ms | 1,402.790 ms |
| Upsert 1K: next query | 13.915 ms | 1,438.549 ms |
| Upsert 10K: next query | 14.578 ms | 1,535.870 ms |
| Delete 100: next query | 13.574 ms | 1,405.846 ms |
| First query after close/reopen | 5.338 s | 15.315 s |
| Warm query after reopen | 10.551 ms | 1.118 ms |

Every cuVS mutation marks the index dirty, and the next query synchronously
rebuilds the full 100K-vector GPU index. The rebuild cost is therefore almost
independent of whether one or 10,000 records were updated. The write API itself
remains comparatively cheap; the cost is shifted onto the next reader. A
production integration should rebuild in the background, coalesce mutations,
and atomically swap the completed index while the prior snapshot continues to
serve queries.

Persistent collection loading is lazy, so the reopen time appears in the
first query rather than adapter construction. The cuVS first query includes
store recovery, rebuilding Python-side records, and building the GPU index.
After that work, the reopened cuVS collection returns to approximately 1.1 ms.

Median host RSS increase during ingestion was 0.93 GiB for native and 2.71 GiB
for cuVS; cuVS additionally used a 0.29 GiB GPU-memory delta after search. The
cuVS host overhead is dominated by the current per-vector Python tuple mirror.
These RSS deltas are directional rather than an allocator-isolated memory
benchmark because both backends run sequentially in each process.

## Async service-facade concurrency

This level calls `VikingVectorIndexBackend.query()` through OpenViking's normal
`asyncio.to_thread` boundary. It includes tenant-filter injection and async
request scheduling, but deliberately uses precomputed query vectors: HTTP,
authentication, embedding, reranking, and LLM work are still excluded.

- Harness revision: `e7af4e0c26c82eb58dbb47c07dcaa54b402fbaab`
- Dataset: 100,000 normalized Gaussian vectors, 768D; K=10
- Concurrency: 1, 4, 16, 32, and 64; default thread-pool limit 32
- Cached scenarios: 200 requests per concurrency level
- New-filter scenario: 32 requests per concurrency level, each with a distinct
  100-record range in addition to the automatic tenant filter
- Post-mutation scenario: one full-record upsert, followed by a simultaneous
  read burst whose size equals the concurrency level

Results aggregate five independent clean processes on H20. Values written with
`+/-` include median absolute deviation; the compact paired-latency tables show
process medians where dispersion is omitted. In these runs, the native
collection used its default int8 vector quantization and cuVS brute-force was
configured with `dtype=float32`.

### Cached tenant filter

Every service-facade request is automatically scoped by `account_id`; this
scenario warms and reuses that prepared filter.

| Concurrency | Native p50 (ms) | Native QPS | cuVS p50 (ms) | cuVS p99 (ms) | cuVS QPS | cuVS/native QPS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 14.136 +/- 0.066 | 71.3 +/- 2.3 | 1.492 +/- 0.011 | 2.175 +/- 0.030 | 614.8 +/- 8.2 | 8.63x |
| 4 | 16.311 +/- 0.177 | 238.0 +/- 1.7 | 6.094 +/- 0.027 | 9.912 +/- 0.176 | 632.6 +/- 5.1 | 2.66x |
| 16 | 16.504 +/- 0.240 | 907.8 +/- 25.1 | 24.820 +/- 0.084 | 29.885 +/- 1.351 | 636.0 +/- 3.9 | 0.70x |
| 32 | 30.050 +/- 1.618 | 982.7 +/- 21.8 | 47.998 +/- 0.585 | 54.204 +/- 0.330 | 651.8 +/- 6.8 | 0.66x |
| 64 | 54.251 +/- 1.059 | 1,056.8 +/- 4.6 | 92.641 +/- 0.531 | 99.232 +/- 1.133 | 677.2 +/- 7.0 | 0.64x |

cuVS keeps a strong low-concurrency advantage, but its throughput remains near
615--677 QPS as concurrency increases. Native CPU search scales across the
thread pool and crosses cuVS between concurrency 4 and 16. cuVS p50 rises from
1.5 ms to 92.6 ms at concurrency 64 because requests queue behind the current
index-wide lock; the measured revision did not yet use concurrent CUDA streams
or micro-batching.

### Cached 10% filter

| Concurrency | Native p50 (ms) | Native QPS | cuVS p50 (ms) | cuVS QPS | cuVS/native QPS |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2.452 +/- 0.011 | 385.5 +/- 3.5 | 1.564 +/- 0.006 | 582.7 +/- 10.5 | 1.51x |
| 4 | 2.937 +/- 0.038 | 1,165.6 +/- 14.7 | 6.473 +/- 0.091 | 591.6 +/- 5.4 | 0.51x |
| 16 | 12.860 +/- 0.227 | 1,151.7 +/- 7.0 | 27.400 +/- 0.257 | 576.0 +/- 5.7 | 0.50x |
| 64 | 48.681 +/- 0.502 | 1,215.1 +/- 19.6 | 103.497 +/- 0.930 | 604.3 +/- 4.1 | 0.50x |

The more selective native scalar path crosses cuVS between concurrency 1 and
4. Prepared-mask reuse removes predicate construction from this scenario, so
the remaining cuVS plateau is the search serialization and service overhead.

### New filter per request

| Concurrency | Native p50 / p99 (ms) | Native QPS | cuVS p50 / p99 (ms) | cuVS QPS |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.807 / 2.939 | 1,034.6 | 280.607 / 308.068 | 3.5 |
| 4 | 3.088 / 5.848 | 1,132.7 | 1,104.427 / 1,159.414 | 3.6 |
| 16 | 12.009 / 19.690 | 1,104.6 | 4,365.132 / 4,429.893 | 3.6 |
| 32 | 20.632 / 27.288 | 1,133.0 | 4,581.936 / 8,734.404 | 3.6 |

The native scalar index resolves each distinct 100-record range efficiently.
cuVS still scans all host-side records for every LRU miss, and the index lock
serializes those scans. At concurrency 32, half the requests complete around
4.6 seconds while the tail approaches 8.7 seconds. The concurrency-64 case is
not separately reported because this scenario has only 32 total requests.

### Read burst after one mutation

| Concurrency | Native p50 / p99 (ms) | cuVS p50 / p99 (ms) | cuVS burst wall time (s) |
| ---: | ---: | ---: | ---: |
| 1 | 21.467 / 21.467 | 1,827.384 / 1,827.384 | 1.828 +/- 0.170 |
| 4 | 23.436 / 23.860 | 1,563.884 / 1,565.954 | 1.566 +/- 0.008 |
| 16 | 24.621 / 28.031 | 1,701.266 / 1,711.689 | 1.712 +/- 0.118 |
| 32 | 36.285 / 40.417 | 1,612.954 / 1,633.418 | 1.635 +/- 0.046 |
| 64 | 49.734 / 69.858 | 1,849.853 / 1,894.171 | 1.896 +/- 0.022 |

The cuVS write call itself takes only approximately 3.0--3.6 ms; the next
reader rebuilds the full index while every concurrent reader waits on the same
lock. Higher apparent burst QPS is therefore not steady-state capacity: it is
the same rebuild delay amortized across more requests released together.

## Current conclusion and next checks

1. Native CPU exact remains preferable for very small collections. On this
   setup, cuVS GPU brute-force exact crosses over between 2K--5K vectors at
   768D and 1K--2K vectors at 1024D.
2. CAGRA requires recall-matched tuning; an approximate label alone does not
   imply better performance.
3. CAGRA can improve batched capacity around Recall@10=0.96, but this benefit
   required batching that the measured pre-microbatch revision did not expose.
4. Collection-level unfiltered lookup retained an approximately 9.0x warm-p50
   benefit at 100K x 768D in these runs, but native int8 versus the configured
   cuVS float32 device representation is not an equal-memory or equal-quality
   comparison.
5. Caching prepared filter bitsets improves repeated-filter cuVS p50 by
   98--111x. Repeated 10% filters now favor cuVS, while native remains
   1.9--3.0x faster at 1% and 0.1% selectivity. A new filter still pays a
   119--141 ms host scan, so scalar-index candidate reuse remains relevant.
6. Synchronous lazy rebuild shifts approximately 1.4--1.7 seconds onto the
   first reader after any mutation at 100K x 768D. Background snapshot rebuild
   and atomic swap should be evaluated before enabling cuVS for write-active
   collections.
7. Independent process results are stable for this hardware and dataset, but
   cross-node and cross-day variance are not yet measured.
8. GloVe-100 and normalized Gaussian vectors are engineering datasets, not a
   representative agent-memory corpus.
9. At the async service facade, cuVS wins cached tenant search through
   concurrency 4, but native crosses it by concurrency 16. On the measured
   revision, the cuVS lock holds throughput near 600--680 QPS and turns
   concurrency into queueing latency rather than GPU parallelism.
10. For this historical checkpoint, the next engineering step was to evaluate
    a read-safe immutable snapshot with concurrent searches or micro-batching,
    plus background rebuild. A later end-to-end matrix should then add HTTP,
    embedding, reranking, and a real agent-memory workload.
