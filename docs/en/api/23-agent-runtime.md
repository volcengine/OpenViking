# Agent Runtime API

OpenViking owns Compile configuration, task submission, snapshots and recovery. The configured Agent Runtime executes the resolved request; the bundled VikingBot implements this protocol for local deployments.

## Compile configuration and execution

A caller-provided `compile_name` identifies saved configuration and its successful incremental baseline. Names contain 1–64 ASCII letters, digits, underscores, or hyphens, are case-sensitive, and are unique for each user within an account. Whitespace is rejected rather than trimmed. The first character cannot be a hyphen. Different users or accounts can use the same name. A `task_id` identifies one accepted execution and uses the `cmp_` prefix. Multiple configurations can share the same `to`; each configuration keeps its own history and baseline.

Ordinary Compile validates normalized directory URIs when creating or updating configuration and when submitting a new manual or scheduled execution:

- Each `from` must differ from `to`; neither can be an ancestor or descendant of the other.
- A directory tagged `compile_target=true` at any strict ancestor or descendant of `to` causes rejection. The tag on `to` itself permits reuse.
- `to` cannot be `viking://user/{user_id}/memories/experiences` or any of its descendants. The `~/` alias and trailing slashes do not bypass this restriction; similar names such as `experiences2` are not rejected by this rule.

Creation acquires an account-isolated, Compile-only virtual tree lease before checking visible index tags. Strict parent/child targets are rejected while tagging is pending; identical targets share independent holders and retain serialized execution. Lock contention waits up to one second, without waiting for Compile or indexing. The lease covers no actual output files and is handed to the existing tagging queue until tags are searchable or the target disappears. Leases can expire during service outages, leaving an admission gap before recovery; they are not permanent reservations. Index checks respect the current request's tenant and permissions. The independent `skill="memory"` consolidation mode without `from` does not apply these directory constraints or target-tagging steps.

### Create configuration

```http
POST /api/v1/compile/create
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `compile_name` | string | Yes | Caller-selected configuration name; returned unchanged |
| `from` | string[] | Yes | Non-empty source directory list |
| `to` | string | Yes | Output directory, writable by the caller and disjoint from the sources |
| `skill` | string | Yes | Readable Skill directory or its `SKILL.md` URI |
| `instruction` | string | No | Additional instructions; null uses the Skill's default behavior |
| `args` | object | No | Runtime arguments and optional `owner_user_id`, `auto_update` / `update_schedule` controls |

```bash
curl -X POST http://localhost:1933/api/v1/compile/create \
  -H 'Content-Type: application/json' -H 'X-API-Key: your-key' \
  -d '{
    "compile_name": "research-summary",
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://agent/skills/llm-wiki",
    "instruction": "Organize the evidence into a knowledge base.",
    "args": {
      "auto_update": true,
      "update_schedule": {"frequency": "daily", "hour": 10, "minute": 0}
    }
  }'
```

The response is `200 OK`:

```json
{"status":"ok","result":{"compile_name":"research-summary"}}
```

Missing or invalid `compile_name` is rejected; the server never generates a fallback name. Duplicate names, including concurrent creation attempts by the same user within one account, return a conflict without replacing configuration or its baseline. Different users can create configurations with the same name. Configuration names cannot be changed.

`args.owner_user_id` optionally selects the configuration owner within the current account; omission defaults to the authenticated user. Creation, updates, and named execution use this same parameter. It only selects configuration ownership or location: it does not change execution identity, grant data permissions, or enter saved Runtime arguments. `args.owner_id` does not select a configuration owner.

Creation synchronously validates inputs, permissions and visible directory tags, creates `to` if needed, and durably enqueues an independent internal tagging Task. It does not wait for directory indexes, embeddings or tag visibility. A file at `to` or failure to durably enqueue the internal task returns an error. A QueueFS consumer prepares the root directory index and applies `compile_target=true`, retrying failures without recursively tagging descendants or replacing existing content, summaries and other tags. If the target path no longer exists, tagging stops for that path without recreating it. Configuration creation does not execute Compile, create a run, or snapshot sources.

Scheduling controls are stored separately from Runtime arguments. A non-null schedule enables updates unless `auto_update=false` is explicit. A schedule requires `frequency` (`daily` or `weekly`) and uses Beijing time (`Asia/Shanghai`).

With only the frequency, the server assigns a fixed time between 00:00 and 03:59. Weekly schedules also get a fixed `weekday` (1=Monday through 7=Sunday) when omitted. Supplying only `hour` (0–23) assigns a fixed minute within that hour, so `hour: 10` runs between 10:00 and 10:59. Supplying only `minute` (0–59) sets the hour to zero. Read the configuration's `update_schedule` to see the assigned values.

Scheduled execution requires `compile_api.scheduler_enabled=true` and the configured Redis expiry service. Credentials are encrypted and revalidated, including current group permissions, at each trigger. Disabled or unavailable scheduling returns an error; creation never submits an execution as a fallback. A disabled schedule has `next_triggered_slot: null`.

### Execute Compile

```http
POST /api/v1/compile
```

This endpoint accepts exactly one of these request shapes:

```json
{"compile_name":"research-summary"}
```

```json
{
  "from":["viking://resources/research"],
  "to":"viking://resources/research-wiki",
  "skill":"viking://agent/skills/llm-wiki",
  "instruction":"Organize the evidence into a knowledge base.",
  "args":{}
}
```

Passing `compile_name` when executing Compile reuses the configuration's saved `from`, `to`, `skill`, and `instruction`.

Submitting `from`, `to`, and a Skill URI directly is deprecated. Accepted requests include the deprecation date and migration guide in the `Deprecation` and `Link` response headers. Send the inputs and a `compile_name` to `POST /api/v1/compile/create` once, then send only `compile_name` to `POST /api/v1/compile` for each run.

A full-input request always performs a one-off full compile, including when the same output has saved configurations. It does not read or write saved configuration/run files and does not snapshot sources. Target start/finish snapshots, cancellation and recovery still apply. `skill="memory"` remains an independent in-place consolidation mode without `from`; it cannot be saved or scheduled through the creation endpoint.

Both ordinary execution forms validate the target, create its directory when needed, and durably enqueue independent tagging before submitting the Compile execution task. Background index or tag delays do not block submission, output writes or Compile finalization, and tagging failures do not fail Compile. Acceptance does not mean the tag is searchable; the virtual lease protects pending targets as described above. Explicit start/finish snapshots, output `snapshot_id`, Outputs and rollback retain their existing semantics. The tag is a directory retrieval marker; task completion, failure or cancellation does not automatically remove it.

Scheduling controls are rejected by the execution endpoint. `args.mode`, `last_compile_time`, `changes`, and `operation_id` are not public execution controls. OpenViking owns the execution plan.

Every accepted execution returns `202 Accepted` with the ordinary TaskRecord:

```json
{
  "status":"ok",
  "result":{
    "task_id":"cmp_0123456789abcdef",
    "task_type":"compile",
    "status":"pending",
    "stage":"queued",
    "resource_id":"viking://resources/research-wiki"
  }
}
```

Use `Idempotency-Key` for execution retries and `/api/v1/tasks/{task_id}` for status or cancellation. Manual execution uses the current caller's identity and connection; it does not borrow the schedule's credentials.

If a source directory has been deleted or moved, a new execution returns HTTP `404` / `NOT_FOUND` before creating a task. The error identifies the missing Viking URI with `details.type="compile source directory"` and asks the caller to update the configuration's source directories before retrying. Permission errors remain separate. Recovering an existing submission with the same `Idempotency-Key` does not revalidate its source directories.

### CLI and SDKs

```bash
ov compile create --compile-name research-summary --from viking://resources/research \
  --to viking://resources/research-wiki --skill viking://agent/skills/llm-wiki \
  --args '{"auto_update":true,"update_schedule":{"frequency":"daily","hour":10}}'
ov compile --compile-name research-summary
```

`--args` is a JSON object. Creation requires `--compile-name` and returns that configuration name; either execution form returns a task ID.

::: code-group

```python [Python]
created = client.create_compile_task(
    ["viking://resources/research"],
    "viking://resources/research-wiki",
    "viking://agent/skills/llm-wiki",
    compile_name="research-summary",
)
task = client.compile(compile_name=created["compile_name"])
```

```ts [TypeScript]
const created = await client.createCompileTask({
  compile_name: "research-summary",
  from: ["viking://resources/research"],
  to: "viking://resources/research-wiki",
  skill: "viking://agent/skills/llm-wiki",
});
const task = await client.compile({ compile_name: created.compile_name });
```

```go [Go]
created, err := client.CreateCompileTask(ctx, "research-summary", openviking.CompileInput{
    FromURIs: []string{"viking://resources/research"},
    To: "viking://resources/research-wiki",
    Skill: "viking://agent/skills/llm-wiki",
})
if err != nil { return err }
task, err := client.Compile(ctx, openviking.CompileRequest{CompileName: created["compile_name"].(string)})
```

:::

The Python example uses `SyncHTTPClient`; use `await` with `AsyncHTTPClient`.

### Configuration and run history

Configuration lookups and run queries use the current account and user. Updates and named executions can select another configuration owner in the same account through `args.owner_user_id`; without it they use the current user. A name absent for the selected owner returns not found. Configuration and run reads also require access to the output; editing or executing requires the authenticated caller's current write permission. Lists include only the current user's readable configurations. They do not merge configurations sharing one output directory.

| HTTP API | Result |
|----------|--------|
| `GET /api/v1/compile/config?compile_name={compile_name}` | Public saved inputs, scheduling and invocation times |
| `POST /api/v1/compile/config/update` | Replace configuration identified by body `compile_name` |
| `GET /api/v1/compile/configs` | `configs` and `next_cursor`, ordered lexically by configuration name |
| `GET /api/v1/compile/runs?compile_name={compile_name}` | `runs` and `next_cursor`, newest first by `created_at` and `task_id` |
| `GET /api/v1/compile/runs/{task_id}?compile_name={compile_name}` | One frozen run |
| `GET /api/v1/compile/runs/{task_id}/outputs?compile_name={compile_name}` | Changed output files derived from run snapshots; `outputs` and `next_cursor` |

List endpoints accept `limit` (1–100, default 20) and an optional `cursor`. Pass `next_cursor` unchanged; null ends pagination. Output items contain `uri`, `snapshot_id`, `latest_snapshot_id` and `change_type`. `snapshot_id` is the frozen run snapshot; `latest_snapshot_id` is the file’s latest change commit at query time (null if no history). It may identify a later deletion, in which case reading that version returns 404. History errors propagate; only returned page items require history reads.

Configuration updates require `compile_name`, `from`, `skill`, and top-level `auto_update`; optional `instruction` and `args` replace the saved values. The optional `to` field preserves the saved output directory when omitted or null. When supplied, its normalized value must match the configuration's saved output directory. Changing the output directory returns `400 INVALID_ARGUMENT` and leaves the configuration unchanged; other inputs such as `from` and `skill` remain editable. Omitting or passing null for top-level `update_schedule` preserves the saved plan. Enabling updates requires a supplied or saved plan. Disabling updates preserves the plan, clears execution credentials and the next trigger time, and unregisters scheduling; `auto_update` controls whether the plan is active. Do not place schedule controls in update `args`. Changing execution inputs invalidates the successful baseline; schedule-only changes preserve it. Updates apply the same directory constraints and durably enqueue independent tagging without waiting for tags. Updating configuration does not run Compile or reset invocation times.

At creation, `state.json` records the configuration owner’s user ID in the top-level `updated_by` field. On update, the caller can supply a top-level `updated_by` string. If omitted, the server records the authenticated user’s ID.

Private fields in nested `args` are redacted from public responses and encrypted for execution: unchanged public arguments preserve their private values, editing a nested subtree containing private fields requires resending those fields, and deleting the subtree removes its saved private values.

The two invocation times are Unix seconds:

- `task_first_create_time`: the first accepted execution task's `created_at`; null until first execution and then immutable.
- `last_run_time`: the most recently accepted new task's `created_at`. Failed or cancelled executions retain this time; idempotent replay does not advance it.

Manual and scheduled submissions both update these fields. `last_triggered_slot` and `next_triggered_slot` belong only to scheduling; manual execution does not advance them.

A saved run freezes `task_id`, `actor`, `status`, `created_at`, `started_at`, `finished_at`, `params`, `result`, `error`, and `meta`. `params` contains the full public inputs and scheduling configuration accepted for that run. `result` contains `created`, `updated`, and `deleted` counts. Its metadata is limited to:

| Field | Meaning |
|-------|---------|
| `trigger` | `initial` for the configuration's first accepted run, regardless of trigger source or execution outcome; subsequent runs use `manual` or `scheduled` |
| `mode` | Actual strategy, `full` or `incremental` |
| `base_snapshot` | Successful source baseline used for this run, or null for a full calculation |
| `start_snapshot` | Frozen source and target state at execution start |
| `finish_snapshot` | Retained target state after publication or completed rollback; null until committed |
| `skill_snapshot_id` | Skill snapshot, or null for a system Skill |

No-op runs may reuse the start snapshot. Failure and cancellation do not advance the successful source baseline. Execution plans and necessary recovery checkpoints remain private Task state; public configuration, runs and tasks never expose execution credentials.

The only Compile business files are:

```text
/local/{account_id}/_system/compile/tasks/{user_id}/{configuration_directory}/state.json
/local/{account_id}/_system/compile/tasks/{user_id}/{configuration_directory}/runs/{task_id}.json
```

`configuration_directory` is the lowercase hexadecimal encoding of the name's ASCII bytes (`compile_name.encode("ascii").hex()`), with no prefix. This preserves case-sensitive names even on case-insensitive storage. Public APIs receive and return the original `compile_name`, not its directory encoding.

Configurations are stored in account and user directories. Public APIs locate configurations by their original `compile_name`.

State contains exactly `from`, `to`, `skill`, `instruction`, `args`, `auto_update`, `update_schedule`, `task_first_create_time`, `last_run_time`, `last_triggered_slot`, `next_triggered_slot`, `conflict_num`, `progress`, `updated_by`, and `execution_context`. `progress` stores the successful incremental baseline; `execution_context` stores internal execution context and encrypted arguments.

`conflict_num` counts files tagged `has_conflict=true` that the execution identity can retrieve under `to` at finalization, including descendants. It does not count individual conflicts inside those files. One `find` call uses an empty query, the tag filter, `level=2`, and `limit=100`, without pagination. Counts at or above 100 are stored as 100; more conflicting files may exist. Successful runs count after output snapshot finalization without waiting for target tagging; failed or cancelled runs count after rollback completes. The field starts as null. Runs without a start snapshot skip retrieval and record null because another task may be modifying the target. Rollback index rebuilding is awaited for at most 30 seconds. Unavailable indexes or retrieval failures log a warning and record null without rolling back successfully generated output. Configuration updates preserve the count when `to` is unchanged and reset it to null when `to` changes.

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

A running Compile task enters `cancelling` until execution stops and required cleanup completes. Recovery uses the frozen target start snapshot and only affects `to`; source directories are not restored. Publication or rollback must finish before final state convergence, and failures or cancellation do not advance the successful incremental baseline. Repeated cancellation of an already cancelled task is idempotent.

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

Both `task_type` and `payload` are required. Only `task_type="compile"` is supported. The `payload` always contains resolved `from`, `to`, `skill`, optional `instruction` and Runtime `args`, and the server-owned execution plan. Runtime does not resolve `compile_name` or receive schedule controls. A one-off full plan does not require a source snapshot. Unsupported types or invalid payloads return `4xx` without creating an execution task.

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

### Archive an execution task

```http
POST /runtime/v1/tasks/archive
```

The request contains the required `session_id` and optional original task `args`, using the same authentication and task ownership checks as status requests. The endpoint is a no-op placeholder: it returns `200 OK` with `{}` without modifying or deleting the task, and can be called repeatedly.

OV calls this endpoint when a Compile Runtime session reaches `completed`, `failed`, or `cancelled`. Tasks without a Runtime session do not trigger archival. Archival is best effort; a failed archive request does not change the task's terminal state.

## Related documentation

- [Background Tasks](17-tasks.md) - generic task inspection, cancellation, and listing
- [Context Compilation](../context-compilation/01-overview.md) - Compile scenarios and examples
- [Skills API](04-skills.md) - managing the Skills used by Compile
