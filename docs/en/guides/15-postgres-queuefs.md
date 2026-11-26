# PostgreSQL QueueFS

QueueFS can persist queues, messages, and consumer leases in PostgreSQL while retaining its control-file API. Instances using the same writable primary and top-level `service_name` can compete to consume the same queue.

## Provision and configure

An administrator or migration role must run the version 1 schema script once:

```sh
psql "$OV_QUEUE_PG_DSN" -v ON_ERROR_STOP=1 \
  -f crates/ragfs/src/plugins/queuefs/postgres_schema.sql
```

The runtime checks schema version and persistence properties and performs DML only. It does not create or migrate tables. The provisioning script is intentionally not a repeatable migration tool.

Grant the runtime role access, replacing `queue_runtime`:

```sql
GRANT USAGE ON SCHEMA public TO queue_runtime;
GRANT SELECT ON public.ov_queuefs_schema TO queue_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.ov_queuefs_queues, public.ov_queuefs_workers, public.ov_queuefs_messages
  TO queue_runtime;
GRANT USAGE ON SEQUENCE public.ov_queuefs_messages_sequence_seq TO queue_runtime;
```

Set the DSN environment variable in every server and worker:

```sh
export OV_QUEUE_PG_DSN='host=pg.example.com port=5432 dbname=openviking user=queue_runtime password=YOUR_PASSWORD sslmode=require'
```

Merge this fragment into `ov.conf`:

```json
{
  "service_name": "production",
  "storage": {
    "agfs": {
      "queuefs": {
        "mode": "shared",
        "backend": "postgres",
        "postgres_dsn_env": "OV_QUEUE_PG_DSN",
        "postgres_lease_seconds": 60
      }
    }
  }
}
```

Only the environment variable name enters plugin configuration. DSNs and server error details are excluded from backend errors. Standalone Rust builds require the `postgres` or `full` feature; the Python binding already enables `postgres`. Switching backends does not migrate existing local messages: drain the old queue or arrange a separate migration.

Namespaces allow 1–256 UTF-8 bytes and queue names allow 1–1024 bytes. SQLite options (`db_path`, `recover_stale_sec`, `busy_timeout_ms`) do not apply. Use `mode=shared` for competing consumers; `mode=worker` retains isolated worker queue names that other workers do not automatically poll.

## Delivery and recovery

- Enqueue returns after commit with `synchronous_commit=on`. Enable PostgreSQL `fsync` and reliable storage. Survival of acknowledged commits during replica failover depends on replication configuration.
- Dequeue protects scheduling eligibility with a queue-scoped transaction advisory lock, then claims the earliest eligible unlocked pending message using `FOR UPDATE SKIP LOCKED`, recording a random instance owner. Completion order is not strictly FIFO.
- Extended enqueue retains scheduling metadata. A `serial_key` allows one processing delivery, with successors blocked until earlier messages are ACKed. A `group_key` allows concurrent deliveries owned by one consumer; ownership is released when its last processing message is ACKed or retried.
- Retry requires the live delivery owner and retains message identity, payload, timestamp, and scheduling keys while returning it to the pending tail. Serial successors still wait for the original message's ACK.
- Messages remain until ACK. ACK must use the same QueueFS instance that claimed the message and checks both its live lease and message ownership. Repeated ACKs and ACKs from another live instance return false.
- A separate connection renews the instance lease every `floor(postgres_lease_seconds / 3)` seconds. Tasks may run longer than the lease while heartbeats continue.
- Each dequeue recovers processing messages in that queue whose owner is missing or expired. Startup never resets a live peer's processing messages. Peek and status do not trigger recovery.
- An expired instance cannot renew or perform queue operations and must restart. Drain business workers before normal shutdown, which releases the lease. Drop and process crashes leave the lease to expire.
- Status returns pending and processing counts from one SQL snapshot; size counts pending messages. Clear and queue deletion also remove processing messages, so coordinate outstanding business tasks.

Delivery is at least once. A crash after business writes and before ACK can repeat the work. ACK fencing protects queue state; it cannot undo business writes or stop an expired process from producing external side effects. Use message IDs for business idempotency. An interrupted commit can succeed without a response; operations are never automatically replayed after an ambiguous failure.

Processing messages owned by a live consumer are not recovered based on message age. Handler failures, failed ACKs, or a committed dequeue whose response was lost can leave a message owned by that consumer. ACK or retry from the original instance according to the business outcome, or stop/restart that instance and allow lease recovery. Restarting a different server does not release a live peer's lease.

Each instance uses two connections and runs database calls on blocking threads. Connection establishment timeout is 5 seconds, statement timeout is 5 seconds, and lock timeout is 1 second; these are not end-to-end network deadlines. Use the same lease settings across instances. Backend errors report SQLSTATE without DSNs, server details, or message contents.

Crashed instances can leave expired worker rows. They do not prevent recovery; maintenance can remove them:

```sql
DELETE FROM public.ov_queuefs_workers WHERE lease_expires_at <= clock_timestamp();
```

The service namespace isolates cooperating clients logically. A runtime role has DML access to all queue tables, so namespaces are not a security boundary against hostile clients; use separate databases and credentials when required. The backend requires ordinary logged tables without RLS.

PG QueueFS addresses queue persistence and competing delivery. Multi-instance deployment still requires suitable PathLock, business file storage, account store, vector backend, and handling of in-process task state.

## Verify

Run the real database contracts in a disposable PostgreSQL instance:

```sh
PG_BIN=/path/to/postgresql/bin python3 scripts/test_postgres_queuefs.py
```

Tests cover concurrent claims, ACK ownership, long task heartbeats, expired/crashed owner recovery, byte and nanosecond timestamp preservation, rollback, namespaces, a DML-only role, and rejection of an unprovisioned database. Full output is retained in `target/queuefs-postgres-test.log`.
