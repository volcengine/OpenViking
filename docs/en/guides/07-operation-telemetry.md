# Operation Telemetry

Operation telemetry lets you ask OpenViking to return a compact summary of what happened during a request, such as duration, token usage, vector retrieval activity, queue progress, and resource-processing stages.

Use it when you want to:

- debug a slow or unexpected request
- inspect token or retrieval behavior
- capture structured execution data in your own logs or observability pipeline

## How it works

Telemetry is opt-in. OpenViking only returns a top-level `telemetry` object when you request it.

Typical response shape:

```json
{
  "status": "ok",
  "result": {"...": "..."},
  "telemetry": {
    "id": "tm_xxx",
    "summary": {
      "operation": "search.find",
      "status": "ok",
      "duration_ms": 31.2,
      "tokens": {
        "total": 24,
        "llm": {
          "input": 12,
          "output": 6,
          "total": 18
        }
      },
      "vector": {
        "searches": 3,
        "scored": 26,
        "passed": 8,
        "returned": 5
      }
    }
  }
}
```

Notes:

- `telemetry.id` is an opaque correlation id
- `telemetry.summary` is the structured payload intended for users
- summary groups appear only when the operation produced them
- numeric `0` values are omitted from the response

## Supported operations

### HTTP API

Operation telemetry is currently available on these endpoints:

- `POST /api/v1/search/find`
- `POST /api/v1/search/search`
- `POST /api/v1/resources/temp_upload`
- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/sessions/{session_id}/commit`

### Python SDK

The same telemetry model is available from the Python clients for:

- `add_resource(...)`
- `add_skill(...)`
- `find(...)`
- `search(...)`
- `commit_session(...)`
- `Session.commit(...)`

## Requesting telemetry

### JSON APIs

For JSON request bodies, `telemetry` supports these forms:

```json
{"telemetry": true}
```

```json
{"telemetry": {"summary": true}}
```

`true` and `{"summary": true}` both request the same payload: `telemetry.id + telemetry.summary`.

The object form currently exposes the `summary` switch only.

If you do not want telemetry, either omit the field or set:

```json
{"telemetry": false}
```

```json
{"telemetry": {"summary": false}}
```

### Multipart upload API

`POST /api/v1/resources/temp_upload` is a multipart form endpoint. For this endpoint, pass telemetry as a form field:

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: your-key" \
  -F "file=@./notes.md" \
  -F "telemetry=true"
```

This endpoint currently accepts the boolean form only.

## Common summary groups

The top-level summary always includes:

- `operation`
- `status`
- `duration_ms`

Depending on the operation, you may also see these groups:

- `tokens`: LLM and embedding token totals
- `vector`: vector search and filtering counts
- `resource`: resource ingestion and processing stages
- `queue`: queue processing counts for wait-mode imports
- `semantic_nodes`: semantic extraction totals
- `memory`: memory extraction or dedup summaries
- `errors`: aggregated error information

If a group does not apply to the operation, it is omitted.

## Field reference

Only fields that are actually produced by an operation are returned. Missing groups should be treated as "not applicable" rather than as zero.

### Top-level telemetry fields

| Field | Meaning |
| --- | --- |
| `telemetry.id` | Opaque correlation id for this operation |
| `summary.operation` | Operation name, such as `search.find`, `resources.add_resource`, or `session.commit` |
| `summary.status` | Final telemetry status, usually `ok` or `error` |
| `summary.duration_ms` | End-to-end duration of the operation in milliseconds |

### `summary.tokens`

| Field | Meaning |
| --- | --- |
| `summary.tokens.total` | Total tokens counted for this operation |
| `summary.tokens.llm.input` | Total LLM input tokens |
| `summary.tokens.llm.output` | Total LLM output tokens |
| `summary.tokens.llm.total` | Total LLM tokens |
| `summary.tokens.embedding.total` | Total embedding-model tokens |

### `summary.vector`

| Field | Meaning |
| --- | --- |
| `summary.vector.searches` | Number of vector search calls |
| `summary.vector.scored` | Number of candidates that were scored |
| `summary.vector.passed` | Number of candidates that passed thresholding or later filters |
| `summary.vector.returned` | Number of results returned to upper-layer logic |
| `summary.vector.scanned` | Number of vectors scanned by the backend |
| `summary.vector.scan_reason` | Text description of the scan strategy or reason |

### `summary.resource`

This group appears on resource ingestion operations such as `resources.add_resource`.

| Field | Meaning |
| --- | --- |
| `summary.resource.request.duration_ms` | Total request-side duration for the add-resource flow |
| `summary.resource.process.duration_ms` | Duration of the main resource-processing flow |
| `summary.resource.process.parse.duration_ms` | Time spent parsing the resource |
| `summary.resource.process.parse.warnings_count` | Number of parse warnings |
| `summary.resource.process.finalize.duration_ms` | Time spent finalizing the resource tree |
| `summary.resource.process.summarize.duration_ms` | Time spent on summarize/vectorize processing |
| `summary.resource.wait.duration_ms` | Time spent waiting for downstream processing when `wait=true` |
| `summary.resource.watch.duration_ms` | Time spent creating, updating, or removing watch tasks |
| `summary.resource.flags.wait` | Whether the request used `wait=true` |
| `summary.resource.flags.build_index` | Whether the request enabled `build_index` |
| `summary.resource.flags.summarize` | Whether the request explicitly enabled `summarize` |
| `summary.resource.flags.watch_enabled` | Whether watch management was enabled for this request |

### `summary.queue`

This group appears when OpenViking waits for queue-backed work to complete.

| Field | Meaning |
| --- | --- |
| `summary.queue.semantic.processed` | Number of semantic-queue messages processed |
| `summary.queue.semantic.error_count` | Number of semantic-queue errors |
| `summary.queue.embedding.processed` | Number of embedding-queue messages processed |
| `summary.queue.embedding.error_count` | Number of embedding-queue errors |

### `summary.semantic_nodes`

| Field | Meaning |
| --- | --- |
| `summary.semantic_nodes.total` | Total DAG or semantic-node count |
| `summary.semantic_nodes.done` | Number of completed nodes |
| `summary.semantic_nodes.pending` | Number of pending nodes |
| `summary.semantic_nodes.running` | Number of nodes still running |

### `summary.memory`

This group appears on memory-extraction flows such as `session.commit`.

| Field | Meaning |
| --- | --- |
| `summary.memory.extracted` | Final number of memories extracted by the operation |
| `summary.memory.extract.duration_ms` | Total duration of the memory-extraction flow |
| `summary.memory.extract.candidates.total` | Total extracted candidates before final actions |
| `summary.memory.extract.candidates.standard` | Standard memory candidates |
| `summary.memory.extract.candidates.tool_skill` | Tool or skill candidates |
| `summary.memory.extract.actions.created` | Number of newly created memories |
| `summary.memory.extract.actions.merged` | Number of merges into existing memories |
| `summary.memory.extract.actions.deleted` | Number of deleted old memories |
| `summary.memory.extract.actions.skipped` | Number of skipped candidates |
| `summary.memory.extract.stages.prepare_inputs_ms` | Time spent preparing extraction inputs |
| `summary.memory.extract.stages.llm_extract_ms` | Time spent in the LLM extraction call |
| `summary.memory.extract.stages.normalize_candidates_ms` | Time spent parsing and normalizing candidates |
| `summary.memory.extract.stages.tool_skill_stats_ms` | Time spent aggregating tool or skill stats |
| `summary.memory.extract.stages.profile_create_ms` | Time spent creating or updating profile memory |
| `summary.memory.extract.stages.tool_skill_merge_ms` | Time spent merging tool or skill memories |
| `summary.memory.extract.stages.dedup_ms` | Time spent deduplicating candidates |
| `summary.memory.extract.stages.create_memory_ms` | Time spent creating new memories |
| `summary.memory.extract.stages.merge_existing_ms` | Time spent merging into existing memories |
| `summary.memory.extract.stages.delete_existing_ms` | Time spent deleting older memories |
| `summary.memory.extract.stages.create_relations_ms` | Time spent creating used-URI relations |
| `summary.memory.extract.stages.flush_semantic_ms` | Time spent flushing semantic queue work |

### `summary.errors`

| Field | Meaning |
| --- | --- |
| `summary.errors.stage` | Logical stage where the error was recorded |
| `summary.errors.error_code` | Error code or exception type |
| `summary.errors.message` | Human-readable error message |

## Examples

### Search request with telemetry

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "telemetry": true
  }'
```

### Add a resource and return telemetry

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "./docs/readme.md",
    "reason": "telemetry demo",
    "wait": true,
    "telemetry": true
  }'
```

### Python SDK

```python
from openviking import AsyncOpenVikingClient

client = AsyncOpenVikingClient(config_path="/path/to/config.yaml")
await client.initialize()

result = await client.find("memory dedup", telemetry=True)
print(result["telemetry"]["summary"]["operation"])
print(result["telemetry"]["summary"]["duration_ms"])
```

## Limitations and behavior

- OpenViking currently exposes summary-only telemetry to users
- `{"telemetry": {"events": true}}` is not a supported public request shape
- event-stream style selection is not part of the public API
- `session.commit` supports telemetry only when `wait=true`
- if you call `session.commit` with `wait=false` and request telemetry, the server returns `INVALID_ARGUMENT`
- telemetry shape is stable at the top level, but optional summary groups vary by operation

## Related docs

- [Monitoring & Health Checks](05-monitoring.md)
- [Authentication](04-authentication.md)
- [System API](../api/07-system.md)
