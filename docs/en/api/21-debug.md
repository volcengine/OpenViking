# Debug

Diagnostic endpoints for inspecting the vector index maintained by OpenViking. They are intended for troubleshooting and operational verification — for example, confirming that a URI actually has vector records, or what metadata (such as tags) is attached to them. On the reference self-hosted deployment (v0.4.17.dev7 with Qwen3-Embedding-8B) these endpoints were used to verify tag propagation and record state after ingestion and reindexing. They are not part of the stable retrieval surface and the record shape may vary by vector backend.

All endpoints are read-only and apply the same tenant isolation as regular requests: records are filtered to the account/user (and peer, when `X-OpenViking-Actor-Peer` is set) of the calling context.

## API Reference

### debug/vector/scroll

#### 1. API Implementation Introduction

Returns vector records paginated, optionally restricted to one URI. Useful for checking what is actually stored in the vector database after ingestion or reindexing.

**Code Entry**:
- `openviking/server/routers/debug.py:debug_vector_scroll()` - HTTP router
- `openviking/storage/vikingdb_manager.py:VikingDBManagerProxy.scroll()` - Tenant-isolated vector store access

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| limit | int | No | `100` | Page size, `1`–`1000` |
| cursor | str | No | None | Opaque cursor taken from a previous response's `next_cursor` |
| uri | str | No | None | Restrict records to this Viking URI (path variables such as `~` are resolved against the request identity) |

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/debug/vector/scroll
```

```bash
curl "http://localhost:1933/api/v1/debug/vector/scroll?limit=10&uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"
```

#### 4. Response Contract

Successful responses use the standard response envelope. `result.records` is a list of raw vector records (shape depends on the configured vector backend), and `result.next_cursor` is the cursor to pass as `cursor` for the next page; it is empty or absent when there are no more pages.

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "records": [
      {
        "uri": "viking://resources/docs/auth/login.md",
        "tags": ["project=demo"],
        "...": "backend-specific fields"
      }
    ],
    "next_cursor": ""
  },
  "time": 0.012
}
```

---

### debug/vector/count

#### 1. API Implementation Introduction

Returns the number of vector records matching an optional filter, without transferring the records themselves. Useful for verifying ingestion completeness or the effect of a reindex.

**Code Entry**:
- `openviking/server/routers/debug.py:debug_vector_count()` - HTTP router
- `openviking/storage/vikingdb_manager.py:VikingDBManagerProxy.count()` - Tenant-isolated vector store access

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | No | None | Count only records under this Viking URI |
| filter | str | No | None | JSON-encoded vector-store filter expression, e.g. `{"op":"must","field":"uri","conds":["viking://resources/docs"]}`. Combined with `uri` when both are given |

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/debug/vector/count
```

```bash
curl "http://localhost:1933/api/v1/debug/vector/count?uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"
```

#### 4. Response Contract and Error Handling

`result.count` is the number of matching vector records after tenant isolation and filtering.

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "count": 42
  },
  "time": 0.008
}
```

**Error Handling**

- `INVALID_FILTER`: `filter` is not valid JSON.
- `NO_VECTOR_DB`: the vector database is not initialized.

---

## Related Documentation

- [System Status](07-system.md) - health/ready/status and consistency endpoints
- [Retrieval](06-retrieval.md) - behavioral notes on tag filtering and the same-query cache
