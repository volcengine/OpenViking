# PostgreSQL PathLock

Use PostgreSQL PathLock when cooperating OpenViking processes or hosts need
shared Exact/Tree locks without Redis. PostgreSQL stores the lock records and
coordinates acquisition, renewal, release, and handoff. It requires no
PostgreSQL extension.

This is a PathLock backend. Business files, vector data, CacheFS, and QueueFS
retain their own backends and concurrency requirements.

## Choose a provider

| Provider | Coordination scope | Requirement |
| --- | --- | --- |
| `filesystem` | Participants accessing the same lock files | File backend with suitable locking semantics |
| `memory` | One process | No shared lock authority |
| `sqlite` | Processes on one machine | The same actual database file on local disk; no EFS/NFS/SMB |
| `postgres` | Processes or hosts | The same writable PostgreSQL primary and lock namespace |
| `cache` | Processes or hosts | Shared Redis, configured through top-level `cache` |

For replicas protecting the same files, use the same provider, top-level
`service_name`, and `lock_expire_secs`. PostgreSQL participants must also use the
same database. Separate service names create independent lock namespaces.

## Prepare PostgreSQL

Use PostgreSQL 13+ with UTF8 server encoding. The connection must reach the
writable primary; do not send lock operations to read replicas.

On first startup, the provider creates `public.ov_pathlock_tokens`, the
`public.path_prefix_2048(text)` helper, and lookup, expiry, and subtree indexes.
The initialization role needs CREATE on the `public` schema. The runtime role
needs SELECT, INSERT, UPDATE, DELETE on the table and EXECUTE on the helper.
A migration role can provision these objects and grant runtime permissions
before startup.

The helper's definition for pre-provisioning is:

```sql
CREATE FUNCTION public.path_prefix_2048(text) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS 'SELECT pg_catalog.substring(pg_catalog.convert_to($1, ''UTF8''), 1, 2048)';
```

Initialization only creates this helper when absent. Use this definition for an
existing helper. Upgrading the earlier `(namespace,path)` primary key requires
the table owner; stop all old provider instances before migration.

Use a Python native binding built with PostgreSQL support. When upgrading a
source installation, rebuild the binding before selecting the provider.
Rust integrations enable the `ragfs/postgres` feature.

## Configure `ov.conf`

Merge this fragment into your existing server configuration; keep your model,
file storage, vector database, and queue settings:

```json
{
  "service_name": "production",
  "storage": {
    "agfs": {
      "pathlock": {
        "provider": "postgres",
        "postgres_dsn_env": "OV_POSTGRES_DSN",
        "lock_expire_secs": 30.0
      }
    }
  }
}
```

| Field under `storage.agfs.pathlock` | Meaning |
| --- | --- |
| `provider` | Set to `postgres` |
| `postgres_dsn_env` | Required, nonempty name of the server environment variable containing the DSN |
| `lock_expire_secs` | Lease expiry duration; at least `1.0` seconds, default `30.0` |

Before starting every server and worker, set the named environment variable:

```sh
export OV_POSTGRES_DSN='host=pg.example.com port=5432 dbname=openviking user=openviking password=YOUR_PASSWORD sslmode=require'
```

Both PostgreSQL URLs and key/value DSNs are accepted. `postgres_dsn_env` contains
the variable name, not the connection string. The DSN is read at initialization;
restart participants after rotating credentials.

For remote connections, use `sslmode=require`; the native TLS connector verifies
the certificate and hostname using the system trust store. Use
`sslmode=disable` only for a disposable local test database.

No top-level `cache` configuration is required for PostgreSQL PathLock.
Redis is still required if other modules select a Redis cache backend.

## Verify coordination

Start two cooperating processes with the same primary, service name, and expiry.
Hold a Tree lease in one process and request an Exact lease below that root from
the other. The second acquisition should conflict until the first lease is
released or becomes eligible for expiry takeover. This verifies PathLock
coordination; it does not certify every storage backend for concurrent writers.

The provider uses the primary's clock for lock timestamps and expiry checks.
Acquisition batches commit in full or roll back. Holders renew and release
independently; expired holders cannot renew after takeover.

The provider can lock nonexistent logical paths without creating business
directories. The current snapshot API still skips missing targets for PostgreSQL,
because its selection code only requests Tree locks for `cache` and `sqlite`.
See the [explicit-path snapshot table](15-snapshot.md).

## Deployment and provider switches

Stop every writer and renewal task before switching providers, including
filesystem → PostgreSQL and Redis ↔ PostgreSQL. Providers have independent lock
authorities; PostgreSQL does not check existing filesystem, SQLite, or Redis
tokens. Preserve persistent queues and verify that consumers acquire fresh
destination-provider coverage before resuming work. See
[provider-switch notes](../concepts/09-transaction.md#switching-to-postgresql).

Selecting PostgreSQL PathLock does not change:

- QueueFS: it defaults to SQLite; do not share its WAL database across hosts on EFS/NFS/SMB.
- CachedFileSystem: its single-writer requirement remains.
- Local vector storage: its workspace process lock and concurrency restrictions remain.
- Business writes after lease loss: PathLock does not provide storage-side fencing.

Operations within one namespace are serialized. Measure acquisition and renewal
at your intended concurrency. Use one writable primary, prevent split brain,
and account for committed-lock loss with asynchronous replication during failover.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Unknown `postgres` provider | Rebuild or upgrade the native binding |
| Missing DSN | Set `postgres_dsn_env` and export that variable in the actual server/worker environment |
| Schema initialization fails | Check UTF8 encoding, schema permissions, object ownership, and migration duration |
| Participants do not conflict | Check primary, database, service name, provider, and expiry settings |
| Database `Busy` | Check database contention and load; it is distinct from a logical path conflict |

For all configuration fields, see [server configuration](../configuration/01-server.md#storage).
For lease and recovery behavior, see [Path Locks and Crash Recovery](../concepts/09-transaction.md).
