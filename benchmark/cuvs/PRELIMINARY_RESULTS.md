# Preliminary cuVS index results

These results are an engineering checkpoint for the OpenViking cuVS
integration. They are not a final performance claim. The initial measurements
use one GPU and one public dataset. The final GPU measurements repeat the
search workload five times in one process; independent process-level runs and
variance are still pending.

## Setup

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
results.

## Batch size 1

This is the closest index-level approximation of the current OpenViking
single-query integration. It includes Python dispatch, host-to-device query
copy, GPU execution, and result copy back to host. It excludes embedding,
HTTP, record lookup, reranking, and LLM work.

| Backend | Recall@10 | p50 (ms/query) | p95 (ms/query) | QPS |
| --- | ---: | ---: | ---: | ---: |
| OpenViking native exact | 1.0000 | 40.022 | 40.607 | 24.8 |
| cuVS GPU brute-force exact | 1.0000 | 0.797 | 0.816 | 1,252.1 |
| cuVS CAGRA ANN, `itopk_size=512` | 0.9630 | 1.694 | 1.914 | 575.9 |
| cuVS CAGRA ANN, `itopk_size=2048` | 0.9944 | 1.739 | 1.963 | 561.7 |

In this low-batch regime, cuVS GPU brute-force exact has about 50x lower warm
p50 latency and about 50x higher QPS than the current native CPU exact call
path. CAGRA is slower than GPU brute-force exact even before requiring 0.99
recall, so CAGRA should not be selected for this dataset and query shape solely
because it is approximate.

## Batch size 128

This measures vector-index throughput capacity. It is not current OpenViking
server throughput because the integration currently submits one query at a
time.

| Backend | Recall@10 | QPS | Relative to GPU exact |
| --- | ---: | ---: | ---: |
| cuVS GPU brute-force exact | 1.0000 | 35,775 | 1.00x |
| cuVS CAGRA ANN, `itopk_size=512` | 0.9624 | 44,464 | 1.24x |
| cuVS CAGRA ANN, `itopk_size=2048` | 0.9946 | 21,962 | 0.61x |

CAGRA shows a throughput benefit only at the lower recall point in this
initial run. At approximately 0.99 recall, GPU exact remains faster.

## Build scope

| Backend | Build time (s) | Scope |
| --- | ---: | --- |
| OpenViking native exact | 10.690 | Python `DeltaRecord` creation plus native upsert |
| cuVS GPU brute-force exact | 0.224 | host-to-device matrix copy plus index wrapper |
| cuVS CAGRA high-recall graph | 4.301 | matrix copy plus graph construction |

These build times do not isolate equivalent kernels. In particular, the native
path includes the current row-oriented OpenViking ingestion interface while
the cuVS paths accept the full matrix. They should be treated as integration
costs, not as a pure CPU-versus-GPU algorithm comparison.

## Current conclusion and next checks

1. cuVS GPU brute-force exact is the strongest default candidate for batch=1
   at this scale.
2. CAGRA requires recall-matched tuning; an approximate label alone does not
   imply better performance.
3. CAGRA can improve batched capacity around Recall@10=0.96, but this benefit
   requires batching that the current OpenViking integration does not expose.
4. The native result has one timed search repetition, while the final GPU
   result has five. Independent process-level runs are still required before
   publishing variance or stronger speedup claims.
5. GloVe-100 is a public ANN sanity dataset, not a representative agent-memory
   embedding corpus. A later matrix must include 768D/1024D embeddings,
   collection-level lookup, filters, lazy rebuild, and server-level latency.
