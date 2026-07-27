# Resource Watches

The Watch API manages periodic resource checks, pausing, resuming, and manual triggers.

## API Reference

### Watch Management

List, inspect, update, and trigger watch tasks created via [`add_resource`](02-resources.md#add_resource) with `watch_interval > 0`. The control plane is mirrored across REST (`/api/v1/watches`), the `ov task watch` CLI subcommand group, and a minimum-closure MCP surface (`list_watches` / `cancel_watch`) for agents.

#### 1. API Implementation Overview

This control plane wraps the `WatchManager` primitives without changing any server-side behavior. Every endpoint and CLI command resolves the target task by either its `task_id` (path) or its `to_uri` (query). The two keys are interchangeable; if both are supplied they must refer to the same task, otherwise the request is rejected with 400.

**Operations**:
- **List** (`GET /api/v1/watches`) — returns `{tasks, total}`; pass `?active_only=true` to filter; pass `?to_uri=...` to collapse to a single-task lookup
- **Show** (`GET /api/v1/watches/{task_id}`) — inspect one task; optional `?to_uri=` performs a cross-key sanity check
- **Update** (`PATCH /api/v1/watches/{task_id}` or `PATCH /api/v1/watches?to_uri=...`) — partial update of `watch_interval`, `is_active`, `reason`, `instruction`. `is_active` is orthogonal to `watch_interval`: flip `is_active` to pause/resume without losing the configured cadence.
- **Delete** (`DELETE /api/v1/watches/{task_id}` or `DELETE /api/v1/watches?to_uri=...`)
- **Trigger** (`POST /api/v1/watches/{task_id}/trigger` or `POST /api/v1/watches/trigger?to_uri=...`) — fire-and-forget refresh; returns immediately while the underlying re-ingest runs in the background

**Code Entry Points**:
- `openviking/server/routers/watches.py` — REST router for `/api/v1/watches`
- `crates/ov_cli/src/commands/watch.rs` — `ov task watch` CLI subcommand group
- `openviking/server/mcp_endpoint.py` — MCP `list_watches` / `cancel_watch` tools and the `watch_interval` / `to` parameters on `add_resource`
- `openviking/resource/watch_manager.py:WatchManager` — task persistence and scheduling primitives

#### 2. Interface and Parameter Description

For every single-task endpoint the path `{task_id}` can be replaced with a `?to_uri=` query argument. The CLI `<key>` argument is auto-classified: any value starting with `viking://` routes to the by-URI path, anything else is treated as a task ID (other URI schemes such as `http://` are rejected locally to avoid silent 404s).

**`PATCH /watches` body** (all fields optional; at least one is required)

| Field | Type | Description |
|-------|------|-------------|
| watch_interval | float | New cadence in minutes. Must be `> 0`; use `is_active=false` to pause without losing the cadence. |
| is_active | bool | Toggle activation without losing the cadence (pause / resume). |
| reason | string | Update the recorded reason for the watch. |
| instruction | string | Update the semantic processing instruction. |

Unrecognized fields are rejected with HTTP `400` and `INVALID_ARGUMENT` (`extra="forbid"` on the request model). Fields left unset preserve their current values.

#### 3. Usage Examples

**HTTP API**

```bash
# List active watch tasks (drop ?active_only to include paused ones)
curl -s "http://localhost:1933/api/v1/watches?active_only=true" \
  -H "X-API-Key: your-key"

# Pause a watch without losing its cadence
curl -X PATCH "http://localhost:1933/api/v1/watches/<task_id>" \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"is_active": false}'

# Trigger an immediate refresh (fire-and-forget; returns before the re-ingest finishes)
curl -X POST "http://localhost:1933/api/v1/watches/<task_id>/trigger" \
  -H "X-API-Key: your-key"

# Resolve by URI instead of task ID
curl -X DELETE "http://localhost:1933/api/v1/watches?to_uri=viking://resources/guide.md" \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
watches = client.list_watches(active_only=True)
client.update_watch(to_uri="viking://resources/guide.md", is_active=False)
client.trigger_watch(to_uri="viking://resources/guide.md")
client.delete_watch(to_uri="viking://resources/guide.md")
```

**TypeScript SDK**

```typescript
const watches = await client.listWatches({ activeOnly: true });
await client.updateWatch(
  { toUri: "viking://resources/guide.md" },
  { isActive: false },
);
await client.triggerWatch({ toUri: "viking://resources/guide.md" });
await client.deleteWatch({ toUri: "viking://resources/guide.md" });
```

**Go SDK**

```go
watches, err := client.ListWatches(ctx, &openviking.ListWatchesOptions{
    ActiveOnly: true,
})
updated, err := client.UpdateWatch(ctx, openviking.UpdateWatchOptions{
    ToURI:    "viking://resources/guide.md",
    IsActive: openviking.Bool(false),
})
triggered, err := client.TriggerWatch(ctx, openviking.WatchRef{
    ToURI: "viking://resources/guide.md",
})
deleted, err := client.DeleteWatch(ctx, openviking.WatchRef{
    ToURI: "viking://resources/guide.md",
})
_, _, _, _ = watches, updated, triggered, deleted
```

**CLI**

The following examples use the `ov task watch` subcommands:

```bash
# List active watches (drop --active-only to include paused ones)
ov task watch ls --active-only

# Inspect a single watch (key may be either a viking:// URI or a task_id)
ov task watch show viking://resources/guide.md

# Pause / resume without losing the cadence
ov task watch pause viking://resources/guide.md
ov task watch resume viking://resources/guide.md

# Update the cadence (or any combination of --active / --reason / --instruction)
ov task watch update viking://resources/guide.md --interval 30

# Trigger an immediate fire-and-forget refresh
ov task watch trigger viking://resources/guide.md

# Remove a watch task entirely
ov task watch rm viking://resources/guide.md
```

**Response**

Listing tasks returns:

```json
{
  "status": "ok",
  "result": {
    "tasks": [
      {
        "task_id": "7f02e980-8df9-4f27-a570-4d8428cbed8a",
        "path": "https://example.com/guide.md",
        "to_uri": "viking://resources/guide.md",
        "parent_uri": "viking://resources",
        "reason": "keep documentation current",
        "instruction": "",
        "watch_interval": 30,
        "build_index": true,
        "summarize": false,
        "processor_kwargs": {},
        "created_at": "2026-07-24T10:00:00",
        "last_execution_time": null,
        "next_execution_time": "2026-07-24T10:30:00",
        "is_active": true,
        "account_id": "default",
        "user_id": "default",
        "original_role": "user"
      }
    ],
    "total": 1
  }
}
```

Getting one task and a successful update return the same task object directly in `result`. Delete and trigger return:

```json
{
  "status": "ok",
  "result": {
    "task_id": "7f02e980-8df9-4f27-a570-4d8428cbed8a",
    "to_uri": "viking://resources/guide.md",
    "deleted": true
  }
}
```

```json
{
  "status": "ok",
  "result": {
    "task_id": "7f02e980-8df9-4f27-a570-4d8428cbed8a",
    "to_uri": "viking://resources/guide.md",
    "scheduled": true
  }
}
```

`scheduled=true` only confirms that background execution was scheduled. It does not mean re-ingestion has completed; read the task again and inspect `last_execution_time`.

**MCP** (agent control plane — minimum closure only)

```text
list_watches()                                            # one line per task; URIs only, no task_ids surfaced
cancel_watch(to_uri="viking://resources/guide.md")        # idempotent removal by URI
```

Pause / resume / trigger / update are intentionally not exposed via MCP — those power-user operations live on the CLI/REST surface to keep the agent system prompt compact. Creating a watch or changing its cadence from the agent side still goes through [`add_resource`](02-resources.md#add_resource) with `watch_interval`; pass `to` explicitly or let the system bind to the `root_uri` returned by this import.

---

## Related Documentation

- [Resources](02-resources.md) - create resources with watch_interval
- [Background Tasks](17-tasks.md) - inspect background processing
