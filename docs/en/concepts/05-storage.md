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
| **Vector Index** | Index storage | URIs, vectors, metadata; compatible VikingDB-backed collections can also persist bounded content for grep recall |

### Design Benefits

1. **Clear responsibilities**: Vector index handles retrieval; AGFS remains the authoritative content source
2. **Backend-aware footprint**: Compatible VikingDB-backed collections can persist bounded derived content; other adapters store references and index fields only
3. **Consistent reads**: Regular file reads resolve from AGFS; vector-index content is a derived retrieval projection
4. **Independent scaling**: Vector index and AGFS can scale separately

With a compatible collection schema, VikingDB-backed adapters can persist a bounded `content` projection (up to 1 MiB) for VikingDB FullText/BM25 candidate recall used by grep. Precise matching still reads the recalled files from AGFS. Other adapters drop `content` before writing to the vector index.

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

Each context directory follows a unified structure:

```
viking://resources/docs/auth/
├── .abstract.md          # L0 abstract
├── .overview.md          # L1 overview
└── *.md                  # L2 detailed content
```

## Vector Index

The vector index stores semantic indices, supporting vector search and scalar filtering. The current unified schema declares the following fields; whether `content` is persisted depends on the backend and collection schema as described above.

### Context Collection Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Primary key |
| `uri` | path | Viking URI |
| `type` | string | Reserved resource subtype |
| `context_type` | string | resource/memory/skill |
| `vector` | vector | Dense vector |
| `sparse_vector` | sparse_vector | Sparse vector |
| `created_at` | date_time | Creation time |
| `updated_at` | date_time | Last update time |
| `active_count` | int64 | Usage count |
| `level` | int64 | L0/L1/L2 level |
| `name` | string | Name |
| `description` | string | Description |
| `tags` | string | Tags |
| `search_tags` | list&lt;string&gt; | Search tags |
| `abstract` | string | L0 abstract text |
| `content` | text | Bounded full-text projection (compatible VikingDB collections) |
| `account_id` | string | Account scope |
| `owner_user_id` | string | Owner user scope |

### Index Strategy

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

VikingFS automatically maintains consistency between vector index and AGFS.

### Delete Sync

```python
viking_fs.rm("viking://resources/docs/auth", recursive=True)
# Automatically deletes all records with this URI prefix from vector index
```

### Move Sync

```python
viking_fs.mv(
    "viking://resources/docs/auth",
    "viking://resources/docs/authentication"
)
# Automatically updates affected uri values in vector index
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - URI specification
- [Multi-Write Storage](./14-multi-write-storage.md) - Primary/backup roles, routing, and consistency
- [Retrieval Mechanism](./07-retrieval.md) - Retrieval process details
