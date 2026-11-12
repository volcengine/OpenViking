# PostgreSQL PathLock

[中文使用说明与文件改动清单](postgresql-pathlock_CN.md)

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

Initialization creates `public.ov_pathlock_tokens` and its expiry index. The role
needs CREATE on public plus SELECT/INSERT/UPDATE/DELETE on the table. These
objects may be provisioned by a migration role in advance; initialization still
executes `CREATE ... IF NOT EXISTS`.

## Semantics

Each operation runs in a transaction with a namespace advisory lock. This
serializes checks against absent rows, ancestors, and subtrees across processes.
Batches commit in full or roll back, including stale-record cleanup. C collation
preserves path segment boundaries and indexed descendant ranges.

Acquisition, renewal, expiry checks, and handoff rollback use the primary's
`clock_timestamp()`. The client supplies the lease duration. Expired holders
cannot renew; after takeover an old owner cannot renew or release the new lock.
The manager continues to maintain local lease lifetime and renewal scheduling.
The provider implements the existing `replace_holder` hook using the configured
expiry duration and database clock, preserving concurrent holder renewals.

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
