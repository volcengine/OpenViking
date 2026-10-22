# Multi-Write Storage

V2 multi-write keeps one primary backend as the business source of truth and
replicates its files to configured backups.

## Read And Write Model

- All direct reads use the primary. Backups never serve application reads.
- A successful primary mutation returns immediately.
- Metadata persistence and backup application continue asynchronously.
- Backup lag does not change the result already returned for the primary.
- Multi-write metadata is stored only on the primary.

This model is eventually consistent. A process crash between primary success
and event persistence can lose that replication event. Rebuild affected
backups from the primary after such a crash.

## Metadata Model

Each account has a fixed set of metadata partitions. Events are routed to one
partition, appended to a segment log, and consumed independently by each
backup. Checkpoints compact older history, and garbage collection removes
metadata that is no longer required.

`initial_partitions` controls the partition count when an account's metadata is
created. Existing accounts keep their persisted partition count and routes when
the configuration changes. Partition expansion and shrinking are not supported
in this release.

## First Enable Import

When `.multiwrite.json` is absent, startup creates it as `V2/migrating`,
starts foreground V2 event submission, and imports the current primary files
in the background. Successful import changes the status to `stable`. Extra
old files on backups are not removed.

Old multi-write protocols and old migration fields are rejected. They are not
converted or interpreted.

## Operations

V2 does not expose synchronization status or manual retry APIs or CLI
commands. Use the multi-write Prometheus metrics to observe queue depth,
worker failures, backup lag, protocol version, and protocol status.

## Related Documents

- [Storage Architecture](./05-storage.md)
- [Metrics](./12-metrics.md)
- [Configuration Guide](../guides/01-configuration.md)
- [Multi-Write Storage Guide](../guides/13-multi-write-storage.md)
