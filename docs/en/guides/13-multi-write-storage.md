# Multi-Write Storage Guide

This guide explains how to configure multi-write storage in OpenViking. Multi-write storage lets one primary backend replicate writes to multiple backup backends for replica storage, optional read routing, and migration. It does not automatically promote a backup when the primary fails.

Multi-write lives inside RAGFS. The Python SDK, HTTP API, and CLI usage remain unchanged.

## Prerequisites

- You already have a working `ov.conf`.
- The primary backend has been verified to read and write correctly.
- If you plan to use S3-compatible storage, prepare the bucket, endpoint, and access credentials first.
- For existing data, follow the [OVPack multi-write migration workflow](./09-ovpack.md#working-with-multi-write-storage); enabling backups alone does not copy historical files.

## Minimal Configuration

Merge these excerpts into your existing model/server configuration. The following example uses a local directory as the primary backend and replicates writes to another local directory.

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "sync_type": "async",
        "items": [
          {
            "name": "local-backup",
            "backend": "local",
            "local": {
                "workspace": "./data/backup"
            }
          }
        ]
      }
    }
  }
}
```

Notes:

- The top-level `backend` is the primary backend.
- `backups.items[]` is the backup backend list.
- `name` is the stable identity of a backup; later sync metadata refers to it.
- A backup with `backend = "local"` uses `local.workspace` to point to its local directory.
- If `sync_type` is omitted, treat it as async by default.

## Configuring Multiple Backups

You can configure more than one backup. The following example writes to both a local replica and S3-compatible object storage.

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "sync_type": "async",
        "items": [
          {
            "name": "local-az2",
            "backend": "local",
            "local": {
                "workspace": "./data/local-az2"
            }
          },
          {
            "name": "object-store",
            "backend": "s3",
            "s3": {
              "bucket": "openviking-backup",
              "region": "us-east-1",
              "endpoint": "https://s3.example.com",
              "access_key": "your-access-key",
              "secret_key": "your-secret-key",
              "prefix": "openviking",
              "directory_marker_mode": "none"
            }
          }
        ]
      }
    }
  }
}
```

Recommendations:

- Do not use unstable hostnames or temporary IDs for `name`.
- The backup path or bucket should not point to the same physical location as the primary backend.
- Changing a backup `name` affects historical sync metadata recognition, so treat that as a production change.

### S3-Compatible Storage Notes

Match these `s3` fields to the endpoint and object-store configuration:

| Field | Required? | Description |
| --- | --- | --- |
| `use_path_style` | Optional; default `true` | Use path-style URLs (`http://host/bucket/key`); select the style your endpoint supports. |
| `directory_marker_mode` | Yes for backup items | `none` creates no marker, `empty` creates a zero-byte marker, and `nonempty` creates a marker with content. Set it explicitly in each S3 backup item; if omitted, startup fails with `AGFSConfigError: invalid directory_marker_mode: null`. |
| `use_ssl` | Optional | Set to `false` for HTTP endpoints (e.g. `http://localhost:9000`). |

**Minimal S3-compatible example (RustFS/MinIO):**

```json
{
  "name": "s3-backup",
  "backend": "s3",
  "s3": {
    "bucket": "my-bucket",
    "endpoint": "http://localhost:9000",
    "access_key": "your-access-key",
    "secret_key": "your-secret-key",
    "prefix": "openviking",
    "use_ssl": false,
    "use_path_style": true,
    "directory_marker_mode": "none"
  }
}
```

The example uses `none`, which suits S3-compatible services that do not use directory markers (RustFS, MinIO, Ceph, etc.). Backup items do not get a default for this field. The top-level `storage` S3 configuration defaults to `empty`.

### Docker Networking for S3 Backup

When running OpenViking in Docker with an S3 backup on the same host:

- **Linux Docker**: Use `--network host` or the host's LAN IP. Docker bridge network can reach the host's LAN via gateway IP (e.g. `172.17.0.1:9000`).
- **macOS/Windows Docker Desktop**: Use `host.docker.internal` to reach the host from the container. Host networking is available as an opt-in feature in Docker Desktop 4.34 and later; see [Docker host networking](https://docs.docker.com/engine/network/drivers/host/).

If the server crashes silently on startup with S3 backup enabled, check the Docker networking first. The RAGFS Rust binding will produce `dispatch failure` if the S3 endpoint is unreachable from inside the container.

## Choosing a Sync Mode

### Async Mode

Async mode fits most deployments.

```json
{
  "backups": {
    "sync_type": "async",
    "items": []
  }
}
```

Characteristics:

- Returns immediately after the primary write succeeds.
- Backup writes run in the background.
- Low write latency.
- Lag has no fixed bound while a backend is unavailable; monitor synchronization and recovery.

Suitable for:

- Write throughput first.
- Backups mainly used for disaster recovery.
- Eventual consistency is acceptable.

### Sync Mode

Sync mode waits for backup acknowledgements.

```json
{
  "backups": {
    "sync_type": "sync",
    "write_ack_count": 1,
    "write_ack_timeout_ms": 5000,
    "items": [{"name": "local-backup", "backend": "local", "local": {"workspace": "./backup-data"}}]
  }
}
```

Parameters:

| Parameter | Description |
| --- | --- |
| `write_ack_count` | Minimum number of backup acknowledgements required before the write returns |
| `write_ack_timeout_ms` | Timeout in milliseconds while waiting for backup acknowledgements |

Characteristics:

- Stronger write confirmation.
- Write latency depends on backup responsiveness.
- Unconfirmed backups continue to be retried in the background.
- The client may still see an error after the primary write has already succeeded when the required backup acknowledgements are not met.

Suitable for:

- Narrowing the confirmation window between the primary and backups.
- Backup latency is predictable.
- The caller can accept the extra latency of synchronous writes.

## Configuring Read Acceleration

Backups do not participate in reads by default. To let a backup serve reads, explicitly configure `operations`.

```json
{
  "name": "cache-backend",
  "backend": "memfs",
  "operations": [
    {"operation": "write", "priority": 0},
    {
      "operation": "read",
      "priority": 10
    }
  ]
}
```

Read priority rules:

- Lower `priority` values are tried first.
- Only backups with `read` configured join the read route.
- The primary backend always remains the final fallback.
- Cold-backup nodes usually should not be read-enabled.

If a backup defines only `read` but not `write`, it does not receive normal multi-write replication. Use that only when you explicitly control how the backend gets its data.

## Redirect Configuration

Redirect sends matching files to a specified backup instead of the primary backend.

Redirect by file extension:

```json
{
  "storage": {
    "agfs": {
      "backend": "local",
      "redirects": [
        {
          "type": "FileExtensionPolicy",
          "extensions": ["(pdf|ppt|zip)"],
          "target": ["object-store"]
        }
      ],
      "backups": {
        "items": [
          {
            "name": "object-store",
            "backend": "s3",
            "s3": {
              "bucket": "openviking-large-files",
              "endpoint": "https://s3.example.com"
            }
          }
        ]
      }
    }
  }
}
```

Redirect by file size:

```json
{
  "type": "FileOverSizePolicy",
  "max_size_mb": 100,
  "target": ["object-store"]
}
```

Notes:

- `target` must reference an existing backup `name`.
- Redirected files still appear as normal readable, listable, and queryable files through the public APIs.
- Redirect mappings are stored in internal metadata on the primary backend.

## Exclude Configuration

Exclude makes one backup skip matching files.

```json
{
  "name": "cache-backend",
  "backend": "memfs",
  "excludes": [
    {
      "type": "FileOverSizePolicy",
      "max_size_mb": 50
    },
    {
      "type": "FileExtensionPolicy",
      "extensions": ["(mp4|zip)"]
    }
  ]
}
```

Common uses:

- Exclude large files from cache backends.
- Exclude file types that do not need to be preserved on a lower-cost backup.
- Keep one backup focused on text or configuration resources only.

If the target backup of a redirect also excludes the same file, the configuration is self-contradictory. Fix the configuration instead of expecting the system to guess another target.

## Encryption Configuration

Multi-write storage reuses OpenViking's transparent at-rest encryption.

Example with global encryption enabled:

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  },
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "backups": {
        "items": [
          {
            "name": "plain-cache",
            "backend": "memfs",
            "encryption": {
              "enabled": false
            }
          },
          {
            "name": "encrypted-backup",
            "backend": "local",
            "local": {
                "workspace": "./data/encrypted-backup"
            },
            "encryption": {
              "enabled": true
            }
          }
        ]
      }
    }
  }
}
```

Rules:

- When global `encryption.enabled=true`, the primary backend must be encrypted.
- Each backup may independently control encryption through `encryption.enabled`.
- The Python SDK, HTTP API, and CLI do not need to handle encryption or decryption.
- Internal metadata such as `.redirect.json` and `.sync_log.json` follows the primary backend's encryption policy.

## Migrating Existing Data

Multi-write only replicates writes that happen after it is enabled. It does not automatically copy historical files.

For OVPack migration, follow [Working with Multi-Write Storage](./09-ovpack.md#working-with-multi-write-storage): configure multi-write on the clean target before restoring through that server, then verify each replica before resuming writes. That guide also covers target account initialization and restore conflict handling.

## Verifying the Configuration

Before startup, it is recommended to run:

```bash
openviking-server doctor
```

After startup, verify with ordinary file APIs:

```bash
openviking write viking://resources/multiwrite-check.txt \
  --content "multi-write check"

openviking read viking://resources/multiwrite-check.txt
```

If you use a local backup, you can also inspect the backup directory directly. Also run `ov system backend sync-status viking://resources` and check every intended replica. A successful ordinary read may come only from the primary and does not prove replication.

## FAQ

### Why is a backup not serving reads?

Backups are write-only by default. To make a backup serve reads, configure:

```json
{
  "operations": [
    {"operation": "write", "priority": 0},
    {
      "operation": "read",
      "priority": 10
    }
  ]
}
```

### Why do historical files not appear in the backup after enabling multi-write?

Multi-write only handles new writes after it is enabled. For historical data, follow the [OVPack migration workflow](./09-ovpack.md#working-with-multi-write-storage); enabling backups does not backfill it.

### Can async mode guarantee that the newest data is immediately readable from a backup?

No. Background retries need a functioning backend to catch up. A stale but successful backup read does not fall back to the primary; route reads to the primary when current data is required.

### Will internal metadata files appear in normal user listings?

No. `.redirect.json` and `.sync_log.json` are internal files and are hidden from ordinary directory listings.

### If sync mode returns a failure, does that mean the primary backend definitely did not write the data?

No. The primary write may already have succeeded while the required backup acknowledgements were not met. In that case the client can see a failure even though the data already exists on the primary backend, and lagging backups will continue to be repaired in the background.

## Related Documents

- [Multi-Write Storage](../concepts/14-multi-write-storage.md)
- [Storage Architecture](../concepts/05-storage.md)
- [Configuration Guide](./01-configuration.md)
- [Encryption Guide](./08-encryption.md)
- [OVPack Import and Export](./09-ovpack.md)
