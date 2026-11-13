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
| `args` | object | No | - | Execution backend extensions and Compile scheduling controls |

The scheduling controls `auto_update` and `update_schedule` are handled by OpenViking. The Python example uses `SyncHTTPClient`; await `compile()` with `AsyncHTTPClient`.

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
    "instruction": "Track the historical progress and preserve supporting evidence."
  }'
```

Without scheduling controls, the endpoint immediately queues a Compile task and returns `202 Accepted` with an OV task record:

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

When `args` explicitly contains `auto_update` or `update_schedule`, the request only saves target settings and registers or removes the schedule. It returns `200 OK` with the saved configuration and `next_run_at`, without a `task_id`. No Compile task or Runtime call occurs until the next scheduled time. For example:

```json
{
  "from": ["viking://resources/research"],
  "to": "viking://resources/research-wiki",
  "skill": "viking://user/default/skills/research-compiler",
  "args": {
    "auto_update": true,
    "update_schedule": {"frequency": "daily", "hour": 0, "minute": 0}
  }
}
```

A non-null `update_schedule` enables automatic updates unless `auto_update=false` is explicit. Disabling updates returns `next_run_at: null`. A request without either scheduling control still compiles immediately, including targets that already have a saved schedule. Schedule saves update the target by `to`; they do not create a task submission for `GET /api/v1/compile/submissions/{key}`.

**CLI**

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://user/default/skills/research-compiler \
  --instruction "Track the historical progress and preserve supporting evidence."
```

`--args` must be a JSON object. Immediate submissions return a task ID; schedule saves return the next run time.

**SDKs**

The Python, TypeScript, and Go SDKs pass `instruction` and `args` through their Compile options:

::: code-group

```python [Python]
task = client.compile(
    ["viking://resources/research"],
    "viking://resources/research-wiki",
    "viking://user/default/skills/research-compiler",
)
```

```ts [TypeScript]
const task = await client.compile(
  ["viking://resources/research"],
  "viking://resources/research-wiki",
  "viking://user/default/skills/research-compiler",
);
```

```go [Go]
task, err := client.Compile(
    ctx,
    []string{"viking://resources/research"},
    "viking://resources/research-wiki",
    "viking://user/default/skills/research-compiler",
    nil,
)
```

:::

### Target settings and operation history

These endpoints use the current account and authentication context. `to` is the registered target's `viking://` URI. Reading settings, operation history, operation details or outputs requires read access; updating settings requires write access. Listing targets returns only accessible targets.

| HTTP API | Description and result (`status: "ok"`) |
|----------|------------------------|
| `GET /api/v1/compile/config?to={to}` | `result` contains target settings and run status |
| `POST /api/v1/compile/config/update` | Required JSON body field `to` identifies the registered Compile output directory to configure; `result` contains the updated settings and run status |
| `GET /api/v1/compile/configs` | Optional query parameters: `limit` (1–100, default 20) and `cursor`; `result.targets` contains this page of readable active target summaries ordered by `sha256(to)` ascending, excluding deleted targets before pagination; `result.next_cursor` is the last returned target URI; pass it unchanged as the next page's `cursor` to continue after its hash; null marks the last page |
| `GET /api/v1/compile/operations?to={to}` | `result.operations` contains operations, newest first; `result.next_cursor` continues pagination |
| `GET /api/v1/compile/operations/{operation_id}?to={to}` | `result` contains one operation with the same fields as a history item, including `params` and public `meta`, excluding private execution checkpoints |
| `GET /api/v1/compile/operations/{operation_id}/outputs?to={to}` | `result.outputs` contains output files and frozen version references, ordered by URI; `result.next_cursor` continues pagination |

The update body requires `to`, `from` (a non-empty source URI array), `skill`, and `auto_update` (boolean). When `auto_update=true`, a valid `update_schedule` is required. When `auto_update=false`, the schedule can be omitted or null; the saved rule is cleared and the returned value is null. New targets have no default rule, and enabling updates after disabling them requires a rule. Optional `instruction` preserves the saved value when omitted; `null` or an empty string clears it. Updating settings does not submit a Compile task.

`update_schedule` requires `frequency` (`daily` or `weekly`). Optional `hour` (0–23) and `minute` (0–59) default to 0. Weekly schedules also accept `weekday` (1=Monday through 7=Sunday, default 1). Schedules use `Asia/Shanghai` time.

Scheduled updates require `compile_api.scheduler_enabled=true` (default `false`) and valid `expiry.service_name` and `expiry.redis` configuration. `compile_api.scheduler_backend` only accepts `redis` (the default); `local` is invalid. A schedule request synchronously rejects disabled or missing scheduling configuration with HTTP 412 (`COMPILE_SCHEDULER_DISABLED`), and unavailable Redis registration with HTTP 503. A failed schedule save does not submit a Compile task. Compile failures, including the first run, do not cancel subsequent scheduled runs. Manual incremental Compile and version management do not require Redis.

The history and output list endpoints accept optional query parameters `limit` (1–100, default 20) and `cursor`. Pass the returned `next_cursor` unchanged; `null` marks the last page.

An `operation_id` contains a fixed-width creation timestamp and a unique suffix. History pages follow descending operation ID order, and a cursor remains usable after its record is cleaned up. Compile operations link to execution tasks through `meta.task_id`; `operation_id` differs from `task_id`. Use `task_id` to query or cancel a task and the history item's `operation_id` to read operation details or outputs.

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

For ordinary user requests, a task is visible only to its creator; missing tasks and tasks owned by another user return `404`. ROOT queries have separate administrative visibility and cannot cancel tasks.

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

A running Compile task first enters `cancelling`, then becomes `cancelled` after the execution backend confirms cancellation and cleanup finishes. Before the output snapshot is committed, cancellation rolls files in the `to` subtree back to the snapshot taken when that Compile run started, including changes made there by other operations and child tasks during the run. Paths outside `to` are not restored. After the output snapshot is committed, failure or cancellation during index and tag finalization preserves the committed output; indexes or tags can remain incomplete. Repeated cancellation of an already `cancelled` task is idempotent.

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
    "instruction": "Organize the sources into a knowledge base."
  }
}
```

Both `task_type` and `payload` are required. Only `task_type="compile"` is supported. The `payload` uses the task creation fields and validation rules described on this page; `instruction` and `args` are optional. Unsupported types or invalid payloads return `4xx` without creating an execution task.

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
