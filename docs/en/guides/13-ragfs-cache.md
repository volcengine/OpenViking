# RAGFS Cache

RAGFS cache is an optional read-cache layer for OpenViking. It speeds up full file reads and directory reads. It is only an acceleration layer, not the source of truth; backend filesystem data remains authoritative.

Assumptions:

- Only one OpenViking / RAGFS process writes to the same namespace.
- File and directory changes go through RAGFS.
- The backend is not modified externally by bypassing RAGFS.
- After a cache Provider successfully writes or deletes one key, later reads of that key do not return the old value.

## Quick Start

For first-time setup, complete the base configuration first:

```bash
openviking-server init
openviking-server doctor
```

Then enable the cache under `storage.agfs.cache` in `~/.openviking/ov.conf`. The following Redis example is a good quick validation setup:

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "cache": {
        "enabled": true,
        "provider": "redis",
        "namespace": "openviking",
        "max_file_size_bytes": 1048576,
        "bypass_prefixes": ["/queue", "/tmp"],
        "redis": {
          "mode": "standalone",
          "endpoints": ["redis://127.0.0.1:6379"],
          "pool_size": 32,
          "connect_timeout_ms": 1000,
          "command_timeout_ms": 20,
          "key_prefix": "ragfs-cache",
          "default_ttl_seconds": 3600,
          "read_from_replica": false
        }
      }
    }
  }
}
```

Start Redis and OpenViking:

```bash
redis-server
openviking-server --config ~/.openviking/ov.conf
```

If the configuration file is at the default path `~/.openviking/ov.conf`, you can also run:

```bash
openviking-server
```

Available Providers:

| Provider | Best for | Notes |
|----------|----------|-------|
| `memory` | Local validation and tests | In-process cache; lost after restart |
| `redis` | Fast rollout on standard networks | Currently supports standalone; read from primary only |
| `yuanrong` | Near-compute cache, shared memory, or heterogeneous multi-tier cache | Requires Yuanrong worker and native feature |
| `mooncake` | Remote memory pool, RDMA/TCP data plane | Requires Mooncake services and native feature |

If the runtime package was not compiled with the selected Provider, startup returns an error similar to "requires the ... feature".

## Configuration

`storage.agfs.cache` supports these common options:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | bool | `false` | Enable the RAGFS cache |
| `provider` | str | `"memory"` | `memory`, `redis`, `yuanrong`, or `mooncake` |
| `namespace` | str | `"openviking"` | Cache namespace for isolating deployments or tenants |
| `max_file_size_bytes` | int | `1048576` | Maximum full-file object size admitted to cache |
| `bypass_prefixes` | list[str] | `[]` | Path prefixes that always bypass cache |

Redis configuration:

| Option | Default | Description |
|--------|---------|-------------|
| `mode` | `"standalone"` | Redis deployment mode |
| `endpoints` | `["redis://127.0.0.1:6379"]` | Redis connection URLs |
| `username` | `""` | Redis ACL username |
| `password_env` | `""` | Environment variable that stores the Redis password |
| `pool_size` | `32` | Command concurrency |
| `connect_timeout_ms` | `1000` | Connection timeout |
| `command_timeout_ms` | `20` | Command timeout |
| `key_prefix` | `"ragfs-cache"` | Redis-side key prefix |
| `default_ttl_seconds` | `3600` | Default TTL; `0` means no TTL |
| `read_from_replica` | `false` | Must be `false` in standalone mode |

Yuanrong configuration:

```json
{
  "storage": {
    "agfs": {
      "cache": {
        "enabled": true,
        "provider": "yuanrong",
        "yuanrong": {
          "host": "127.0.0.1",
          "port": 31501,
          "connect_timeout_ms": 5000,
          "request_timeout_ms": 5000,
          "sdk_concurrency": 4
        }
      }
    }
  }
}
```

Mooncake configuration:

```json
{
  "storage": {
    "agfs": {
      "cache": {
        "enabled": true,
        "provider": "mooncake",
        "mooncake": {
          "local_hostname": "127.0.0.1",
          "metadata_server": "http://127.0.0.1:8080/metadata",
          "master_server_addr": "127.0.0.1:50051",
          "protocol": "tcp",
          "device_name": "",
          "global_segment_size": 536870912,
          "local_buffer_size": 134217728,
          "replica_num": 2,
          "sdk_concurrency": 4,
          "operation_timeout_ms": 5000
        }
      }
    }
  }
}
```

## Architecture

RAGFS splits caching into two layers:

- `CachedFileSystem`: implements filesystem semantics, including cache hit/miss handling, backend fallback, cache fill, invalidation, generation checks, and metrics.
- `CacheProvider`: only stores cache objects through `get`, `put`, `delete`, batch reads/writes, and close operations.

Call flow:

```text
OpenViking
  -> RAGFS / MountableFS
  -> CachedFileSystem
       |-> CacheProvider -> Memory / Redis / Yuanrong / Mooncake
       `-> Backend FileSystem
```

With this boundary, file, directory, rename, recursive delete, and write-after-invalidation logic live only in the common layer. A Provider does not need to understand path semantics; it only needs to store stable key-value objects.

## Cache Objects

RAGFS mainly caches three object types.

### File Cache

File keys use a stable namespace and path hash:

```text
ragfs:v1:{namespace}:file:{hash(path)}
```

The file value is a `CacheEnvelope` containing file content, object kind, path, and generation snapshots. After a full-read cache hit, RAGFS validates the envelope and generation before returning the content.

The default policy prefers summary files such as `.abstract.md` and `.overview.md`. Files larger than `max_file_size_bytes` are not admitted to cache. Non-full range reads also bypass the cache.

### Directory Cache

Directory key:

```text
ragfs:v1:{namespace}:dir:{hash(path)}
```

The directory cache stores raw backend `read_dir` entries, not permission-filtered final results. Permission, role, and agent-context filtering still happens in the OpenViking upper layer at request time.

This lets one directory cache object serve `ls`, `tree`, `glob`, the file-collection phase of `grep`, and path collection before delete or move operations.

### Subtree Generation

Subtree generation key:

```text
ragfs:v1:{namespace}:subtree:{hash(scope)}
```

`remove_all` and directory `rename` can leave descendant keys behind in the Provider. RAGFS bumps the subtree generation so old envelopes fail their generation snapshot check. Later real reads fall back to the backend and rebuild the cache.

## Consistency and Invalidation

In the single-writer scenario, RAGFS does not need a distributed write lock. The important part is maintaining three invalidation classes according to filesystem semantics:

- File changes: delete or update `file_key(path)` and delete `dir_key(parent)`.
- Directory changes: delete the directory's own `dir_key` and the parent directory's `dir_key`.
- Subtree changes: bump `subtree` generation for recursive delete and directory rename.

Typical write order:

```text
Acquire the in-process operation lock
-> Apply backend change
-> Update or delete related cache keys
-> Bump subtree generation when needed
-> Return result
```

If a Provider fails, RAGFS treats the backend as authoritative and puts the affected path into short-term bypass, avoiding reads from potentially stale cache.

## Request Coalescing

When multiple requests read the same uncached small file or directory at the same time, `CachedFileSystem` uses an in-process inflight table to coalesce them:

```text
The first miss becomes the leader and performs backend fallback and cache fill.
Later requests for the same key become followers and wait for the leader result.
The inflight entry is removed after the request completes.
```

This only reduces duplicate backend access within one OpenViking process. It does not change the Provider consistency boundary.

## Cache Policy

RAGFS automatically bypasses paths that are not suitable for caching:

- Lock files: `.path.ovlock`, `*.lock`, `*.lck`
- Control files: `enqueue`, `dequeue`, `peek`, `ack`
- Transient state: `heartbeat`, `lease`, `cursor`, `offset`, `pid`
- Path prefixes configured through `bypass_prefixes`

Add permission-sensitive directories to `bypass_prefixes`. If raw directory entries themselves depend on the caller's permissions, that directory should not be cached.

## Failure and Observability

The cache layer must not affect filesystem correctness:

- `get` failure: fall back to backend.
- `put` failure: record the error and put the path into bypass.
- `delete` failure: record the error and put the path or scope into bypass.
- Provider unavailable: do not return old cache; use backend results as authoritative.

Recommended signals to watch:

- cache hit / miss / bypass
- stale generation
- provider get / put / delete latency
- cache set / delete failures
- inflight leader / follower / backend saved
- backend fallback bytes

## Recommended Rollout

1. Use `memory` locally to validate the configuration shape.
2. Use `redis` to validate real remote-cache benefits.
3. Move to `yuanrong` or `mooncake` for high-performance environments.
4. Cache summary files and raw `read_dir` first, then expand to more regular small files.
5. Add lock, control-plane, and permission-sensitive paths to `bypass_prefixes`.

In short: RAGFS cache is responsible for correct invalidation according to filesystem semantics, while the Provider is responsible for where cache objects live. As long as the backend remains the source of truth, every cache hit must pass envelope and generation validation before it is returned.
