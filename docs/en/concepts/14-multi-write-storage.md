# Multi-Write Storage

Multi-write storage lets OpenViking use one primary storage backend together with multiple backup backends under a unified filesystem abstraction. It is suitable for high availability, cross-region replicas, read acceleration, and storage migration.

From the API user's point of view, interfaces such as `read()`, `write()`, `ls()`, and `stat()` do not change. Multi-write logic lives inside RAGFS, so callers do not need to care which underlying backend ultimately stores a file.

## Core Model

Multi-write storage consists of one primary backend and multiple backup backends:

| Role | Configuration | Description |
| --- | --- | --- |
| primary | `storage.agfs.backend` | Authoritative write target and final read fallback |
| backup | `storage.agfs.backups.items[]` | Receives replicated writes and may optionally participate in reads |

If `backups` is not configured, OpenViking continues to use the original single-backend mode.

## Write Path

By default, writes land on the primary backend first and are then replicated to write-enabled backup backends.

```text
Client
  -> OpenViking API
  -> RAGFS MultiWrite
  -> primary
  -> backup1 / backup2 / ...
```

If a backup does not define `operations`, it participates in writes by default. This keeps cold-backup setup simple.

## Sync Modes

Multi-write supports two consistency modes.

| Mode | Config value | Behavior | Suitable for |
| --- | --- | --- | --- |
| Async multi-write | `async` | Return as soon as the primary write succeeds; backups sync in the background | Low-latency writes, eventual consistency |
| Sync multi-write | `sync` | Wait for backup acknowledgements after the primary write succeeds | Stronger write confirmation when extra latency is acceptable |

In async mode, backups may lag behind the primary for a short time. In sync mode, `write_ack_count` and `write_ack_timeout_ms` control how many backup acknowledgements are required and how long the system waits.

Even in sync mode, backups that timed out or did not confirm are still retried in the background.

## Read Path

Reads do not hit every backup by default. Only backups that explicitly declare the `read` operation join the read route.

Read order:

```text
1. Read-enabled backups in ascending priority order
2. Fallback to the primary backend
3. If the file is redirected, access the redirect target
4. Return NotFound if the file is still missing
```

This avoids having cold-backup nodes participate in reads by default and reduces the risk of serving stale data.

## Redirect

Redirect means "certain files are not written to the primary backend and are written to a specified backup instead."

Common cases:

- Large files go to object storage.
- Specific file extensions go to a dedicated backend.
- The primary backend keeps standard content, while special files live elsewhere.

Redirect policies are configured on the primary backend. When a file matches a policy, OpenViking records the mapping in internal metadata. Calls such as `ls()`, `stat()`, and `read()` still expose a normal filesystem view.

## Exclude

Exclude means "a specific backup backend does not receive matching files."

Common cases:

- A memory or cache backend should not keep large files.
- One backup only stores text resources.
- A lower-cost backend excludes temporary or oversized files.

Exclude policies are configured on each backup and affect only whether that backup receives a write.

## Internal Metadata

Multi-write uses two internal metadata files:

| File | Purpose |
| --- | --- |
| `.redirect.json` | Records which backend stores each redirected file |
| `.sync_log.json` | Records per-file sync version and backup acknowledgement progress |

These files are hidden from normal users, do not appear in standard directory listings, and should not be read or written through public APIs.

If the primary backend enables at-rest encryption, these internal metadata files follow the same encryption policy.

## Encryption Relationship

Multi-write does not change OpenViking's transparent encryption model.

Rules:

- The Python layer and public APIs stay unaware of encryption details.
- The primary backend must be encrypted when global encryption is enabled.
- Each backup may decide independently whether to enable encryption.
- Internal metadata must go through the primary backend's encryption path.

That means enabling multi-write does not change how clients call the system; encryption remains a configuration concern per backend.

## Relationship with OVPack

Multi-write only handles new writes after it is enabled. It does not automatically sync historical files that already exist in the primary backend.

Recommended migration flow:

1. Use OVPack or another controlled process for a full data migration.
2. Validate the target backend data.
3. Enable the multi-write configuration.
4. Let future new writes and updates continue to replicate through multi-write.

## Limitations

- In async mode, backups may lag temporarily.
- Historical files that existed before multi-write was enabled need a separate migration or backfill process.
- Redirected files rely on internal metadata to reconstruct the directory view.
- Concurrent writes from multiple processes to the same primary backend still need future distributed metadata locking.
- Hot directories may update internal metadata frequently and can introduce additional write amplification.

## Related Documents

- [Storage Architecture](./05-storage.md)
- [Configuration Guide](../guides/01-configuration.md)
- [Multi-Write Storage Guide](../guides/13-multi-write-storage.md)
- [OVPack Import and Export](../guides/09-ovpack.md)
