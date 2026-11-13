# pgvector directory filter indexes

pgvector directory filtering combines tenant predicates with normalized URI
scopes. In a million-row benchmark, the ordinary `uri` index did not serve
these scope predicates: a directory matching about a hundred rows still
required scanning the tenant's candidates first. A matching composite B-tree
expression index substantially reduced narrow and empty-directory scans.
Scopes covering an entire tenant saw limited improvement.

This guide records the candidate index and measured evidence. **The backend
does not automatically create this expression index.** It was tested only on
isolated benchmark collections and removed with their tables. See
[PostgreSQL + pgvector](25-pgvector.md) for setup and capability boundaries.

## How directory filtering works

`PathScope("uri", "viking://resources/g00/d000", depth)` on a path field becomes
a `/resources/g00/d000` scope, compiled to parameterized SQL alongside
`account_id`, `level`, ACL and other predicates. `level=0/1/2` represents the
L0 abstract, L1 overview and L2 content respectively. Level and path depth
are independent predicates.

| `depth` | Scope |
| --- | --- |
| Less than 0 | The node and all descendants |
| Equal to 0 | The node's exact path |
| Greater than 0 | The node and descendants with at most `depth` relative path segments |

The query applies this SQL expression to stored `uri` values:

```sql
CASE
    WHEN uri IS NULL THEN NULL
    WHEN btrim(uri) = ''
      OR rtrim(btrim(uri), '/') = '' THEN '/'
    ELSE rtrim(btrim(uri), '/')
END
```

Below, `N(uri)` abbreviates that expression; it is not a new column or
database function. An unbounded scope can be summarized as:

```text
account_id = :account_id
AND (N(uri) = :path OR N(uri) LIKE :child_pattern ESCAPE '\')
AND level = :level
```

For `/resources/g00/d000`, the child pattern is `/resources/g00/d000/%`.
Literal `%`, `_` and `\` in the path are escaped first; the final `/` preserves
the directory boundary. Positive-depth queries also count relative segments
in matching paths.

See
[`_sql_normalized_path` and `_build_path_scope_clause`](https://github.com/volcengine/OpenViking/blob/eaf44199aacfb601f059202bd8d04246e5248f2e/openviking/storage/vectordb_adapters/opengauss/sql.py#L191-L224).
pgvector reuses this filter compiler.

## Why the ordinary URI index was insufficient

An ordinary B-tree keys the original `uri`; these directory predicates compare
a `CASE/btrim/rtrim` expression instead. The observed plan used the
`account_id` index, then evaluated the path condition on the retrieved tenant
rows.

In the million-row table, a directory matched 100 rows, but the tenant index
produced 100,000 candidates. The parallel Bitmap Heap Scan removed about
99,900 rows through path filtering. Interleaved tenants and `work_mem=4MB`
also resulted in a lossy bitmap and additional index-condition rechecks.
Filter-only `count(*)` had a p50 of 141.11 ms, while flat Top-10 took
141.42 ms: scanning and filtering dominated this case, rather than computing
distances for the final 100 rows.

For `level=0/1`, a BitmapAnd over the existing `level` and `account_id`
indexes reduced candidates to 1,000 rows in this fixture. `level=2`
represented 98% of the data and was insufficiently selective to solve the
scan problem. Benefits depend on layer proportions and tenant layout.

## Composite expression index

Use `account_id` first, followed by the query's normalized URI expression
with `text_pattern_ops`:

```sql
-- Replace public."context" with the actual collection table,
-- and choose a distinct index name for each table.
CREATE INDEX context_account_uri_scope_idx
ON public."context" (
    account_id,
    (CASE
        WHEN uri IS NULL THEN NULL
        WHEN btrim(uri) = ''
          OR rtrim(btrim(uri), '/') = '' THEN '/'
        ELSE rtrim(btrim(uri), '/')
    END) text_pattern_ops
);

ANALYZE public."context";
```

`account_id` restricts the tenant. The expression key matches the query, and
`text_pattern_ops` supports these prefix `LIKE` ranges and equality.
The index can establish that an empty directory has no candidates without
walking the whole tenant. PostgreSQL still chooses plans according to
statistics and cost.

The index preserves path, tenant and ACL semantics. It does not separately
optimize the relative-segment calculation for positive depth. It is a scalar
B-tree; vector distances still use flat or HNSW retrieval. Flat queries set
`enable_indexscan=off` to preserve exact search, but Bitmap Scan remains
available and can use this index.

For the same million-row query matching 100 rows, key plan nodes became:

```text
Bitmap Heap Scan
  Actual Rows: 100
  Exact Heap Blocks: 77
  Rows Removed by Filter: 0
  BitmapOr
    Bitmap Index Scan on the scope index: 2 rows (exact path)
    Bitmap Index Scan on the scope index: 98 rows (child prefix)
```

The 2 exact-path rows are L0/L1, and the 98 descendants are L2 files.
This excerpt omits full predicates and distance sorting. Adding `level=2`
restricts candidates to that layer.

## Benchmark method

Tests ran on **2026-11-13**, using code revision
`eaf44199aacfb601f059202bd8d04246e5248f2e`.
The real `collection adapter.query` path queried PostgreSQL. Timings include
the pool, SQL and result reads, but exclude HTTP, embedding generation and
the complete recursive retrieval workflow.

| Item | Setting |
| --- | --- |
| Host | macOS arm64, 18 logical CPUs, 48 GiB RAM |
| Database | PostgreSQL 17.6, pgvector 0.8.7, local loopback |
| PostgreSQL | `shared_buffers=128MB`, `work_mem=4MB`, `max_parallel_workers_per_gather=2`, `lc_collate=C` |
| Pool | `min_size=1`, `max_size=4` |
| Vectors | Random unit vectors, 128 dimensions, cosine distance; a separate 20,000-row, 4096-dimensional flat test |
| Size | 100,000-row baseline and 1,000,000-row main test |
| Million-row layout | 10 interleaved tenants, 10 groups per tenant, 100 directories per group, 100 rows per directory |
| Layers | One L0 and one L1 row per directory, 98 L2 rows; half of the L2 files are under `sub` |
| Queries | Tenant `a0`, Top-10; scope, depth and level tested separately |
| Samples | Five warmups per condition; 100 samples for the 100k flat baseline and level supplement, 200 for other conditions |
| Concurrency | Closed-loop client threads, 1 or 4 |

The million-row fixture contains 10,000 `(account_id, directory path)`
combinations. Binary COPY loaded synthetic data; this was not a production
traffic or write-throughput benchmark. Current and expression-index phases
ran sequentially, without controlling the OS page cache.
The 20 query vectors use the first data batch's random seed (base seed
`20261113`), corresponding to its first 20 rows; 2 belong to tenant `a0`.
Recall describes only this small set of corpus-derived queries.

There were 98 conditions and 14,800 measured requests. Condition summaries
are available in the [main CSV](/benchmarks/pgvector-directory-filtering-20261113/measurements.csv)
and [level supplement CSV](/benchmarks/pgvector-directory-filtering-20261113/levels-measurements.csv).
The CSVs retain conditions, samples, matches, latency percentiles, QPS and
measured recall; they do not contain per-request result IDs or full plans.

### Million rows: OV level predicates

All results below are single-client flat Top-10 in ms. The level supplement
rebuilt a million-row fixture with the same distribution and measured
100 requests per condition.

| Level | Scope | Matches | Current p50 | Current p99 | Expression-index p50 | Expression-index p99 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | Directory subtree | 98 | 144.28 | 197.60 | 0.48 | 0.95 |
| 2 | Empty directory | 0 | 139.95 | 174.44 | 0.38 | 0.82 |
| 2 | Tenant root | 98,000 | 145.01 | 252.04 | 143.62 | 209.90 |
| 0 | Directory subtree | 1 | 7.29 | 9.18 | 0.60 | 1.21 |
| 1 | Directory subtree | 1 | 6.98 | 9.53 | 0.50 | 0.93 |

The L2 directory's p50 improved by about 300 times. The tenant root still
processes about a hundred thousand rows, so path-index benefits are limited.
L0/L1 already benefit from layer indexes, but the path index narrows their
candidates further.

### Million rows: path depth and concurrency

These queries have no additional level predicate: the directory includes
2 summaries and 98 files. All values are single-client flat Top-10 in ms.

| Scope | Matches | Current p50 | Current p99 | Expression-index p50 | Expression-index p99 |
| --- | --- | --- | --- | --- | --- |
| Tenant root | 100,000 | 155.16 | 214.50 | 139.60 | 192.18 |
| Group subtree | 10,000 | 140.67 | 167.60 | 11.77 | 12.45 |
| Directory subtree | 100 | 141.42 | 181.79 | 0.46 | 0.65 |
| `depth=1` | 51 | 143.07 | 176.39 | 0.55 | 0.92 |
| `depth=0` | 2 | 132.26 | 162.81 | 0.34 | 0.49 |
| Empty directory | 0 | 137.67 | 168.53 | 0.36 | 0.65 |

For the directory with concurrency 4, QPS increased from 13.6 to 3,524.9,
and p99 fell from 456.80 ms to 10.11 ms. These results include pool waits and
creation/release of extra connections; they are not pure SQL throughput over
four permanently retained connections.

### 100k rows: HNSW post-filtering

HNSW used `m=16`, `ef_construction=64`, `ef_search=100`,
`iterative_scan=strict_order`, and the database's default scan limit.
Single-client recall used flat results from the same data and 20 query vectors
as ground truth, with denominator `min(10, matches)`.

| Scope | Matches | Flat p50 ms | HNSW-configured p50 ms | Mean Recall@10 |
| --- | --- | --- | --- | --- |
| Tenant root | 10,000 | 12.43 | 3.41 | 52.5% |
| Directory subtree | 100 | 10.75 | 18.93 | 67.0% |
| Empty directory | 0 | 10.54 | 29.81 | Not applicable |

These three scopes used HNSW Index Scan. A directory plan filtered out
6,155 candidates before returning 10 rows; the empty-directory plan filtered
19,916. HNSW configuration does not force every query through ANN:
`depth=0/1` used scalar Bitmap Scan plus exact sorting, so their 100% recall
cannot be attributed to HNSW.

The test did not evaluate expression indexes combined with HNSW or tune
HNSW parameters. It does not establish that HNSW is generally unsuitable
for directory filtering, or that returning Top-10 guarantees high recall.

### The 4096-dimensional boundary

The 4096-dimensional test used only 20,000 rows and flat retrieval.
These single-client queries had no level predicate:

| Scope | Matches | Current p50 ms | Expression-index p50 ms |
| --- | --- | --- | --- |
| Tenant root | 2,000 | 13.13 | 14.13 |
| Directory subtree | 100 | 5.40 | 4.19 |
| Empty directory | 0 | 4.77 | 3.44 |

URI scans, vector processing and TOAST costs differ from the million-row
128-dimensional case. The adapter's `vector` HNSW limit is 2000 dimensions,
so 4096-dimensional retrieval uses flat. Do not extrapolate 128-dimensional
speedups or ANN results to a million-row, 4096-dimensional workload.

## Validation and rollout

Add the candidate index and run `ANALYZE` on representative data, then prefix
actual adapter SQL with `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`.
Record matches, scanned/rechecked rows, heap blocks, p50/p95/p99, throughput
and ANN recall. Narrow scopes should be able to use the scope index to reduce
candidates; tenant-wide scopes may continue using the account index or other
scan paths.

An index name does not validate its definition. Check collection fields and
the expression before deployment, rather than hiding a conflicting definition
with `IF NOT EXISTS`. For online builds, evaluate `CREATE INDEX CONCURRENTLY`,
which runs outside transaction blocks, and verify that the resulting index is
valid. The benchmark used ordinary `CREATE INDEX` after data loading.
Additional disk usage and write-maintenance cost were not separately measured.

Example rollback for the manual index:

```sql
DROP INDEX public.context_account_uri_scope_idx;
```

Future automation can share the SQL expression builder and handle new
collections, existing-collection migrations and physical index-definition
checks separately. Validate trailing slashes, empty paths, LIKE metacharacters,
depth, ACL predicates, directory skew, concurrent writes and different memory
settings. Choosing exact or ANN retrieval by candidate size is a future
option; the current backend has no automatic switching policy.

This was a warmed, local synthetic benchmark with limited p99 samples.
It did not cover realistic ACL complexity, the full service path, sustained
loads, cold caches, clustered tenant rows or partitioning. Results establish
an index direction, not production latency or capacity guarantees.
