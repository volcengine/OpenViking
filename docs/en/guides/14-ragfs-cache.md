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

Configure the global top-level `cache` Provider, then select `backend=cache` under `storage.agfs.cachefs`:

```json
{
  "cache": {
    "provider": "redis",
    "params": {
      "mode": "standalone",
      "endpoints": ["redis://127.0.0.1:6379"],
      "pool_size": 32,
      "connect_timeout_ms": 1000,
      "command_timeout_ms": 1000,
      "default_ttl_seconds": 3600
    }
  },
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "cachefs": {
        "backend": "cache",
        "namespace": "openviking",
        "max_file_size_bytes": 1048576,
        "bypass_prefixes": ["/queue", "/tmp"]
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
| `redis` | Default delivery on standard networks | Built into RAGFS; supports standalone, Cluster, and Sentinel |
| `dynamic` | YuanRong, Mooncake, or closed-source cache systems | Loaded from an external shared library through the versioned C ABI |

`MemoryMockProvider` is only used by unit and smoke tests; it is not a production configuration option.

## Breaking Configuration Change

Legacy cache configuration is no longer accepted. Migrate before upgrading:

| Removed configuration | Canonical replacement |
|-----------------------|-----------------------|
| `storage.agfs.cache` | top-level `cache.provider` + `cache.params`, plus `storage.agfs.cachefs.backend="cache"` |
| `storage.agfs.queuefs.backend="redis"` and `queuefs.redis` | `storage.agfs.queuefs.backend="cache"`, plus the shared top-level `cache` section |
| Redis `mode="singleton"` | `mode="standalone"` |
| `tls_enabled=true` | use `rediss://` endpoints |
| `read_from_replica` | removed; all reads use the primary |
| Redis Provider `key_prefix` | CacheFS uses `cachefs.namespace`; QueueFS uses `queuefs.cache_key_prefix` |

OpenViking rejects removed fields with a migration error instead of silently translating them.

## DynamicProvider

OpenViking ships the DynamicProvider loader and a versioned C ABI. The default wheel does not bundle third-party SDKs or Provider libraries. Deploy the Provider shared library separately and configure `provider=dynamic` when an external cache system is required.

A dynamic library must export this versioned entry point:

```text
openviking_cache_provider_v1
```

The C contract is defined by `crates/ragfs/include/openviking_cache_provider_v1.h`. The Provider must use the Host allocator for returned data, obey the documented ownership and close semantics, catch exceptions before they cross the C ABI, and make its handle safe for concurrent calls.

Provider artifacts should declare the ABI version, target OS and CPU, minimum runtime version, external SDK version, dynamic dependencies, and SHA256. When a Provider depends on native libraries, its publisher must make them discoverable through RPATH, `LD_LIBRARY_PATH`, or deployment instructions.

External Providers can be upgraded independently without rebuilding the default OpenViking wheel. OpenViking only needs a coordinated upgrade when the DynamicProvider ABI becomes incompatible.

## Configuration

The top-level `cache` section is a sibling of `storage`:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `provider` | str | none | Provider name; supports `redis` and `dynamic` |
| `params` | object | `{}` | Provider-owned parameters |

`storage.agfs.cachefs` controls CacheFS behavior:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `backend` | str | `"local"` | `local` preserves existing behavior; `cache` enables CachedFileSystem |
| `namespace` | str | `"openviking"` | Cache namespace for isolating deployments or tenants |
| `max_file_size_bytes` | int | `1048576` | Maximum full-file object size admitted to cache |
| `traversal_mode` | str | `"backend"` | Use backend traversal or `cached_traversal` for recursive APIs |
| `bypass_prefixes` | list[str] | `[]` | Path prefixes that always bypass cache |

Redis configuration:

| Option | Default | Description |
|--------|---------|-------------|
| `mode` | `"standalone"` | Redis deployment mode |
| `endpoints` | `["redis://127.0.0.1:6379"]` | Redis connection URLs; use `redis://` for plaintext or `rediss://` for TLS |
| `username` | `""` | Redis ACL username |
| `password_env` | `""` | Environment variable that stores the Redis password |
| `pool_size` | `32` | Command concurrency |
| `connect_timeout_ms` | `1000` | Connection timeout |
| `command_timeout_ms` | `20` | Command timeout |
| `default_ttl_seconds` | `3600` | Default TTL; `0` means no TTL |
| `tls_insecure_skip_verify` | `false` | Skip certificate verification for `rediss://`; intended only for controlled test environments |

All Redis reads are sent to the primary node so QueueFS does not observe stale queue state. CacheRuntime relies on Redis transport security when required and does not add a separate application-layer value encryption format.

DynamicProvider configuration:

OpenViking uses `cache.params.library` to load the dynamic library. All remaining fields are Provider-owned and passed to `create` as JSON. Use the schema documented by the Provider publisher.

```json
{
  "cache": {
    "provider": "dynamic",
    "params": {
      "library": "/opt/openviking/providers/libopenviking_cache_provider.so",
      "endpoint": "127.0.0.1:31501",
      "request_timeout_ms": 5000
    }
  }
}
```

## Architecture

RAGFS splits caching into two layers:

- `CachedFileSystem`: implements filesystem semantics, including cache hit/miss handling, backend fallback, cache fill, invalidation, generation checks, and metrics.
- `CacheRuntime`: exposes common primitive operations and binds either the built-in RedisProvider or an external DynamicProvider during startup.

Call flow:

```text
OpenViking
  -> RAGFS / MountableFS
  -> CachedFileSystem
       |-> CacheRuntime -> RedisProvider
       |               `-> DynamicProvider -> external shared library
       `-> Backend FileSystem
```

With this boundary, file, directory, rename, recursive delete, and write-after-invalidation logic live only in the common layer. An external Provider does not need to understand path semantics; it only supplies primitive key-value operations through the stable C ABI.

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

1. Disable caching to validate baseline backend behavior.
2. Use the built-in `redis` Provider to validate remote-cache benefits.
3. Use an independently released DynamicProvider `.so` for high-performance or closed-source cache systems.
4. Cache summary files and raw `read_dir` first, then expand to more regular small files.
5. Add lock, control-plane, and permission-sensitive paths to `bypass_prefixes`.

In short: RAGFS cache is responsible for correct invalidation according to filesystem semantics, while the Provider is responsible for where cache objects live. As long as the backend remains the source of truth, every cache hit must pass envelope and generation validation before it is returned.
