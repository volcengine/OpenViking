# 后台任务

任务 API 用于跟踪资源导入、会话提交、索引维护和快照恢复等异步操作。

## API 参考

### get_task()

#### 1. API 实现介绍

查询返回 `task_id` 的后台任务状态，例如 session commit、`add_resource` 和 admin reindex。

**任务状态**：
- `pending`: 任务等待执行
- `running`: 任务执行中
- `cancelling`: 已请求取消，正在等待该任务的持久化队列消息和进程内工作结束
- `completed`: 任务成功完成
- `failed`: 任务失败
- `cancelled`: 任务已取消

**代码入口**：
- `openviking/server/routers/tasks.py:get_task()` - HTTP 路由

任务记录会持久化到 AGFS，服务重启后仍可查询，但仍受任务保留清理策略影响。

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_id | str | 是 | - | 后台 API 返回的任务 ID |

#### 3. 使用示例

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

task = await client.get_task(task_id="uuid-xxx")
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

**响应示例（资源导入进行中）**

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

`stage` 可以为 `null`。Git 仓库资源导入任务可能报告 `queued`、`fetching`、`parsing`、`finalizing`、`processing_queue`；其他任务类型可能将其留空。实时队列计数不会出现在任务状态中；需要实时数量时使用 observer queue，任务完成后可读取 `result.queue_status`。

**响应示例（完成）**

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

---

### cancel_task()

#### 1. API 实现介绍

请求协作式取消后台任务。接口会立即阻止该任务产生新的 QueueFS work，并取消仍在运行的进程内工作；已经完成的写入不会回滚。当任务仍有待处理的持久化消息或进程内工作时，接口先返回 `cancelling`，全部收敛后任务才进入 `cancelled`。

重复取消处于 `cancelling` 或 `cancelled` 状态的任务是幂等的。

**支持的任务类型**：
- `add_resource`
- `session_commit`
- `admin_reindex`
- `snapshot_restore_reindex`

**代码入口**：
- `openviking/server/routers/tasks.py:cancel_task()` - HTTP 路由
- `openviking/service/task_tracker.py:TaskTracker.cancel()` - 任务生命周期
- `crates/ov_cli/src/commands/task.rs:cancel()` - CLI 命令

#### 2. 接口和参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_id | str | 是 | - | 要取消的后台任务 ID |

只有任务所属的当前用户可以取消任务。ROOT 身份不能执行取消操作。

#### 3. 使用示例

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

**响应示例**

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

如果任务没有剩余 work，响应中的状态可以直接为 `cancelled`。否则继续通过 `get_task()` 查询，直到状态变为 `cancelled`。

**错误处理**：
- `NOT_FOUND`（404）：任务不存在、已过期或不属于当前用户
- `PERMISSION_DENIED`（403）：ROOT 身份请求取消任务
- `FAILED_PRECONDITION`（412）：任务类型不支持取消，或任务已经 `completed`/`failed`

---

### list_tasks()

#### 1. API 实现介绍

列出当前调用方可见的后台任务，支持按类型、状态、资源过滤。

**代码入口**：
- `openviking/server/routers/tasks.py:list_tasks()` - HTTP 路由
- `sdk/python/openviking_sdk/client.py:AsyncHTTPClient.list_tasks()` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_type | str | 否 | None | 按任务类型过滤，例如 `session_commit` |
| status | str | 否 | None | 按任务状态过滤：`pending`、`running`、`cancelling`、`completed`、`failed`、`cancelled` |
| resource_id | str | 否 | None | 按资源 ID 过滤，例如会话 ID |
| limit | int | 否 | 50 | 最多返回的任务条数 |

#### 3. 使用示例

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
# 列出任务
ov task list

# 按任务类型和状态过滤
ov task list --task-type session_commit --status running
```

**响应示例**

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

## 相关文档

- [会话](05-sessions.md) - 会话提交任务
- [资源](02-resources.md) - 资源导入任务
- [内容](12-content.md) - 异步 reindex 任务
