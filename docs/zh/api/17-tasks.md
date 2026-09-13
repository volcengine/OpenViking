# 后台任务

任务 API 用于跟踪资源导入、会话提交、索引维护和快照恢复等异步操作。

推荐先提交操作、保存返回的 `task_id`，再通过独立请求查询状态。收到任务 ID 只表示任务已提交；状态为 `completed` 才表示处理成功。`pending`、`running`、`cancelling` 都不是终态。停止轮询不会取消后台任务。

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
| include_events | bool | 否 | false | 在 HTTP 响应中包含持久化的执行事件 |

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

**执行事件记录（HTTP）**

请求 `GET /api/v1/tasks/{task_id}?include_events=true`，即可获得额外的 `result.execution_events` 字段。默认详情响应和任务列表不包含此字段。事件沿用任务记录的权限和保留策略。

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

| 事件类型 | 记录的事实 |
|----------|------------|
| `created` | TaskTracker 创建了任务记录 |
| `status_changed` | TaskTracker 接受了新的任务状态，包括取消过程和终态 |
| `stage_changed` | 执行路径在任务活跃期间上报了不同的阶段 |
| `error_recorded` | TaskTracker 接受了任务的首个脱敏错误 |
| `waiting_for_descendants` | 等待路径发现任务仍有未结束的队列工作，已排除自身 work ID |

`recorded_at` 是 TaskTracker 记录事件的 UTC 时间，不是浏览器轮询时间，也不一定是底层异常发生的时间。事件与任务状态在同一次写入成功后才对外发布。`seq` 确定任务内的事件顺序，即使多个事件时间相同也不会混淆。`stage` 表示任务最后上报的阶段；并行工作可能在其他阶段产生错误。`operation` 存在时标识上报事件的工作项。错误沿用现有脱敏和长度限制；这里不包含完整组件日志或 Python 堆栈。

每个任务最多保留 64 条事件，序列化事件数据不超过 32 KiB。超限时优先删除较早事件，`dropped_count` 记录删除数量，保留事件的序号不重置。事件随任务过期清理。旧任务返回 `execution_events: null`；若旧的活跃任务后来产生事件，则 `started_mid_task` 为 true，不会重建此前的历史。旧版本服务即使收到参数也可能不返回该字段。Studio 会说明这些情况，并保留任务元数据、结果和错误展示。

扩展执行事件时，在 `openviking/service/task_events.py` 注册事件类型、补充 Studio 翻译，然后在实际执行点调用 `await tracker.record_event(task_id, kind, account_id=..., user_id=..., operation=...)`。这个内部接口不修改任务状态或阶段，只接受有长度限制的操作标识，不接受任意日志内容。现有生命周期方法会自动记录它们接受的状态变化。

持久化任务文件新增 `execution_events` 字段。回滚目标需要具备未知任务字段的保留能力（提交 `a5166386` 或之后的版本）；更早的读取实现可能拒绝这些文件。回滚期间也会停止上报事件，因此跨降级执行的任务历史可能不完整。

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
| include_internal | bool | 否 | false | 是否包含 Connector 导入产生的内部子任务 |
| limit | int | 否 | 50 | 最多返回的任务条数 |

默认仅返回用户可见任务；排查 Connector 导入时可传 `include_internal=true` 查看其内部 `add_resource` 子任务。

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
