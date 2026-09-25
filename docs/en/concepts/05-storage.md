# Storage Architecture

OpenViking uses a dual-layer storage architecture that separates content storage from index storage.

## Overview

```
┌─────────────────────────────────────────┐
│          VikingFS (URI Abstraction)      │
│    URI Mapping · Hierarchical Access     │
└────────────────┬────────────────────────┘
        ┌────────┴────────┐
        │                 │
┌───────▼────────┐  ┌─────▼───────────┐
│  Vector Index  │  │      AGFS       │
│ (Semantic      │  │ (Content        │
│  Search)       │  │  Storage)       │
└────────────────┘  └─────────────────┘
```

## Dual-Layer Storage

| Layer | Responsibility | Content |
|-------|----------------|---------|
| **AGFS** | Content storage | L0/L1/L2 full content, multimedia files |
| **Vector Index** | Index storage | URIs, vectors, metadata, and retrieval text such as abstracts |

### Design Benefits

AGFS holds the source files. The vector index stores what retrieval needs, including URI references, vectors, metadata, and abstracts. Memory records store their body text in the abstract field, capped at 50,000 bytes, so the vector index can contain readable text.

The two backends can be configured separately. File APIs read from AGFS, while retrieval can return indexed text without rereading every source file. Backups and access controls should account for both stores.

Note: AGFS has been rewritten as a Rust implementation (RAGFS)

## VikingFS Virtual Filesystem

VikingFS is the unified URI abstraction layer that hides underlying storage details.

### URI Mapping

```
viking://resources/docs/auth  →  /local/{account_id}/resources/docs/auth
viking://~/memories        →  /local/{account_id}/user/{user_id}/memories
viking://~/skills          →  /local/{account_id}/user/{user_id}/skills
```

### Core API

| Method | Description |
|--------|-------------|
| `read(uri)` | Read file content |
| `write(uri, data)` | Write file |
| `mkdir(uri)` | Create directory |
| `rm(uri)` | Delete file/directory (syncs vector deletion) |
| `mv(old, new)` | Move/rename (syncs vector URI update) |
| `abstract(uri)` | Read L0 abstract |
| `overview(uri)` | Read L1 overview |
| `find(query, uri)` | Semantic search |

## AGFS Backend Storage

AGFS provides POSIX-style file operations with multiple backend support.

### Single-Backend and Multi-Write Modes

By default, AGFS uses a single backend for content storage. Once `storage.agfs.backups` is configured, OpenViking enters multi-write mode:

- Top-level `storage.agfs.backend` is the primary backend and remains the authoritative write target.
- `storage.agfs.backups.items[]` defines backup backends for replicas, migration, or read acceleration.
- The Python SDK, HTTP API, and CLI filesystem interfaces stay unchanged.
- Multi-write uses `.redirect.json` and `.sync_log.json` internally to track redirect mappings and sync progress. These files are not visible to users.

For the conceptual model, see [Multi-Write Storage](./14-multi-write-storage.md). For examples, see the [Multi-Write Storage Guide](../guides/13-multi-write-storage.md).

### Backend Types

| Backend | Description | Config |
|---------|-------------|--------|
| `localfs` | Local filesystem | `path` |
| `s3fs` | S3-compatible storage | `bucket`, `endpoint` |
| `memory` | Memory storage (for testing) | - |

### Directory Structure

A directory after semantic processing commonly has the following structure. Summary sidecars may be absent before processing or when only L0 has been created:

```
viking://resources/docs/auth/
├── .abstract.md          # L0 abstract
├── .overview.md          # L1 overview
└── *.md                  # L2 detailed content
```

## Vector Index

The vector index stores semantic indices, supporting vector search and scalar filtering.

### Local Record Format Compatibility

The local backend packs non-vector fields into a JSON payload. New candidate records and delta
logs store that payload as `text` with a 32-bit byte length, removing the previous 65,535-byte
limit on the combined JSON. The payload is preserved without truncation.

New records carry a format version. Existing unversioned records remain readable, and updates
write the new format; no full database rewrite, index rebuild, or embedding recomputation is
required for this format upgrade. Delta replay also accepts both formats.

This compatibility is one-way: older releases cannot read the new record format. To downgrade,
restore a backup taken before the first write with the new format; changing the executable alone
is insufficient.

### Context Collection Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Primary key |
| `uri` | string | Resource URI |
| `parent_uri` | string | Parent directory URI |
| `context_type` | string | resource/memory/skill |
| `is_leaf` | bool | Whether leaf node |
| `vector` | vector | Dense vector |
| `sparse_vector` | sparse_vector | Sparse vector |
| `abstract` | string | L0 abstract text |
| `name` | string | Name |
| `description` | string | Description |
| `created_at` | string | Creation time |
| `active_count` | int64 | Usage count |

### Index Strategy

Example index metadata; available settings depend on the selected vector backend:

```python
index_meta = {
    "IndexType": "flat_hybrid",  # Hybrid index
    "Distance": "cosine",        # Cosine distance
    "Quant": "int8",             # Quantization
}
```

### Backend Support

| Backend | Description |
|---------|-------------|
| `local` | Local persistence |
| `http` | HTTP remote service |
| `volcengine` | Volcengine VikingDB |

## Vector Synchronization

Filesystem operations coordinate changes to files and vector records. Summary refreshes can continue asynchronously, and failures can leave partial changes; see the [filesystem API](../api/03-filesystem.md) for operation-specific behavior. The examples below use an initialized synchronous Python SDK client.

### Delete Sync

```python
client.rm("viking://resources/docs/auth", recursive=True)
# Automatically deletes all records with this URI prefix from vector index
```

### Move Sync

```python
client.mv(
    "viking://resources/docs/auth",
    "viking://resources/docs/authentication"
)
# Automatically updates uri and parent_uri fields in vector index
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - URI specification
- [Multi-Write Storage](./14-multi-write-storage.md) - Primary/backup roles, routing, and consistency
- [Retrieval Mechanism](./07-retrieval.md) - Retrieval process details
