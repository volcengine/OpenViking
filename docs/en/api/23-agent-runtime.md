# Agent Runtime API

Agent Runtime Server executes Agent tasks and currently supports Compile. Applications submit tasks through OpenViking's Compile API. OpenViking validates requests, persists tasks, and manages their lifecycle while calling the Runtime execution API. The bundled VikingBot implements the same execution protocol for local deployments.

**Code entry points**:

- `openviking/server/routers/compile.py` - Compile task creation
- `openviking/server/routers/tasks.py` - task inspection and cancellation
- `openviking/service/compile_service.py` - Runtime calls and task state convergence

## Compile task API

### Create a task

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `from` | string[] | Yes | - | One or more source directories |
| `to` | string | Yes | - | Target Resource or Memory directory, or a supported Skill namespace |
| `skill` | string | Yes | - | Skill directory or its `SKILL.md` URI |
| `reason` | string | No | Skill-driven default | Additional instructions for this Compile run |
| `args` | object | No | - | Execution backend extensions; `model_name` accepts a model endpoint ID |

The entire `args` object is optional, and the model endpoint ID is not a top-level field. Use `args.model_name` to select a model; when omitted, the execution backend uses its default model configuration.

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
    "reason": "Track the historical progress and preserve supporting evidence.",
    "args": {"model_name": "your-model-endpoint-id"}
  }'
```

The endpoint returns `202 Accepted` with an OV task record:

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
  --reason "Track the historical progress and preserve supporting evidence." \
  --args '{"model_name":"your-model-endpoint-id"}'
```

`--args` must be a JSON object. The command returns a task ID immediately after submission.

**SDKs**

The Python, TypeScript, and Go SDKs pass `reason` and `args` through their Compile options:

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

### Get task status

A task is visible only to the principal that created it. A missing task and a task owned by another principal both return `404`.

```http
GET /api/v1/tasks/{task_id}
```

```bash
ov task status cmp_01abc
```

Terminal task responses also contain the result or error.

### Cancel a task

```http
POST /api/v1/tasks/{task_id}/cancel
```

```bash
ov task cancel cmp_01abc
```

The task first enters `cancelling`, then becomes `cancelled` after in-process work and cleanup settle. Writes that already completed are not rolled back. Repeated cancellation of an already `cancelled` task is idempotent.

| Status | Typical stages |
|--------|----------------|
| `pending` | `queued` |
| `running` | Execution stage reported by the backend, such as `agent` or `writing` |
| `cancelling` | Settling in-process work and resource cleanup |
| `completed` | `completed`, `salvaged` |
| `failed` | Stage where the failure occurred; the response contains `error` |
| `cancelled` | `cancelled` |

### Legacy endpoints

The following legacy VikingBot proxy routes on OV are retired and return migration guidance only:

```http
POST /bot/v1/compile
GET /bot/v1/compile/{task_id}
POST /bot/v1/compile/{task_id}/cancel
```

Create tasks through `/api/v1/compile`; inspect and cancel them through `/api/v1/tasks/{task_id}`.

## Runtime execution API

The execution service configured by `compile_api.base_url` provides the following endpoints for OV. Applications submit tasks through the Compile API above.

### Create an execution task

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
    "reason": "Organize the sources into a knowledge base.",
    "args": {"model_name": "your-model-endpoint-id"}
  }
}
```

Both `task_type` and `payload` are required. Only `task_type="compile"` is supported. The `payload` uses the task creation fields and validation rules described on this page; `reason` and `args` are optional. Bundled VikingBot does not support non-empty `args`. Unsupported types or invalid payloads return `4xx` without creating an execution task.

OV forwards the current user's OV API key in `X-API-Key`. It also sends `X-Gateway-Token` when `compile_api.gateway_token` is configured. These credentials must not appear in public task results.

An accepted request returns `202 Accepted` with an execution identifier:

```json
{"session_id": "session-123"}
```

Repeated requests with the same `Idempotency-Key` from the same user must return the same `session_id` without executing again. Use `session_id` to inspect or cancel execution on the backend; applications use `task_id` to inspect their OV task.

### Inspect or cancel an execution task

```http
POST /runtime/v1/tasks/status
POST /runtime/v1/tasks/cancel
```

Both endpoints accept this request body:

```json
{"session_id": "session-123"}
```

Both return an execution status, for example:

```json
{
  "status": "running",
  "stage": "compile: agent",
  "error": null,
  "meta": {},
  "result": null
}
```

The `status` is one of `pending`, `running`, `cancelling`, `completed`, `failed`, or `cancelled`. Cancellation may return `cancelling` until execution has stopped and cleanup has finished, then return `cancelled`. Repeated cancellation of a terminal task returns its current terminal state. Both inspection and cancellation must enforce task ownership.

## Related documentation

- [Background Tasks](17-tasks.md) - generic task inspection, cancellation, and listing
- [Context Compilation](../context-compilation/01-overview.md) - Compile scenarios and examples
- [Skills API](04-skills.md) - managing the Skills used by Compile
