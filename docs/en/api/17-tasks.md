# Background Tasks

The Task API tracks asynchronous resource imports, session commits, reindexing, snapshot restores, and similar operations.

Submit an operation, save its `task_id`, and check status with separate requests. Receiving a task ID means the task was submitted; only `completed` means processing succeeded. `pending`, `running`, and `cancelling` are not terminal states. Stopping polling does not cancel the background task.

## API Reference

### get_task()

#### 1. API Implementation Introduction

Query background task status for APIs that return `task_id`, such as session commit, `add_resource`, and admin reindex.

**Task Statuses:**
- `pending`: Task waiting to execute
- `running`: Task in progress
- `cancelling`: Cancellation requested; waiting for the task's durable queue messages and in-process work to settle
- `completed`: Task successfully completed
- `failed`: Task failed
- `cancelled`: Task cancelled

**Code Entries:**
- `openviking/server/routers/tasks.py:get_task()` - HTTP route

Task records are persisted in AGFS and can be queried after server restart, subject to task retention cleanup.

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_id | str | Yes | - | Task ID returned by a background API |
| include_events | bool | No | false | Include persisted execution events in the HTTP response |

#### 3. Usage Examples

**HTTP API**

```http
GET /api/v1/tasks/{task_id}
```

```bash
curl -X GET http://localhost:1933/api/v1/tasks/uuid-xxx \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import asyncio

from openviking_sdk import AsyncHTTPClient

client = AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()

try:
    submitted = await client.add_resource("https://example.com/guide.md")
    task_id = submitted["task_id"]
    print(f"Import task: {task_id}")
    while True:
        task = await client.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} is no longer available")
        if task["status"] == "completed":
            break
        if task["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Import task {task_id}: {task['status']} ({task.get('error')})")
        await asyncio.sleep(2)
    print(task["result"])
finally:
    await client.close()
```

**TypeScript SDK**

```typescript
console.log(await client.getTask("task-id"));
```

**Go SDK**

```go
task, err := client.GetTask(ctx, "uuid-xxx")
if err != nil {
    return err
}
if task != nil {
    fmt.Println(task["status"])
}
```

**CLI**

```bash
ov task status uuid-xxx
```

**Response Example (resource import in progress)**

```json
{
  "status": "ok",
  "result": {
    "task_id": "uuid-xxx",
    "task_type": "add_resource",
    "status": "running",
    "resource_id": "viking://resources/guide",
    "stage": "processing_queue"
  }
}
```

`stage` is nullable. Git repository resource import tasks may report `queued`, `fetching`, `parsing`, `finalizing`, or `processing_queue`; other task types may leave it as `null`. Live queue counters are intentionally not part of task status; use observer queue APIs for live counts, or read `result.queue_status` after completion.

**Recorded execution events (HTTP)**

Request `GET /api/v1/tasks/{task_id}?include_events=true` to also receive `result.execution_events`. The default detail response and task lists omit this field. Events use the same authorization and retention policy as the task record.

```json
{
  "items": [
    {
      "seq": 1,
      "recorded_at": "2026-09-09T01:00:00.123456+00:00",
      "kind": "created",
      "status": "pending",
      "stage": null,
      "operation": null,
      "error": null
    }
  ],
  "dropped_count": 0,
  "started_mid_task": false
}
```

| Kind | Recorded fact |
|------|---------------|
| `created` | TaskTracker created the task record |
| `status_changed` | TaskTracker accepted a new status, including cancellation and terminal states |
| `stage_changed` | An execution path reported a different stage while the task was active |
| `error_recorded` | TaskTracker accepted the task's first sanitized error |
| `waiting_for_descendants` | The wait path observed unfinished owned queue work, excluding its own work ID |

`recorded_at` is the UTC time TaskTracker recorded the event, not browser polling time or necessarily when an underlying exception occurred. Events and task state are written together before publication. `seq` orders events within this task even when timestamps coincide. `stage` is the last reported task stage; parallel work can report errors from other stages. `operation`, when present, identifies the reporting work item. Errors use the existing sanitization and length limit; this history is not the complete component log or Python traceback.

History retains at most 64 events and 32 KiB of serialized event data. Older entries are removed first, `dropped_count` counts removed entries, and retained sequence numbers are not reset. Events expire with the task. Legacy tasks return `execution_events: null`; if an active legacy task later emits an event, `started_mid_task` is true. No earlier events are reconstructed. An older server can omit the field even when requested. Studio explains these cases and retains task metadata, results and errors.

To instrument another execution point, register its kind in `openviking/service/task_events.py`, add Studio translations, and call `await tracker.record_event(task_id, kind, account_id=..., user_id=..., operation=...)`. This internal API records a fact without changing status or stage; it accepts bounded operation identifiers, not arbitrary log payloads. Existing lifecycle methods automatically record their accepted transitions.

Persisted task files now contain `execution_events`. Rolling back requires a version that preserves unknown task fields (commit `a5166386` or later); older readers may reject these files. Rolling back also stops event reporting, so history for tasks active during a downgrade may be incomplete.

**Response Example (completed)**

```json
{
  "status": "ok",
  "result": {
    "task_id": "uuid-xxx",
    "task_type": "session_commit",
    "status": "completed",
    "result": {
      "session_id": "a1b2c3d4",
      "archive_uri": "viking://user/alice/sessions/a1b2c3d4/history/archive_001",
      "memory_diff_uri": "viking://user/alice/sessions/a1b2c3d4/history/archive_001/memory_diff.json",
      "memories_extracted": {
        "profile": 1,
        "preferences": 2,
        "entities": 1,
        "cases": 1
      },
      "active_count_updated": 2,
      "token_usage": {
        "llm": {
          "prompt_tokens": 5200,
          "completion_tokens": 1800,
          "total_tokens": 7000
        },
        "embedding": {
          "total_tokens": 1500
        },
        "total": {
          "total_tokens": 8500
        }
      }
    }
  }
}
```

`memories_extracted` in the completed task result reports per-category counts for this commit only. Sum its values when you want the total for this commit.

---

### cancel_task()

#### 1. API Implementation Introduction

Request cooperative cancellation of a background task. The operation immediately prevents the task from creating new QueueFS work and cancels its active in-process work; writes that already completed are not rolled back. If durable messages or in-process work remain, the operation first returns `cancelling`. The task becomes `cancelled` only after all owned work settles.

Repeated cancellation of a task in `cancelling` or `cancelled` is idempotent.

**Supported Task Types:**
- `add_resource`
- `session_commit`
- `admin_reindex`
- `snapshot_restore_reindex`

**Code Entries:**
- `openviking/server/routers/tasks.py:cancel_task()` - HTTP route
- `openviking/service/task_tracker.py:TaskTracker.cancel()` - task lifecycle
- `crates/ov_cli/src/commands/task.rs:cancel()` - CLI command

#### 2. Interface and Parameter Description

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_id | str | Yes | - | Background task ID to cancel |

Only the current user who owns the task can cancel it. ROOT identities cannot cancel tasks.

#### 3. Usage Examples

**Python SDK**

```python
task = await client.cancel_task(task_id="uuid-xxx")
print(task["status"])
```

**TypeScript SDK**

```typescript
const task = await client.cancelTask("uuid-xxx");
console.log(task.status);
```

**Go SDK**

```go
task, err := client.CancelTask(ctx, "uuid-xxx")
if err != nil {
    return err
}
fmt.Println(task["status"])
```

**HTTP API**

```http
POST /api/v1/tasks/{task_id}/cancel
```

```bash
curl -X POST http://localhost:1933/api/v1/tasks/uuid-xxx/cancel \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov task cancel uuid-xxx
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "task_id": "uuid-xxx",
    "task_type": "add_resource",
    "status": "cancelling",
    "resource_id": "viking://resources/guide",
    "stage": "processing_queue",
    "result": null,
    "error": null
  }
}
```

If the task has no remaining work, the response status can be `cancelled` immediately. Otherwise, continue polling with `get_task()` until the status becomes `cancelled`.

**Error Handling:**
- `NOT_FOUND` (404): the task does not exist, has expired, or belongs to another user
- `PERMISSION_DENIED` (403): a ROOT identity attempts to cancel a task
- `FAILED_PRECONDITION` (412): the task type does not support cancellation, or the task is already `completed`/`failed`

---

### list_tasks()

#### 1. API Implementation Introduction

List background tasks visible to the current caller, supporting filtering by type, status, resource.

**Code Entries:**
- `openviking/server/routers/tasks.py:list_tasks()` - HTTP route
- `sdk/python/openviking_sdk/client.py:AsyncHTTPClient.list_tasks()` - Python SDK

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_type | str | No | None | Filter by task type, for example `session_commit` |
| status | str | No | None | Filter by task status: `pending`, `running`, `cancelling`, `completed`, `failed`, `cancelled` |
| resource_id | str | No | None | Filter by task resource ID, for example a session ID |
| include_internal | bool | No | false | Include internal child tasks created by Connector imports |
| limit | int | No | 50 | Maximum number of task records to return |

By default, only user-visible tasks are returned. Pass `include_internal=true` when diagnosing a Connector import to include its internal `add_resource` child tasks.

#### 3. Usage Examples

**HTTP API**

```http
GET /api/v1/tasks?task_type=session_commit&status=running&limit=20
```

```bash
curl -X GET "http://localhost:1933/api/v1/tasks?task_type=session_commit&status=running&limit=20" \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
from openviking_sdk import AsyncHTTPClient

client = AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()

tasks = await client.list_tasks(
    task_type="session_commit",
    status="running",
    limit=20,
)
for task in tasks:
    print(task["task_id"], task["status"])
await client.close()
```

**TypeScript SDK**

```typescript
console.log(await client.listTasks());
```

**Go SDK**

```go
tasks, err := client.ListTasks(ctx, &openviking.ListTasksOptions{
    TaskType: "session_commit",
    Status:   "running",
    Limit:    20,
})
if err != nil {
    return err
}
for _, task := range tasks {
    fmt.Println(task)
}
```

**CLI**

```bash
# List tasks
ov task list

# Filter by task type and status
ov task list --task-type session_commit --status running
```

**Response Example**

```json
{
  "status": "ok",
  "result": [
    {
      "task_id": "uuid-xxx",
      "task_type": "session_commit",
      "status": "running",
      "resource_id": "a1b2c3d4",
      "created_at": 1770000000.0,
      "updated_at": 1770000005.0,
      "result": null,
      "error": null,
      "stage": null
    }
  ]
}
```

---

## Related Documentation

- [Sessions](05-sessions.md) - session commit tasks
- [Resources](02-resources.md) - resource ingestion tasks
- [Content](12-content.md) - asynchronous reindex tasks
