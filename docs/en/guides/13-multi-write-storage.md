# Multi-Write Storage Guide

V2 multi-write returns after the primary succeeds and updates metadata and
backups asynchronously. The primary is the only direct read source.

## Minimal Configuration

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "initial_partitions": 16,
        "checkpoint_interval_secs": 86400,
        "provider": "filesystem",
        "items": [
          {
            "name": "local-backup",
            "backend": "local",
            "params": {
              "workspace": "./data/backup"
            }
          }
        ]
      }
    }
  }
}
```

The three top-level V2 settings are:

| Field | Default | Meaning |
| --- | --- | --- |
| `initial_partitions` | `16` | Metadata partitions created for an account |
| `checkpoint_interval_secs` | `86400` | Checkpoint discovery interval; minimum `60` |
| `provider` | `"filesystem"` | Metadata segment provider: `filesystem` or `cache` |

Each `items[]` entry needs `name`, `backend`, and backend-specific `params`.
`encryption` is optional. Backup names must be unique and cannot be
`primary`.

When `provider` is `cache`, multi-write reuses the top-level CacheRuntime.
The top-level `cache.provider` selects the implementation; multi-write has no
separate Cache Provider connection.

The Python configuration layer still accepts legacy V1 sync, acknowledgement,
retry, operation, exclude, and redirect fields. It warns and removes them
before calling Rust; the fields have no effect.

## Runtime Behavior

- The primary is the only direct read source and business source of truth.
- Primary success returns without waiting for metadata or backups.
- Multi-write metadata is stored only on the primary.
- Backups converge in the background.
- There is no synchronization status or manual retry API or CLI command.

A crash between primary success and event persistence can lose one replication
event. Rebuild an affected backup from the primary.

## Partition Count

`initial_partitions` applies only when an account's partition manifest is first
created. Existing accounts continue to use their persisted partition count and
routes after configuration changes. Partition expansion and shrinking are not
supported in this release.

## First Enable And Rollback

When `.multiwrite.json` is absent, the mount starts foreground V2 event
submission and imports current primary files in the background. Extra old
files on backups are not removed.

Old multi-write protocols and old migration fields are rejected. Take a
complete backup before enabling multi-write. Restore that backup before
rolling back the binary.

## Verification

Use normal file APIs to verify primary reads and writes. Use the metrics in
[Metrics](../concepts/12-metrics.md) to monitor queue depth, worker failures,
backup lag, protocol version, and protocol status.

## Related Documents

- [Multi-Write Storage](../concepts/14-multi-write-storage.md)
- [Configuration Guide](./01-configuration.md)
- [OVPack Import and Export](./09-ovpack.md)
