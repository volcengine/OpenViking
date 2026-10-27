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
| `instruction` | string | No | Skill-driven default | Additional instructions for this Compile run |
| `args` | object | No | - | Execution backend extensions; `model_name` accepts a model endpoint ID |

The entire `args` object is optional, and the model endpoint ID is not a top-level field. Use `args.model_name` to select a model; when omitted, the execution backend uses its default model configuration.

The model-selection examples below require a backend that accepts `args.model_name`. When using bundled VikingBot, omit `args` and CLI `--args`; it rejects non-empty arguments. The Python example uses `SyncHTTPClient`; await `compile()` with `AsyncHTTPClient`.

**HTTP API**

```http
POST /api/v1/compile
```

```bash
curl -X POST http://localhost:1933/api/v1/compile \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "Idempotency-Key: studio-compile-001" \
  -d '{
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://user/default/skills/research-compiler",
    "instruction": "Track the historical progress and preserve supporting evidence.",
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
  --instruction "Track the historical progress and preserve supporting evidence." \
  --args '{"model_name":"your-model-endpoint-id"}'
```

`--args` must be a JSON object. The command returns a task ID immediately after submission.

**SDKs**

The Python, TypeScript, and Go SDKs pass `instruction` and `args` through their Compile options:

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

### Target settings and operation history

These endpoints use the current account and authentication context. `to` is the registered target's `viking://` URI. Reading or updating one target requires write access; listing targets returns only accessible targets.

| HTTP API | Description and result (`status: "ok"`) |
|----------|------------------------|
| `GET /api/v1/compile/config?to={to}` | `result` contains target settings and run status |
| `POST /api/v1/compile/config/update` | Required JSON body field `to` identifies the registered Compile output directory to configure; `result` contains the updated settings and run status |
| `GET /api/v1/compile/configs` | Optional query parameters: `limit` (1–100, default 20) and `cursor`; `result.targets` contains this page of visible target summaries ordered by `target_id` ascending; pass `result.next_cursor` unchanged as the next page's `cursor`; null marks the last page |
| `GET /api/v1/compile/operations?to={to}` | `result.operations` contains operations, newest first; `result.next_cursor` continues pagination |
| `GET /api/v1/compile/operations/{operation_id}/outputs?to={to}` | `result.outputs` contains output files and frozen version references, ordered by URI; `result.next_cursor` continues pagination |

The update body requires `to`, `from` (a non-empty source URI array), `skill`, and `auto_update` (boolean). When `auto_update=true`, a valid `update_schedule` is required. When `auto_update=false`, the schedule can be omitted or null; the saved rule is cleared and the returned value is null. New targets have no default rule, and enabling updates after disabling them requires a rule. Optional `instruction` preserves the saved value when omitted; `null` or an empty string clears it. Updating settings does not submit a Compile task.

`update_schedule` requires `frequency` (`daily` or `weekly`). Optional `hour` (0–23) and `minute` (0–59) default to 0. Weekly schedules also accept `weekday` (1=Monday through 7=Sunday, default 1). Schedules use `Asia/Shanghai` time.

Scheduled updates require `compile_api.scheduler_enabled=true` (default `false`) and valid `expiry.service_name` and `expiry.redis` configuration. `compile_api.scheduler_backend` only accepts `redis` (the default); `local` is invalid. Enabling `auto_update` while scheduling is disabled returns `COMPILE_SCHEDULER_DISABLED`. Manual incremental Compile and version management do not require Redis.

Both history endpoints accept optional query parameters `limit` (1–100, default 20) and `cursor`. Pass the returned `next_cursor` unchanged; `null` marks the last page. `operation_id` is 1–128 characters, starts with a letter or digit, and otherwise allows letters, digits, `:`, `.`, `_`, and `-`. Operations remain queryable after target deletion. Missing targets, operations, or output manifests return `404`. A null `version_id` or `snapshot_id` on an output item indicates that the corresponding reference is unavailable.

### Check Compile availability

```http
GET /api/v1/compile/capabilities
```

Uses the current authentication context and takes no request parameters. Returns `200 OK`:

```json
{
  "status": "ok",
  "result": {
    "configured": true,
    "can_create": true,
    "reason_code": null
  }
}
```

| Field | Meaning |
|-------|---------|
| `configured` | Whether a Compile execution endpoint is configured |
| `can_create` | Whether the current authentication context permits submission to that endpoint |
| `reason_code` | `NOT_CONFIGURED` when no endpoint is configured; `API_KEY_REQUIRED` when a remote endpoint requires a forwardable OV API key; otherwise `null` |

An unavailable configuration is reported in the result with `can_create: false`, not as an HTTP error. This checks configuration and credentials; it does not probe backend health or validate a particular Compile request.

### Compile routing embeddings

`POST /api/v1/compile/embeddings` embeds transient routing texts using the current authentication context, without indexing or retaining them.

Body: required `target_uri` (an existing resource or Skill namespace target with read access); optional `texts` (string array, default `[]`, at most 32 texts, each 1–1024 characters and not whitespace-only); optional `expected_model` (model fingerprint; a non-empty value must match the current model).

Returns `200 OK` with `status: "ok"` and `result: {"model": "...", "vectors": [...]}`. Vectors follow input order; empty `texts` returns only the model fingerprint and an empty vector array.

### Find a task by submission key

```http
GET /api/v1/compile/submissions/{key}
```

Use this endpoint when a task creation response was lost or timed out. Pass the same key that was sent in the optional `Idempotency-Key` header of `POST /api/v1/compile`.

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `key` | Path | string | Yes | 16–128 characters; only letters, digits, `:`, `.`, `_`, and `-` are allowed |

```bash
curl http://localhost:1933/api/v1/compile/submissions/studio-compile-001 \
  -H "X-API-Key: your-key"
```

Returns `200 OK` with `status: "ok"` and the existing OV task record in `result`, using the same structure as the task creation response above. Lookup is scoped to the current account and user and does not create a task. A missing submission, including a key used only by another user, returns `404`; an invalid key returns `400 INVALID_ARGUMENT`.

To retry creation safely, reuse the same `Idempotency-Key` and request parameters. A key reused with different parameters returns `409`. Without this header, creation does not provide a submission key for this lookup.

### Get task status

For ordinary user requests, a task is visible only to its creator; missing tasks and tasks owned by another user return `404`. ROOT queries have separate administrative visibility and can cancel any task that exists.

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
    "instruction": "Organize the sources into a knowledge base.",
    "args": {"model_name": "your-model-endpoint-id"}
  }
}
```

Both `task_type` and `payload` are required. Only `task_type="compile"` is supported. The `payload` uses the task creation fields and validation rules described on this page; `instruction` and `args` are optional. Bundled VikingBot does not support non-empty `args`. Unsupported types or invalid payloads return `4xx` without creating an execution task.

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
