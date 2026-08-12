# openGauss Vector Backend

OpenViking integrates openGauss DataVec through the VectorDB Adapter. Upper-layer `find/search` calls remain unchanged; SQL, index lifecycle, pooling, and distributed behavior stay inside the adapter.

## Installation

```bash
pip install "openviking[opengauss]"
```

## First-party configuration

Configure indexes only under `storage.vectordb.opengauss` with `index_type + build_params + search_params`. `custom_params` is not the first-party openGauss configuration path.

```json
{
  "storage": {
    "vectordb": {
      "backend": "opengauss",
      "name": "context",
      "dimension": 512,
      "distance_metric": "cosine",
      "opengauss": {
        "host": "127.0.0.1",
        "port": 5432,
        "user": "gaussdb",
        "password": "replace-me",
        "db_name": "openviking",
        "mode": "standalone",
        "shard_count": 32,
        "index_type": "hnsw-pq",
        "build_params": {"m": 16, "ef_construction": 64, "pq_m": 8, "pq_ksub": 256},
        "search_params": {"ef_search": 100},
        "parallel_workers": 4,
        "maintenance_work_mem_mb": 128,
        "connection_pool_min_size": 1,
        "connection_pool_max_size": 8
      }
    }
  }
}
```

Supported logical types are `hnsw`, `hnsw-pq`, `hnsw-rabitq`, `ivfflat`, `ivf-pq`, `ivf-rabitq`, and `diskann`. Quantized variants use the base `hnsw` or `ivfflat` access method with `enable_pq=on` or `enable_rabitq=on`. DiskANN-PQ is not supported; `diskann` configurations cannot set `enable_pq` or `pq_m`.

## Build parameters

- **HNSW:** `m` 2–100; `ef_construction` 4–1000 and at least `2*m`.
- **IVFFlat:** `lists` 1–32768.
- **DiskANN:** `index_size` 16–1000.
- **PQ:** HNSW/IVF support `enable_pq`, `pq_m`, and `pq_ksub`; `dimension % pq_m == 0`. `pq_m` is 1–2000 and `pq_ksub` is 1–256. IVF-PQ also supports `by_residual`.
- **RabitQ:** `enable_rabitq`, `rabitq_refine_type` (`none`, `SQ8`, `FP32`), and `rabitq_fht`. PQ and RabitQ are mutually exclusive. DiskANN does not support RabitQ.
- **Parallel build:** top-level `parallel_workers` is 0–32.
- **Build memory:** top-level `maintenance_work_mem_mb` defaults to 64 MiB and accepts 16–1048576. The adapter applies `SET LOCAL maintenance_work_mem` only in the ANN `CREATE INDEX` transaction, without changing the database-wide setting. Increase it when IVFFlat, PQ, or larger datasets report insufficient index-build memory.

Unknown parameters and invalid combinations fail configuration validation.

## Search parameters

- HNSW: `ef_search` and `earlystop_threshold` map to `hnsw_ef_search` and `hnsw_earlystop_threshold`.
- IVF: `probes` maps to `ivfflat_probes`; IVF-PQ also supports `ivfpq_kreorder`.
- DiskANN: `probes` maps to `diskann_probes`.
- RabitQ: `rbq_query_bits` and `rbq_refinek` map to the same-named GUCs.

The adapter applies these with `SET LOCAL` in the search transaction. In distributed mode it first executes `SET LOCAL spq.propagate_set_commands = 'local'` in the same transaction, because spq defaults to `none` and would otherwise leave DN shard scans on server-default parameters.

## Lifecycle and consistency

Plain HNSW may be created on an empty table. IVFFlat, PQ, RabitQ, and DiskANN are built after data exists; a `bulk_ingest` scope builds once when the outer scope ends. Physical access method, operator class, and options are verified through the catalog before `_ov_index_*` metadata is persisted. Invalid historical metadata is removed. Search fails if the configured physical ANN index is missing or mismatched.

## Distributed mode

Set `mode=distributed` and point `host/port` to an spq CN. Startup verifies the spq extension, distribution functions, active DN workers, all-node connectivity, and catalog state in `pg_dist_partition`. Collection tables are hash distributed by `id`. Standard spqplugin_v2 hash-distributes metadata by `table_name` and `index_name`; a Citus-compatible CN may use reference tables when it explicitly provides `create_reference_table`.

The official openGauss distributed-vector example guarantees plain HNSW, and that is the only ANN type enabled by the adapter in `mode=distributed`. Real CN + 2 DN acceptance confirmed a DN `Ann Index Scan` for plain HNSW. In the same cluster, HNSW-PQ and IVF-PQ failed while SPQ attempted to build on the zero-row CN logical table; HNSW-RabitQ, IVFFlat, and IVF-RabitQ produced DN sequential scans instead of ANN scans. The adapter therefore rejects every distributed index type except plain HNSW before connecting or creating tables. Use standalone mode for PQ, RabitQ, IVF, or DiskANN indexes.

## Verification

Inspect both `pg_indexes.indexdef` and `EXPLAIN`. A real ANN verification requires an `Ann Index Scan` using the intended physical index; recall results alone do not prove index use.
