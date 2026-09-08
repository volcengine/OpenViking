# Agent Runtime API

Agent Runtime Server 负责执行 Agent 任务，当前支持 Compile。应用通过 OpenViking 的 Compile API 提交任务，OpenViking 负责校验请求、持久化任务和管理生命周期，再调用 Runtime 执行接口；内置 VikingBot 也实现了同一执行协议，可用于本地部署。

**代码入口**：

- `openviking/server/routers/compile.py` - 创建 Compile 任务
- `openviking/server/routers/tasks.py` - 查询和取消任务
- `openviking/service/compile_service.py` - Runtime 调用与任务状态收敛

## Compile 任务接口

### 创建任务

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `from` | string[] | 是 | - | 一个或多个来源目录 |
| `to` | string | 是 | - | 目标 Resource 或 Memory 目录，或受支持的 Skill namespace |
| `skill` | string | 是 | - | Skill 目录或其 `SKILL.md` URI |
| `reason` | string | 否 | Skill 驱动的默认值 | 本次 Compile 的补充指令 |
| `args` | object | 否 | - | 执行端扩展参数；`model_name` 可传模型 Endpoint ID |

`args` 整体可省略，模型 Endpoint ID 也不是顶层字段。需要指定模型时使用 `args.model_name`；不传时由执行端使用其默认模型配置。

**HTTP API**

```http
POST /api/v1/compile
```

```bash
curl -X POST http://localhost:1933/api/v1/compile \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://user/default/skills/research-compiler",
    "reason": "追踪历史进展，并保留支撑证据。",
    "args": {"model_name": "your-model-endpoint-id"}
  }'
```

接口返回 `202 Accepted` 和 OV TaskRecord：

```json
{
  "status": "ok",
  "result": {
    "task_id": "cmp_01abc",
    "task_type": "compile",
    "status": "pending",
    "stage": "queued",
    "resource_id": "viking://resources/research"
  }
}
```

**CLI**

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://user/default/skills/research-compiler \
  --reason "追踪历史进展，并保留支撑证据。" \
  --args '{"model_name":"your-model-endpoint-id"}'
```

`--args` 必须是 JSON object。命令提交后立即返回 Task ID。

**SDK**

Python、TypeScript 和 Go SDK 都通过各自的 Compile options 传递 `reason` 和 `args`：

::: code-group

```python [Python]
task = client.compile(
    ["viking://resources/research"],
    "viking://resources/research-wiki",
    "viking://user/default/skills/research-compiler",
    {"args": {"model_name": "your-model-endpoint-id"}},
)
```

```ts [TypeScript]
const task = await client.compile(
  ["viking://resources/research"],
  "viking://resources/research-wiki",
  "viking://user/default/skills/research-compiler",
  { args: { model_name: "your-model-endpoint-id" } },
);
```

```go [Go]
task, err := client.Compile(
    ctx,
    []string{"viking://resources/research"},
    "viking://resources/research-wiki",
    "viking://user/default/skills/research-compiler",
    &openviking.CompileOptions{
        Args: map[string]any{"model_name": "your-model-endpoint-id"},
    },
)
```

:::

### 查询任务

任务仅对创建它的 principal 可见；任务不存在或属于其他 principal 时均返回 `404`。

```http
GET /api/v1/tasks/{task_id}
```

```bash
ov task status cmp_01abc
```

任务进入终态后，响应还会包含结果或错误。

### 取消任务

```http
POST /api/v1/tasks/{task_id}/cancel
```

```bash
ov task cancel cmp_01abc
```

任务会先进入 `cancelling`，待当前进程内工作和清理完成后进入 `cancelled`；已经完成的写入不会回滚。重复取消已经 `cancelled` 的任务是幂等的。

| Status | 常见 Stage |
|--------|------------|
| `pending` | `queued` |
| `running` | 执行端返回的执行 Stage，例如 `agent`、`writing` |
| `cancelling` | 收敛当前进程内工作和清理资源 |
| `completed` | `completed`、`salvaged` |
| `failed` | 失败发生时的 Stage；响应包含 `error` |
| `cancelled` | `cancelled` |

### 旧接口

OV 上的以下旧 VikingBot 代理路由已经停用，只返回迁移提示：

```http
POST /bot/v1/compile
GET /bot/v1/compile/{task_id}
POST /bot/v1/compile/{task_id}/cancel
```

创建任务使用 `/api/v1/compile`，查询和取消统一使用 `/api/v1/tasks/{task_id}`。

## Runtime 执行接口

以下接口由 `compile_api.base_url` 指向的执行服务提供，供 OV 调用。应用通过前述 Compile API 提交任务。

### 创建执行任务

```http
POST /runtime/v1/tasks
Idempotency-Key: <OV task_id>
```

```json
{
  "task_type": "compile",
  "payload": {
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://agent/skills/wiki",
    "reason": "整理成知识库",
    "args": {"model_name": "your-model-endpoint-id"}
  }
}
```

`task_type` 和 `payload` 均必填。当前只支持 `task_type="compile"`；`payload` 使用本页创建任务的字段及校验规则，`reason`、`args` 可省略。内置 VikingBot 不支持非空 `args`。不支持的类型或无效的 payload 返回 `4xx`，不创建执行任务。

OV 通过 `X-API-Key` 传递当前用户的 OV API Key；配置 `compile_api.gateway_token` 时还会发送 `X-Gateway-Token`。这些凭证不能出现在公开任务结果中。

接受请求后返回 `202 Accepted`，响应包含执行端标识：

```json
{"session_id": "session-123"}
```

同一用户的相同 `Idempotency-Key` 必须返回同一个 `session_id`，不能重复执行。`session_id` 用于执行端查询和取消；应用查询 OV 任务时使用 `task_id`。

### 查询与取消执行任务

```http
POST /runtime/v1/tasks/status
POST /runtime/v1/tasks/cancel
```

两个接口均使用以下请求体：

```json
{"session_id": "session-123"}
```

两个接口均返回执行状态，例如：

```json
{
  "status": "running",
  "stage": "compile: agent",
  "error": null,
  "meta": {},
  "result": null
}
```

`status` 可为 `pending`、`running`、`cancelling`、`completed`、`failed` 或 `cancelled`。取消请求可以先返回 `cancelling`，实际执行停止并完成清理后再返回 `cancelled`；重复取消已结束任务返回其当前终态。查询和取消均须校验任务归属。

## 相关文档

- [后台任务](17-tasks.md) - 通用任务查询、取消和列表接口
- [上下文编译](../context-compilation/01-overview.md) - Compile 使用场景和示例
- [Skills API](04-skills.md) - 管理 Compile 使用的 Skill
