# Background Tasks

The Task API tracks asynchronous resource imports, session commits, reindexing, snapshot restores, and similar operations.

## API Reference

### get_task()

#### 1. API Implementation Introduction

Query background task status for APIs that return `task_id`, such as session commit, `add_resource`, and admin reindex.

**Task Statuses:**
- `pending`: Task waiting to execute
- `running`: Task in progress
- `completed`: Task successfully completed
- `failed`: Task failed

**Code Entries:**
- `openviking/server/routers/tasks.py:get_task()` - HTTP route

Task records are persisted in AGFS and can be queried after server restart, subject to task retention cleanup.

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_id | str | Yes | - | Task ID returned by a background API |

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
from openviking_sdk import AsyncHTTPClient

client = AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()

task = await client.get_task("uuid-xxx")
print(f"Status: {task['status']}")
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

### list_tasks()

#### 1. API Implementation Introduction

List background tasks visible to the current caller, supporting filtering by type, status, resource.

**Code Entries:**
- `openviking/server/routers/tasks.py:list_tasks()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.list_tasks()` - Python SDK

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_type | str | No | None | Filter by task type, for example `session_commit` |
| status | str | No | None | Filter by task status: `pending`, `running`, `completed`, `failed` |
| resource_id | str | No | None | Filter by task resource ID, for example a session ID |
| limit | int | No | 50 | Maximum number of task records to return |

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
