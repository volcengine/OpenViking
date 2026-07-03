# Preliminary cuVS index results

These results are an engineering checkpoint for the OpenViking cuVS
integration. They are not a final performance claim. The measurements use one
GPU, one public ANN dataset, and deterministic synthetic vectors for exact
high-dimensional scaling. Every reported row aggregates five independent
processes as median +/- median absolute deviation (MAD).

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

## Batch size 1

This is the closest index-level approximation of the current OpenViking
single-query integration. It includes Python dispatch, host-to-device query
copy, GPU execution, and result copy back to host. It excludes embedding,
HTTP, record lookup, reranking, and LLM work.

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

This comparison deliberately uses the normal collection defaults. The native
flat index uses its default int8 quantization, while cuVS brute-force retains
float32 vectors. Consequently, the native path is not the float exact baseline
from the index microbenchmark. Its Recall@10 against cuVS GPU brute-force was
0.982 without a filter and 0.978--0.994 with filters. This is both a quality
and memory semantic that the integration must make explicit.

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

## Current conclusion and next checks

1. Native CPU exact remains preferable for very small collections. On this
   setup, cuVS GPU brute-force exact crosses over between 2K--5K vectors at
   768D and 1K--2K vectors at 1024D.
2. CAGRA requires recall-matched tuning; an approximate label alone does not
   imply better performance.
3. CAGRA can improve batched capacity around Recall@10=0.96, but this benefit
   requires batching that the current OpenViking integration does not expose.
4. Collection-level unfiltered lookup retains an approximately 9.0x warm-p50
   benefit at 100K x 768D, but native int8 versus cuVS float32 is not an
   equal-memory or equal-quality comparison.
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
   representative agent-memory corpus. The next matrix should cover server
   concurrency and a real agent-memory embedding workload while separately
   tracking new versus cached filters and post-mutation rebuilds.
