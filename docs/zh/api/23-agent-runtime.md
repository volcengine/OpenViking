# Agent Runtime API

OpenViking 管理 Compile 配置、执行任务、快照和恢复。配置的 Agent Runtime 接收完整执行请求；内置 VikingBot 实现相同协议，用于本地部署。

## Compile 配置与执行

`compile_name` 由调用者提供，标识持久配置及其成功增量基线。名称由 1–64 位 ASCII 字母、数字、下划线或连字符组成，首字符不能是连字符，区分大小写，在同一 account 的同一用户内唯一；不同用户或不同 account 可以使用相同名称。空白字符直接拒绝，不会自动去除。`task_id` 标识一次已受理的执行，使用 `cmp_` 前缀。同一个配置可以多次执行；多个配置可以指向同一个 `to`，各自维护独立基线和历史。

普通 Compile 在创建或更新配置、手动或定时提交新执行时，统一校验规范化后的目录 URI：

- 每个 `from` 都不能与 `to` 相同，也不能互为祖先或后代。
- `to` 的任何严格祖先或后代目录带有 `compile_target=true` 时拒绝请求；`to` 自身已有该 tag 时允许复用。
- `to` 不能是 `viking://user/{user_id}/memories/experiences` 或其任意层级子目录。`~/` 别名和末尾斜杠不能绕过限制；`experiences2` 等相似名称不被这条规则拒绝。

创建时先取得按账号隔离的 Compile 专用虚拟树锁，再检查可见索引标签。补标期间，严格父子目标拒绝创建；相同目标共享独立持有凭证，执行仍按现有流程串行处理。锁竞争最多等待 1 秒，不等待 Compile 或索引完成。锁不覆盖真实产物路径，由现有补标队列接管，直到 tag 可查询或目标路径消失。服务中断期间租约可能过期，恢复前仍可能出现受理空窗；租约不提供永久占位。索引检查遵循当前请求的租户和权限范围。不传 `from` 的独立 `skill="memory"` 就地整理模式不应用这些目录约束或目标打标流程。

### 创建配置

```http
POST /api/v1/compile/create
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `compile_name` | string | 是 | 调用者指定的配置名称，原样返回 |
| `from` | string[] | 是 | 非空来源目录列表 |
| `to` | string | 是 | 调用者可写、与来源不重叠的输出目录 |
| `skill` | string | 是 | 可读的 Skill 目录或其 `SKILL.md` URI |
| `instruction` | string | 否 | 补充指令；null 使用 Skill 默认行为 |
| `args` | object | 否 | Runtime 参数，可附带 `owner_user_id` 和 `auto_update` / `update_schedule` 控制 |

```bash
curl -X POST http://localhost:1933/api/v1/compile/create \
  -H 'Content-Type: application/json' -H 'X-API-Key: your-key' \
  -d '{
    "compile_name": "research-summary",
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://agent/skills/llm-wiki",
    "instruction": "整理成保留证据引用的中文知识库。",
    "args": {
      "auto_update": true,
      "update_schedule": {"frequency": "daily", "hour": 10, "minute": 0}
    }
  }'
```

返回 `200 OK`：

```json
{"status":"ok","result":{"compile_name":"research-summary"}}
```

缺少 `compile_name` 或名称非法时直接报错，服务端不会生成备用名称。同一 account 的同一用户重名创建（包括并发创建）返回冲突，不覆盖配置或增量基线；不同用户可以创建同名配置。配置名称不支持修改。

`args.owner_user_id` 可选，用于选择当前 account 内的配置归属用户，省略时使用当前认证用户。创建、更新和按名称执行统一使用该参数；它只决定配置归属或定位，不改变执行身份、不授予数据访问权限，也不进入保存的 Runtime 参数。`args.owner_id` 不再用于选择配置归属用户。

创建接口同步校验输入、权限及可见的目录标签，必要时创建 `to`，并持久入队独立的内部补标 Task；不等待目录索引、embedding 或 tag 可见性。`to` 是文件或内部任务未能持久入队时返回错误。QueueFS 消费者负责根目录索引和 `compile_target=true` 补齐，失败后重试，不递归标记子目录，并保留已有内容、摘要和其他 tag。目标路径不存在时停止该路径的补标，不重建目录。创建配置本身不执行 Compile、不创建 run、不拍来源快照。

调度控制与 Runtime 参数分开保存。非空计划默认开启自动更新，除非显式指定 `auto_update=false`。计划必填 `frequency`（`daily` 或 `weekly`），使用北京时间（`Asia/Shanghai`）。

只传频率时，服务端在 00:00–03:59 中为计划分配固定时间。每周计划省略 `weekday` 时也会分配固定执行日（1 为周一，7 为周日）。只传 `hour`（0–23）时，在该小时内分配固定分钟，例如 `hour: 10` 对应 10:00–10:59。只传 `minute`（0–59）时，小时取 0。查询配置的 `update_schedule` 可查看分配结果。

定时执行要求开启 `compile_api.scheduler_enabled=true` 并配置 Redis expiry 服务。凭据加密保存，每次触发时重新验证凭据及当前用户组权限。调度未启用或不可用时返回错误，不会退化为立即执行。禁用计划后 `next_triggered_slot` 为 null。

### 执行编译

```http
POST /api/v1/compile
```

只接受以下两种互斥请求：

```json
{"compile_name":"research-summary"}
```

```json
{
  "from":["viking://resources/research"],
  "to":"viking://resources/research-wiki",
  "skill":"viking://agent/skills/llm-wiki",
  "instruction":"整理成保留证据引用的中文知识库。",
  "args":{}
}
```

执行 Compile 时传入 `compile_name`，会复用该配置中已保存的 `from`、`to`、`skill` 和 `instruction`。

直接提交 `from`、`to` 和 Skill URI 的写法已弃用。受理时，`Deprecation` 和 `Link` 响应头提供弃用日期和迁移指南。请先将这些输入连同 `compile_name` 提交到 `POST /api/v1/compile/create`，再向 `POST /api/v1/compile` 仅提交 `compile_name`。

传完整输入时始终执行一次性全量编译，即使同一输出目录已有持久配置，也不查找、复用或更新配置。不读写 compile 配置和 run 文件、不拍来源快照；目标开始/结束快照、取消和失败恢复仍然执行。`skill="memory"` 保留不传 `from` 的独立就地整理行为，不支持通过创建接口保存配置或开启定时执行。

两种普通执行形式都先完成目标校验、必要的目录创建及独立补标任务持久入队，再提交 Compile 执行任务。后台索引或 tag 延迟不阻塞 Compile 提交、产物写入或收尾，也不使 Compile 因补标失败而失败。受理成功不代表 tag 已可查询，补标期间由上述虚拟树锁保护目标。Compile 的显式开始/结束快照、产物 `snapshot_id`、Outputs 和回滚流程保持原有语义。该 tag 是目录检索标记，任务完成、失败或取消不会自动移除它。

执行接口拒绝调度控制。`args.mode`、`last_compile_time`、`changes` 和 `operation_id` 不是公开执行参数；执行计划由 OpenViking 生成。

每次成功受理均返回 `202 Accepted` 和通用 TaskRecord：

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

执行重试可使用 `Idempotency-Key`；查询和取消使用 `/api/v1/tasks/{task_id}`。手动执行使用当前调用者的权限和连接，不借用保存的调度凭据。

源目录被删除或移动后，新执行在创建任务前返回 HTTP `404` / `NOT_FOUND`。错误标明缺失的 Viking URI，`details.type="compile source directory"`，并提示修改配置中的源目录后重试；权限错误保持独立。使用相同 `Idempotency-Key` 恢复已有提交时，不重新校验源目录。

### CLI 与 SDK

```bash
ov compile create --compile-name research-summary --from viking://resources/research \
  --to viking://resources/research-wiki --skill viking://agent/skills/llm-wiki \
  --args '{"auto_update":true,"update_schedule":{"frequency":"daily","hour":10}}'
ov compile --compile-name research-summary
```

`--args` 是 JSON 对象。创建必须传入 `--compile-name` 并返回该配置名称；两种执行方式都返回任务 ID。

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

Python 示例使用 `SyncHTTPClient`；使用 `AsyncHTTPClient` 时添加 `await`。

### 配置与运行历史

配置查询及运行历史按当前 account 和用户定位；更新和按名称执行可通过 `args.owner_user_id` 选择同一 account 内的其他配置归属用户，省略时使用当前用户。指定用户下不存在该名称时返回不存在。读取配置和 run 还需要目标读取权限，修改或执行需要当前认证用户的写权限。列表只返回当前用户的可读配置，不合并指向相同输出目录的多个配置。

| HTTP API | 结果 |
|----------|------|
| `GET /api/v1/compile/config?compile_name={compile_name}` | 公开配置输入、调度与调用时间 |
| `POST /api/v1/compile/config/update` | 按请求体 `compile_name` 替换配置 |
| `GET /api/v1/compile/configs` | `configs` 和 `next_cursor`，按配置名称的字典序排序 |
| `GET /api/v1/compile/runs?compile_name={compile_name}` | `runs` 和 `next_cursor`，按 `created_at`、`task_id` 从新到旧排序 |
| `GET /api/v1/compile/runs/{task_id}?compile_name={compile_name}` | 本次执行冻结的 run |
| `GET /api/v1/compile/runs/{task_id}/outputs?compile_name={compile_name}` | 由本次快照推导的变更产出，包含 `outputs` 和 `next_cursor` |

列表接受 `limit`（1–100，默认 20）及可选 `cursor`。原样传递 `next_cursor`；null 表示结束。产出项包含 `uri`、`snapshot_id`、`latest_snapshot_id`、`change_type`。`snapshot_id` 固定为本次 run 的产出快照；`latest_snapshot_id` 为查询时该文件最近变更的 commit，无历史时为 null。最近变更可能是后续删除，此时读取该版本正文返回 404。仅查询本页产出的文件历史，查询失败直接报错。

配置更新必填 `compile_name`、`from`、`skill` 和顶层 `auto_update`，可选 `instruction`、`args` 替换已保存值。`to` 可选，省略或为 null 时沿用该配置已保存的目标目录；显式传入的 `to` 规范化后必须与已保存的目标目录一致，修改目标目录返回 `400 INVALID_ARGUMENT`，配置保持不变；`from`、`skill` 等其他参数仍可修改。顶层 `update_schedule` 省略或为 null 时保留已保存的计划。启用自动更新需要本次提供或已保存的计划；关闭时保留计划，清除执行凭据和下次触发时间，并取消调度注册，是否启用计划由 `auto_update` 决定。更新请求的 `args` 不接受调度控制。执行输入变化使成功基线失效；仅修改调度保持基线。更新同样校验目录约束并持久入队独立补标任务，不等待 tag 就绪。配置更新不执行编译、不重置调用时间。

创建配置时，`state.json` 的顶层字段 `updated_by` 记录配置归属用户的 ID。更新时可在请求体顶层传入字符串 `updated_by` 指定记录的值，省略时记录当前认证用户的 ID。

嵌套 `args` 的私有字段在公开响应中脱敏并加密保存供执行使用：公开参数不变时保留私有值，修改含私有字段的嵌套子树时须重新传入这些私有字段，删除子树则同时删除其保存的私有值。

两个调用时间都使用 Unix 秒：

- `task_first_create_time`：第一次成功受理的执行 task 的 `created_at`；未执行时为 null，首次设置后不变。
- `last_run_time`：最近一次成功受理的新 task 的 `created_at`。后续失败或取消不回退，幂等重放不刷新。

手动与定时提交均更新这些时间。`last_triggered_slot`、`next_triggered_slot` 仅用于调度，手动执行不推进 slot。

run 冻结保存 `task_id`、`actor`、`status`、`created_at`、`started_at`、`finished_at`、`params`、`result`、`error`、`meta`。`params` 是本次受理时的完整公开输入及调度配置；`result` 包含 `created`、`updated`、`deleted` 数量。`meta` 仅包含：

| 字段 | 含义 |
|------|------|
| `trigger` | 配置的第一次受理运行为 `initial`，与触发来源和执行结果无关；后续运行为 `manual` 或 `scheduled` |
| `mode` | 实际执行策略：`full` 或 `incremental` |
| `base_snapshot` | 本次采用的成功源基线；完整重算时为 null |
| `start_snapshot` | 执行开始时冻结的来源及目标状态 |
| `finish_snapshot` | 发布或回滚完成后保留的目标快照；尚未提交时为 null |
| `skill_snapshot_id` | Skill 快照；系统 Skill 为 null |

无变化时可复用开始快照。失败、取消不推进成功源基线。执行计划及必要恢复检查点保存在私有 Task 状态；公共配置、run 和 Task 不暴露执行凭据。

Compile 业务文件仅有：

```text
/local/{account_id}/_system/compile/tasks/{user_id}/{configuration_directory}/state.json
/local/{account_id}/_system/compile/tasks/{user_id}/{configuration_directory}/runs/{task_id}.json
```

`configuration_directory` 为名称 ASCII 字节的小写十六进制编码（`compile_name.encode("ascii").hex()`），不添加前缀，保证存储系统不区分大小写时，不同大小写的名称仍相互独立。公开 API 接收和返回原始 `compile_name`，不使用目录编码。

配置按账号和用户分目录存储，公开 API 使用原始 `compile_name` 定位配置。

state 的精确字段为 `from`、`to`、`skill`、`instruction`、`args`、`auto_update`、`update_schedule`、`task_first_create_time`、`last_run_time`、`last_triggered_slot`、`next_triggered_slot`、`conflict_num`、`progress`、`updated_by`、`execution_context`。`progress` 保存成功增量基线；`execution_context` 保存内部执行上下文和加密参数。

`conflict_num` 是收尾时 `to` 及其后代中，执行身份可检索的 `has_conflict=true` 文件数量，不是文件内部的冲突条数。统计使用空 query、标签过滤和 `level=2`，单次调用 `find`，固定 `limit=100`，不分页；计数达到或超过 100 时统一保存为 100，可能仍有更多冲突文件。正常完成在产物快照收尾后统计，不等待目标补标；失败或取消在回滚完成后统计。字段初始为 null；尚未取得起始快照的执行不检索并保存 null，避免读取其他任务正在修改的目标。回滚索引最多等待 30 秒；索引未就绪或检索失败时记录 warning 并保存 null，不因此回滚已成功生成的产物。修改配置时，`to` 不变保留已有计数，改变 `to` 则重置为 null。

### 检查 Compile 可用性

```http
GET /api/v1/compile/capabilities
```

使用当前认证上下文，无需请求参数。返回 `200 OK`：

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

| 字段 | 含义 |
|------|------|
| `configured` | 是否已配置 Compile 执行端点 |
| `can_create` | 当前认证上下文是否允许向该端点提交任务 |
| `reason_code` | 未配置端点时为 `NOT_CONFIGURED`；远程端点需要可转发的 OV API Key 时为 `API_KEY_REQUIRED`；其他情况为 `null` |

配置不可用时，在结果中返回 `can_create: false`，不会因此返回 HTTP 错误。此接口仅检查配置和凭证，不探测执行后端的健康状态，也不校验具体 Compile 请求。

### Compile 路由向量

`POST /api/v1/compile/embeddings` 使用当前认证上下文生成临时路由文本的向量，不建立索引或保留文本。

请求体：必填 `target_uri`（已存在且有读取权限的资源或 Skill 命名空间目标）；可选 `texts`（字符串数组，默认 `[]`，最多 32 条，每条 1–1024 个字符且不能仅含空白）；可选 `expected_model`（模型指纹，非空值必须与当前模型一致）。

返回 `200 OK`，包含 `status: "ok"` 和 `result: {"model": "...", "vectors": [...]}`。向量按输入顺序排列；`texts` 为空时仅返回模型指纹和空向量数组。

### 按提交键查询任务

```http
GET /api/v1/compile/submissions/{key}
```

创建任务的响应丢失或超时时，可通过此接口找回任务。传入的键必须与 `POST /api/v1/compile` 请求中可选的 `Idempotency-Key` 请求头一致。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `key` | 路径 | string | 是 | 长度为 16–128 个字符；仅允许字母、数字、`:`、`.`、`_` 和 `-` |

```bash
curl http://localhost:1933/api/v1/compile/submissions/studio-compile-001 \
  -H "X-API-Key: your-key"
```

返回 `200 OK`，其中 `status` 为 `"ok"`，`result` 为已有的 OV 任务记录，结构与上方创建任务响应一致。查询范围限定为当前账号和用户，不会创建任务。提交不存在（包括键仅被其他用户使用）时返回 `404`；键格式不合法时返回 `400 INVALID_ARGUMENT`。

需要安全重试创建请求时，应复用相同的 `Idempotency-Key` 和请求参数。使用同一键提交不同参数会返回 `409`。未传入该请求头的创建请求，不提供用于此查询的提交键。

### 查询任务

普通用户只能查询自己创建的任务；任务不存在或属于其他用户时均返回 `404`。ROOT 查询使用独立的管理可见范围，且不能取消任务。

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

运行中的 Compile 任务进入 `cancelling`，直到执行停止并完成必要清理。恢复使用冻结的目标开始快照，只影响 `to`，不恢复来源目录。发布或回滚完成后才能收敛终态；失败或取消不推进成功增量基线。重复取消已经取消的任务是幂等的。

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
    "instruction": "整理成知识库"
  }
}
```

`task_type` 和 `payload` 均必填。当前只支持 `task_type="compile"`；`payload` 始终包含解析后的 `from`、`to`、`skill`，可选的 `instruction`、Runtime `args`，以及服务端生成的执行计划。Runtime 不解析 `compile_name`，也不接收调度控制；一次性全量计划不要求来源快照。不支持的类型或无效的 payload 返回 `4xx`，不创建执行任务。

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

### 归档执行任务

```http
POST /runtime/v1/tasks/archive
```

请求体包含必填的 `session_id` 和可选的原始任务 `args`，沿用状态查询的鉴权和任务归属校验。该接口为空操作占位：返回 `200 OK` 和 `{}`，不修改或删除任务，允许重复调用。

Compile 的 Runtime session 进入 `completed`、`failed` 或 `cancelled` 时，OV 调用该接口；没有 Runtime session 的任务不触发归档。归档采用尽力通知方式，请求失败不影响任务终态。

## 相关文档

- [后台任务](17-tasks.md) - 通用任务查询、取消和列表接口
- [上下文编译](../context-compilation/01-overview.md) - Compile 使用场景和示例
- [Skills API](04-skills.md) - 管理 Compile 使用的 Skill
