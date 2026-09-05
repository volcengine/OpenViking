# OpenViking Qdrant Integration Design

## Goal

Add a first-party Qdrant vector backend that preserves the current
`CollectionAdapter` contract, OpenViking path/tag/tenant semantics, and
hybrid sparse-vector correctness without reverting the removed implementation.

## Scope

### Phase 1

- Qdrant REST transport implemented with the Python standard library.
- Collection create/load/drop and index lifecycle.
- Dense vector upsert, fetch, delete, count, scalar ordering, and search.
- OpenViking filter AST mapping:
  - `And`, `Or`, `Eq`, `In`, `Range`, `TimeRange`, `PathScope`.
  - `Contains` is rejected unless a caller uses a supported Qdrant text field
    explicitly; the adapter never silently changes substring semantics.
- Segment-aware path scopes using `uri`, `uri_depth`, and `scope_roots`.
- Exact multi-tag AND and `In` semantics for `search_tags`.
- Account isolation through the existing `_SingleAccountBackend` filter path.
- Deterministic UUID point IDs with the original OpenViking ID in payload.
- Durable OpenViking metadata marker; existing unmarked collections fail closed.
- `USE_CONTENT_FIELD = False`; grep remains filesystem-backed.

### Phase 2

- Named Qdrant sparse vector support.
- Durable sparse-term dictionary stored in a sidecar Qdrant collection.
- Stable SHA-256-derived positive uint32 term IDs (the Qdrant REST/protobuf
  sparse-index limit) with collision detection and an explicit error on
  collision; no lossy hash merging.
- Dense-only configurations continue to work without creating sparse metadata.
- A non-zero sparse query against a backend configured without sparse support
  fails explicitly.

## Non-goals

- No revert of PR #3872.
- No Qdrant Python SDK dependency; REST is sufficient for the contract.
- No migration or implicit takeover of existing unmarked Qdrant collections.
- No server-side full-text content index in Qdrant.
- No change to upper-layer search APIs or Filter AST.
- No distributed metadata transaction protocol beyond Qdrant point-level
  idempotence and collision detection.

## Data model

The data collection stores:

- Dense vector under a configurable named vector (default `vector`).
- Sparse vector under a configurable named vector (default `sparse_vector`).
- OpenViking fields from the collection schema.
- `uri_depth` as an integer payload.
- `scope_roots` as an array of exact normalized path strings.
- `_openviking_original_id` as the original string ID.

The metadata collection stores:

- A fixed OpenViking schema marker point.
- One point per sparse term containing `term` and `index`.
- The deterministic point ID is derived from the term; a matching index with a
  different term is treated as a collision and raises.

## Filter mapping

Qdrant `must`/`should` clauses carry the OpenViking AST structure:

| OpenViking expression | Qdrant representation |
| --- | --- |
| `And` | `must` |
| `Or` | `should` |
| `Eq` | `match.value` |
| `In` | `match.any` for multiple values |
| `Range` / `TimeRange` | `range` |
| `PathScope(depth=0)` | exact `uri` match |
| `PathScope(depth<0)` | `scope_roots` exact element match |
| finite `PathScope` | `scope_roots` match plus `uri_depth` upper bound |

Multiple `Eq("search_tags", ...)` expressions remain separate `must` clauses,
so tag filters are AND rather than OR.

## Error handling

- HTTP failures become a backend-specific `QdrantError` carrying method, path,
  status, and a bounded response body.
- Missing OpenViking metadata refuses collection loading.
- Unsupported filters and sparse requests fail before sending a lossy query.
- Invalid vector dimensions and malformed sparse values fail at conversion.
- Qdrant response shape mismatches fail loudly rather than returning empty
  results.

## Testing

- Pure unit tests for path payload construction and filter compilation.
- Pure unit tests for deterministic IDs and sparse term collision handling.
- Fake-transport tests for collection CRUD, search, count, and index creation.
- Optional integration tests enabled by `QDRANT_URL`, skipped otherwise.
- Existing OpenViking unit tests remain unchanged.
