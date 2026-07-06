# NVIDIA cuVS dense search backend

The `cuvs` backend keeps OpenViking's embedded record store, scalar indexes, sparse retrieval, and recovery logic, while executing dense vector search with NVIDIA cuVS. cuVS 26.06 Python wheels require Python 3.11 or newer.

Install the package matching the host CUDA major version:

```bash
# CUDA 12
pip install -e .
pip install cuvs-cu12 'cupy-cuda12x[ctk]' --extra-index-url=https://pypi.nvidia.com

# CUDA 13
pip install -e .
pip install cuvs-cu13 'cupy-cuda13x[ctk]' --extra-index-url=https://pypi.nvidia.com
```

Start with exact brute-force search:

```json
{
  "storage": {
    "workspace": "/data/openviking",
    "vectordb": {
      "backend": "cuvs",
      "distance_metric": "cosine",
      "cuvs": {
        "algorithm": "brute_force",
        "fallback_to_native": true,
        "filter_cache_size": 16
      }
    }
  }
}
```

Set `algorithm` to `cagra` for approximate graph search. `build_params` and `search_params` are passed to cuVS `cagra.IndexParams` and `cagra.SearchParams` respectively.

### Memory-aware auto mode

Keep `backend` set to `local` and enable `cuvs.auto_enable` to use otherwise
idle GPU memory without changing the default behavior for other installations:

```json
{
  "storage": {
    "vectordb": {
      "backend": "local",
      "cuvs": {
        "auto_enable": true,
        "algorithm": "brute_force",
        "auto_memory_reserve_mb": 1024,
        "auto_memory_safety_factor": 2.0
      }
    }
  }
}
```

Before each lazy build or rebuild, auto mode reads free device memory and
estimates the float32 vector payload, CAGRA graph and intermediate graph when
applicable, and the configured filter-bitset cache. It multiplies those known
allocations by `auto_memory_safety_factor` and then preserves
`auto_memory_reserve_mb`. If the estimate does not fit, or cuVS/GPU discovery
is unavailable, that query uses the unchanged native index. The cuVS index
remains dirty so a later query can retry after GPU memory becomes available.
An allocation failure after admission also falls back to native. Explicit
`backend: "cuvs"` retains fail-fast behavior and does not use this gate.
This switch is memory-aware, not yet latency-aware: a highly selective scalar
filter can still be faster on the native path because the native scalar index
shrinks the distance candidate set, while cached cuVS brute-force remains near
a full-matrix scan. Adaptive per-query routing is a separate follow-up.

## GPU memory footprint

The current GPU shadow is float32, so brute-force's dominant retained payload
is `N * dimension * 4` bytes. CAGRA additionally retains approximately
`N * graph_degree * 4` bytes for the graph and can require an intermediate
`N * intermediate_graph_degree * 4` bytes while building. Each cached filter
bitset costs approximately `ceil(N / 32) * 4` bytes.

Prior index-only runs measured the following `cudaMemGetInfo` deltas from just
before to just after build; each value is the median of five clean processes:

| Dataset | cuVS algorithm | Measured GPU delta |
| --- | --- | ---: |
| 100K x 768D | brute-force | 294 MiB |
| 1M x 768D | brute-force | 2.9 GiB |
| 100K x 1024D | brute-force | 392 MiB |
| 1M x 1024D | brute-force | 3.9 GiB |
| 1,183,514 x 100D | brute-force | 452 MiB |
| 1,183,514 x 100D | CAGRA | 872 MiB |

These are retained-build deltas rather than sampled peak VRAM. Allocator
state, cuVS version, CAGRA parameters, query batch size, and concurrent GPU
workloads can increase the peak. The delta also excludes the approximately
327 MiB CUDA runtime/context baseline observed before build in these processes.
This is why auto mode initializes the runtime first, reads the remaining free
memory, and then applies a conservative safety factor and independent reserve
rather than admitting from the vector payload alone.

## Data type and native-index behavior

Enabling cuVS does not change OpenViking's default backend or rewrite the
native CPU index. The normal collection metadata remains
`VectorIndex.Quant=int8`, so native fallback searches keep the existing
per-vector-scale int8 quantization. In parallel, the current cuVS runtime keeps
its GPU shadow in float32 because the cuVS Python brute-force API accepts
float32/float16 rather than OpenViking's scaled int8 record format.

The two dense paths therefore do not have equal memory or numerical semantics:
native results are exact within the quantized CPU representation, while cuVS
brute-force is exact over the retained float32 vectors. Small score or neighbor
ordering differences are expected. Benchmarks must report the two data types
and include Recall@K instead of presenting the comparison as equal-dtype or
equal-memory. This separation is intentional for the initial opt-in
integration and leaves existing CPU behavior unchanged.

Lower-precision GPU storage is a follow-up rather than an implicit cast. The
first candidate is configurable float16 for both the cuVS dataset and queries,
with Recall@K measured against float32. Native-compatible int8 requires a
separate design because OpenViking uses a per-vector scale, while cuVS
brute-force does not accept that scaled-int8 representation. CAGRA int8 or PQ
compression must likewise be evaluated as approximate modes with an explicit
recall/latency/memory frontier.

The initial integration rebuilds the GPU index lazily after an upsert or delete. Common scalar filters are translated to a cuVS bitset prefilter. `filter_cache_size` retains recently reused bitsets on the GPU and invalidates them on mutation; the first use of a new filter still evaluates the predicate against the host-side records. Sparse/hybrid queries and unsupported type-sensitive filters fall back to OpenViking's native local index when `fallback_to_native` is enabled. The canonical vectors remain in the local store and repopulate cuVS after restart.

The `[ctk]` CuPy extra installs the CUDA toolkit headers required by the cuVS
Python interop path, even when the host provides a CUDA driver but no toolkit.

After installation, run `python examples/cuvs_smoke.py` for an exact
GPU-backed write and filtered-search check, or
`python examples/cuvs_smoke.py --algorithm cagra` to exercise the graph index.
Neither command requires an embedding or VLM service.
