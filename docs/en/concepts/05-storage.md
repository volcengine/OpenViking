# Storage Architecture

OpenViking uses a dual-layer storage architecture that separates content storage from index storage.

## Overview

```
┌─────────────────────────────────────────┐
│          VikingFS (URI Abstraction)      │
│    URI Mapping · Hierarchical Access     │
│           · Relation Management          │
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
| **AGFS** | Content storage | L0/L1/L2 full content, multimedia files, relations |
| **Vector Index** | Index storage | URIs, vectors, metadata (no file content) |

### Design Benefits

1. **Clear responsibilities**: Vector index handles retrieval, AGFS handles storage
2. **Memory optimization**: Vector index doesn't store file content, saving memory
3. **Single data source**: All content read from AGFS; vector index only stores references
4. **Independent scaling**: Vector index and AGFS can scale separately

## VikingFS Virtual Filesystem

VikingFS is the unified URI abstraction layer that hides underlying storage details.

### URI Mapping

```
viking://resources/docs/auth  →  /local/resources/docs/auth
viking://user/memories        →  /local/user/memories
viking://agent/skills         →  /local/agent/skills
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
| `relations(uri)` | Get relation list |
| `find(query, uri)` | Semantic search |

### Relation Management

VikingFS manages resource relations through `.relations.json`:

```python
# Create relation
viking_fs.link(
    from_uri="viking://resources/docs/auth",
    uris=["viking://resources/docs/security"],
    reason="Related security docs"
)

# Get relations
relations = viking_fs.relations("viking://resources/docs/auth")
```

## AGFS Backend Storage

AGFS provides POSIX-style file operations with multiple backend support.

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
├── .relations.json       # Relations table
└── *.md                  # L2 detailed content
```

## Vector Index

The vector index stores semantic indices, supporting vector search and scalar filtering.

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
# Automatically updates uri and parent_uri fields in vector index
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - URI specification
- [Retrieval Mechanism](./07-retrieval.md) - Retrieval process details
