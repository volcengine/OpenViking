# PostgreSQL PathLock

[中文使用说明与文件改动清单](postgresql-pathlock_CN.md)

The public documentation site includes the [PostgreSQL PathLock setup guide](../en/guides/25-postgresql-pathlock.md).

PostgreSQL provides shared path locks for cooperating OpenViking processes.
The provider reuses SQLite's holder protocol: Exact/Tree conflicts, reentrancy,
holder-level renewal/release, upgrades, and handoff validation. No Redis server
or PostgreSQL extension is required.

## Configuration

Keep the connection string in the server environment:

```sh
export OV_POSTGRES_DSN='host=pg.example.com port=5432 dbname=openviking user=openviking password=... sslmode=require'
```

```json
{
  "service_name": "community-workspace",
  "storage": {
    "agfs": {
      "pathlock": {
        "provider": "postgres",
        "postgres_dsn_env": "OV_POSTGRES_DSN",
        "lock_expire_secs": 30
      }
    }
  }
}
```

Participants protecting the same files must use the same writable database
primary, service name, provider, and expiry duration. Service names separate lock
namespaces. Restart processes after rotating credentials; the DSN is resolved
at initialization. Both URLs and key/value connection strings are accepted.
For remote connections use `sslmode=require`: native TLS verifies the certificate
and hostname using the system trust store. Use `sslmode=disable` only for local
disposable tests.

The Python binding includes PostgreSQL support; rebuild it before selecting this
provider. Rust consumers enable `ragfs/postgres` (also included by `ragfs/full`).

PostgreSQL 13 or later with UTF8 server encoding is required (`gen_random_uuid()` is built in).
Initialization creates `public.ov_pathlock_tokens`, `public.path_prefix_2048(text)`
and its lookup/expiry/subtree indexes. The role
needs CREATE on public plus SELECT/INSERT/UPDATE/DELETE on the table. These
objects may be provisioned by a migration role in advance; initialization still
executes `CREATE ... IF NOT EXISTS`; the helper is created only when absent.
A pre-provisioned helper must use the definition below. Runtime roles need
EXECUTE on it; initialization does not replace a migration role's function.

```sql
CREATE FUNCTION public.path_prefix_2048(text) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS 'SELECT pg_catalog.substring(pg_catalog.convert_to($1, ''UTF8''), 1, 2048)';
CREATE INDEX ov_pathlock_subtree_prefix_2048
ON public.ov_pathlock_tokens(md5(namespace), public.path_prefix_2048(path));
```

The helper returns at most **2048 bytes**, not characters. Its UTF8-to-UTF8
conversion is deterministic in a UTF8 database and does not follow
`client_encoding`. A truncated prefix may end inside a Unicode character;
it remains bytea and is never decoded. The bounded index fits standard
8 KiB PostgreSQL B-tree pages even for incompressible paths.

The primary key is a generated UUID. Non-unique indexes contain only
`md5(namespace), md5(path)` for exact lookups and `md5(namespace), time_ns`
for expiry. Complete namespaces and paths stay in TEXT columns and are always
compared after hash filtering, so hash collisions cannot overwrite or alias
another lock. Writes use update-then-insert under the namespace advisory lock.

When upgrading from the `(namespace,path)` primary key, stop all old provider
instances first. Initialization transactionally assigns UUIDs to existing rows,
replaces the primary key and expiry index, and preserves lease tokens. The
initializing role must own the table for this migration, or a migration role
must provision the new schema before restarting runtime instances. Old binaries
cannot write the new schema; this is not a rolling upgrade. Migration failures,
including DDL timeouts, roll back and leave the old schema and records intact.

## Semantics

Each operation runs in a transaction with a namespace advisory lock. This
serializes checks against absent rows, ancestors, and subtrees across processes.
Batches commit in full or roll back, including stale-record cleanup. C collation
preserves path segment boundaries and descendant ranges. Full paths are not
B-tree index keys, allowing poorly compressible long logical paths.
Subtree queries use namespace hash plus binary prefix range bounds, then compare
full namespace and C-collated paths. The prefix upper bound is inclusive:
truncation can make both bounds equal. Full-path comparison retains the exclusive
upper bound and excludes the root. Paths sharing their first 2048 bytes can
still require many candidate checks. Benchmark these distributions before use.

Local release measurements on PostgreSQL 17.6 (warm cache, 200 samples per
serial case): at 100k active locks plus 100k other-namespace locks, empty-subtree
SQL P50 changed from 7.122 ms with hash-only indexes to 0.227 ms with the prefix
index; Tree acquisition P99 changed from 12.681 to 5.692 ms.
For 10k long paths sharing their first 2048 bytes, a deep near-prefix miss
still checked all 10k rows and SQL P50 increased from 15.424 to 23.750 ms.
These are separate local runs, not production latency guarantees. The additional
index's ongoing write and space costs have not been benchmarked.

Existing UUID/hash schemas acquire the additional subtree index at initialization,
without changing lease records. Index creation is transactional and subject to the
DDL timeout; provision it separately before startup if a large table needs longer.

Acquisition, renewal, expiry checks, and handoff rollback use the primary's
`clock_timestamp()`. The client supplies the lease duration. Expired holders
cannot renew; after takeover an old owner cannot renew or release the new lock.
The manager continues to maintain local lease lifetime and renewal scheduling.
The provider implements the existing `replace_holder` hook using the configured
expiry duration and database clock, preserving concurrent holder renewals.

The raw `try_create_token`, `compare_and_write_token`, and legacy `refresh_token`
methods also use the primary's clock. Creation rebases every holder; CAS rebases
new or renewed holders while preserving unchanged timestamps within the same
owner, including kind-only changes and legacy-reference conversion. Changing
owners rebases all references. CAS compares the original snapshot before
normalization; callers must reread the persisted token for subsequent comparisons.

One connection per provider executes on Tokio blocking workers. Connect timeout
is 5 seconds, statement timeout 5 seconds, and SQL lock timeout 1 second. Database
contention returns Busy, distinct from a path conflict. These bound server waits,
not the total request during network blackholes or blocking-worker saturation.

Failed operations discard the connection; later operations reconnect. Mutations
are never automatically replayed after a database/commit error because a lost
commit response can leave the outcome unknown. Orphaned leases expire normally.

## Limits

This first version serializes operations within each namespace. Benchmark
acquisition and renewal at the target concurrency before high-throughput use.
Hash collisions only add contention between unrelated namespaces.

This provider coordinates leases; it does not fence local-filesystem or S3 writes
that continue after lease loss. SQLFS transactions or storage-side version/fencing
checks are separate work, as are PostgreSQL FS and pgvector.

Use one writable primary, never read replicas. Failover must prevent split brain;
asynchronous replication can lose committed lock records. HA failover safety and
throughput are not certified by this implementation. All lock table writers must
follow this provider's transaction protocol.

## Tests

Use a disposable database. Tests create unique namespaces and normally remove
tokens; failures can leave test rows. The connection-loss test requires permission
to terminate the role's own connections and uses an unencrypted local test DSN.

```sh
export OV_TEST_PG_DSN='host=127.0.0.1 port=5432 dbname=postgres user=test sslmode=disable'
cargo test -p ragfs --features postgres --test postgres_pathlock -- --ignored
cargo test -p ragfs --features postgres --lib lock::
```

Coverage includes connections/processes, ancestor/subtree conflicts, UTF-8 and
root path boundaries, namespaces, rollback, CAS, client clock skew, stale-holder
rejection/takeover, ordered renewal, handoff, and connection loss.
It also verifies independently configured stacks without a cache runtime, plus
database contention returning Busy and recovery on a subsequent operation.
Rollback tests verify that a renewed source is preserved and an expired source
cannot be restored even when the client's stale threshold is incorrect.
Long-path tests cover 3216-byte poorly compressible paths, long namespaces,
batch acquisition/rollback, renewal/release, CAS, root/ancestor/subtree boundaries,
and stale descendant cleanup beyond the bounded global sweep.
Fresh-schema and legacy-migration tests use session-local temporary tables,
verify record preservation and repeated initialization, and run with:

```sh
cargo test -p ragfs --features postgres --lib schema_initialization_and_legacy_migration --locked -- --ignored
```

Raw-write clock tests cover client timestamps `0`, `1`, and `u128::MAX`, v1/v2
creation, legacy refresh, exact CAS mismatch, independent holder timestamps,
owner changes, and conversion of a legacy reference. Expired records are injected
through test-only SQL rather than the normalizing provider APIs.

Validated on PostgreSQL 17.6 on 2026-11-12: 17 integration-file tests passed
serially with `--include-ignored --test-threads=1` (including expiry validation
and the subprocess entry), 93 existing lock tests passed with 2 ignored,
and the migration test passed separately. Migration coverage includes rollback
of a failed migration to the original schema and lease records.
Prefix coverage includes identical 2048-byte prefixes, UTF8 truncation within a
character, client-encoding independence, and adding the index to a UUID/hash schema.
Python PostgreSQL/SQLite configuration tests: 10 passed.
