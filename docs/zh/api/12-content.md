# 内容

内容 API 负责读取 L0/L1/L2 内容、写入文本，以及维护内容对应的语义和向量索引。

## API 参考

### abstract()

读取 L0 摘要（约 100 token 的概要）。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI（必须是目录） |


**Python SDK (Embedded / HTTP)**

```python
abstract = client.abstract("viking://resources/docs/")
print(f"Abstract: {abstract}")
# Output: "Documentation for the project API, covering authentication, endpoints..."
```

**TypeScript SDK**

```typescript
const abstract = await client.abstract("viking://resources/docs/");
console.log(abstract);
```

**Go SDK**

```go
abstract, err := client.Abstract(ctx, "viking://resources/docs/")
if err != nil {
    return err
}
fmt.Println(abstract)
```

**HTTP API**

```
GET /api/v1/content/abstract?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/content/abstract?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking abstract viking://resources/docs/
```


**响应**

```json
{
  "status": "ok",
  "result": "Documentation for the project API, covering authentication, endpoints...",
  "time": 0.1
}
```

---

### overview()

读取 L1 概览，适用于目录。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI（必须是目录） |


**Python SDK (Embedded / HTTP)**

```python
overview = client.overview("viking://resources/docs/")
print(f"Overview:\n{overview}")
```

**TypeScript SDK**

```typescript
const overview = await client.overview("viking://resources/docs/");
console.log(overview);
```

**Go SDK**

```go
overview, err := client.Overview(ctx, "viking://resources/docs/")
if err != nil {
    return err
}
fmt.Println(overview)
```

**HTTP API**

```
GET /api/v1/content/overview?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking overview viking://resources/docs/
```


**响应**

```json
{
  "status": "ok",
  "result": "## docs/\n\nContains API documentation and guides...",
  "time": 0.1
}
```

---

### read()

读取 L2 完整内容。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |
| offset | int | 否 | 0 | 起始行号（0 开始） |
| limit | int | 否 | -1 | 读取的行数，`-1` 表示读到结尾 |
| raw | bool | 否 | false | 返回未过滤 MEMORY_FIELDS 的原始存储内容（仅 HTTP API，Python SDK 暂未暴露）。 |

**说明**

- `read()` 只接受文件 URI。传入已存在的目录 URI 时返回 `INVALID_ARGUMENT`（`400`），而不是 `NOT_FOUND`。该错误会携带结构化的 `details` 字段——`details.expected` 为 `"file"`，`details.actual` 为 `"directory"`，`details.resource` 为出错的 URI（HTTP 路径上会带上）——客户端据此即可以编程方式判断"文件 vs 目录"不匹配（例如回退到 `list`），而无需对错误消息做字符串匹配。
- 公开 URI 参数接受 `resources` 和 `user` 作用域。访问 session 文件时，使用 `viking://user/{user_id}/sessions/{session_id}`，也可以使用向后兼容的 `viking://session/{session_id}` 别名。`temp`、`queue` 等内部作用域会返回 `INVALID_URI`。


**Python SDK (Embedded / HTTP)**

```python
content = client.read("viking://resources/docs/api.md")
print(f"Content:\n{content}")
```

**TypeScript SDK**

```typescript
const content = await client.read("viking://resources/docs/api.md", 0, -1);
console.log(content);
```

**Go SDK**

```go
content, err := client.Read(ctx, "viking://resources/docs/api.md", 0, -1)
if err != nil {
    return err
}
fmt.Println(content)
```

**HTTP API**

```
GET /api/v1/content/read?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking read viking://resources/docs/api.md
```


**响应**

```json
{
  "status": "ok",
  "result": "# API Documentation\n\nFull content of the file...",
  "time": 0.1
}
```

---

### write()

修改一个已存在的文件，或在 `mode="create"` 时创建新文件，并自动刷新相关语义与向量。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要写入的文件 URI。`mode="create"` 时目标文件必须不存在 |
| content | str | 是 | - | 要写入的新内容 |
| mode | str | 否 | `replace` | `replace`、`append` 或 `create` |
| wait | bool | 否 | `false` | 是否等待后台语义/向量刷新完成 |
| timeout | float | 否 | `null` | 当 `wait=true` 时的超时时间（秒） |

**说明**

- `replace` 和 `append` 要求文件已存在；`create` 仅用于创建新文件，目标路径已存在时返回 `409 Conflict`。目录始终会被拒绝。
- `create` 只允许以下文本类扩展名：`.md`、`.txt`、`.json`、`.yaml`、`.yml`、`.toml`、`.py`、`.js`、`.ts`。父目录会自动创建。
- 不允许直接写入派生语义文件：`.abstract.md`、`.overview.md`、`.relations.json`。
- 文件内容会在 API 返回前完成更新；`wait` 只控制是否等待语义/向量刷新完成。
- 公共 API 已不再接受 `regenerate_semantics` 或 `revectorize`；写入后一定会自动刷新相关语义与向量。


**Python SDK (Embedded / HTTP)**

```python
result = client.write(
    "viking://resources/docs/api.md",
    "# Updated API\n\nFresh content.",
    mode="replace",
    wait=True,
)
print(result["root_uri"])
```

**TypeScript SDK**

```typescript
await client.write("viking://resources/docs/new.md", "# New document\n", { wait: true });
```

**Go SDK**

```go
result, err := client.Write(
    ctx,
    "viking://resources/docs/api.md",
    "# Updated API\n\nFresh content.",
    &openviking.WriteOptions{
        Mode: "replace",
        Wait: true,
    },
)
if err != nil {
    return err
}
fmt.Println(result["root_uri"])
```

**HTTP API**

```
POST /api/v1/content/write
```

```bash
curl -X POST "http://localhost:1933/api/v1/content/write" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/docs/api.md",
    "content": "# Updated API\n\nFresh content.",
    "mode": "replace",
    "wait": true
  }'
```

**CLI**

```bash
openviking write viking://resources/docs/api.md \
  --content "# Updated API\n\nFresh content." \
  --wait
```


**响应**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/api.md",
    "root_uri": "viking://resources/docs",
    "context_type": "resource",
    "mode": "replace",
    "written_bytes": 29,
    "content_updated": true,
    "semantic_status": "complete",
    "vector_status": "complete",
    "queue_status": {
      "Semantic": {
        "processed": 1,
        "error_count": 0,
        "errors": []
      },
      "Embedding": {
        "processed": 2,
        "error_count": 0,
        "errors": []
      }
    }
  }
}
```

---

### download()

以原始字节流下载文件，适用于图片、PDF 和其他非文本内容。响应使用 `application/octet-stream`，并通过 `Content-Disposition` 返回文件名。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | string | 是 | 要下载的文件 URI |

**HTTP API**

```http
GET /api/v1/content/download?uri={uri}
```

```bash
curl --get http://localhost:1933/api/v1/content/download \
  -H "X-API-Key: your-key" \
  --data-urlencode "uri=viking://resources/images/logo.png" \
  --output logo.png
```

公共 SDK 和 CLI 当前没有独立的原始字节下载方法，因此本节只展示 HTTP Tab。

---

### set_tags()

设置用于检索过滤的显式 `k=v` 标签。`replace` 替换已有标签，`append` 追加标签；对目录设置 `recursive=true` 时会更新目录下的文件。

**Python SDK (Embedded / HTTP)**

```python
result = client.set_tags(
    "viking://resources/project/",
    ["team=search", "env=prod"],
    mode="replace",
    recursive=True,
)
```

**TypeScript SDK**

```typescript
const result = await client.setTags(
  "viking://resources/project/",
  ["team=search", "env=prod"],
  { mode: "replace", recursive: true },
);
```

**Go SDK**

```go
result, err := client.SetTags(
    ctx,
    "viking://resources/project/",
    []string{"team=search", "env=prod"},
    &openviking.SetTagsOptions{Mode: "replace", Recursive: true},
)
```

**HTTP API**

```http
POST /api/v1/content/set_tags
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/content/set_tags \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri":"viking://resources/project/",
    "tags":["team=search","env=prod"],
    "mode":"replace",
    "recursive":true
  }'
```

`POST /api/v1/fs/attrs/set_tags` 是等价兼容路径，当前 Python、TypeScript、Go SDK 和 CLI 使用该路径。

**CLI**

```bash
ov set-tags viking://resources/project/ \
  --tags team=search,env=prod \
  --mode replace \
  --recursive
```

---

### reindex()

对已经存储在 OpenViking 中的现有内容，重新构建语义产物和/或向量索引。这是一个运维维护接口，适用于 embedding 模型更换、VLM 更换、向量库重刷、版本升级后修复历史索引等场景。

这个接口面向已有的 `viking://...` 内容，不负责导入新文件。常规导入请使用 [Resources](02-resources.md)。

**认证**

- HTTP 端点：在开启认证时要求 admin/root 角色。`api_key` 模式下，租户内容重建请使用 admin key；裸 root key 不能访问租户级数据。
- Python embedded 模式：使用当前 service context
- Python HTTP client / CLI：使用当前认证身份发起请求

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要重新索引的 Viking URI |
| mode | str | 否 | `vectors_only` | 重建模式：`vectors_only`、`semantic_and_vectors` 或 `prune_orphans` |
| wait | bool | 否 | `true` | 是否等待任务完成 |
| dry_run | bool | 否 | `false` | 仅适用于 `mode="prune_orphans"`；只报告 orphan 向量记录，不实际删除 |

HTTP 请求体不接受未知字段。`uri` 可以使用其他 content API 支持的 OpenViking 路径变量，服务端会先解析再校验。

**支持的 URI 范围**

- `viking://`
- `viking://user`
- `viking://user/<user_id>`
- `viking://resources`
- `viking://resources/...`
- `viking://user/<user_id>/memories/...`
- `viking://user/<user_id>/skills`
- `viking://user/<user_id>/skills/<skill_name>`

`reindex()` 不支持会话命名空间。请求 `viking://session/...` 或
`viking://user/<user_id>/sessions/...` 会被拒绝；重建更大的 user 命名空间时，
session 子树会被跳过。

**模式说明**

- `vectors_only`：基于当前仍可恢复的源数据重建向量库记录，不会重写 `.abstract.md` 和 `.overview.md`
- `semantic_and_vectors`：先重新生成语义产物，再基于新的语义结果重建向量
- `prune_orphans`：删除请求 URI 范围内源文件已不存在的向量库记录。设置 `dry_run=true` 时，只报告会删除多少记录，不实际删除。

对于 `resource` 和 `skill`，`semantic_and_vectors` 会刷新目录/文件语义产物，包括 `.abstract.md` 和 `.overview.md`。对于 `memory`，它会重建当前已持久化 memory 子树的语义和向量，但不会回放历史记忆抽取顺序。

对于 `semantic_and_vectors`，语义刷新和向量重建由 reindex executor 串行编排。语义刷新阶段不会再额外向后台 embedding queue 投递自己的向量化任务；向量由 reindex 阶段统一重建，因此 `wait=true` 表示等待 reindex 操作本身完成。

对于 `prune_orphans`，源文件是否存在以当前文件系统为准。如果整个目录已经不存在，该目录下的正文文件向量和语义 sidecar 向量（例如 `.abstract.md`、`.overview.md`）会一起清理。`dry_run` 用在其他模式时会被拒绝。

**Python SDK (Embedded / HTTP)**

```python
result = client.reindex(
    uri="viking://resources",
    mode="vectors_only",
    wait=True,
)
print(result)
```

```python
result = client.reindex(
    uri="viking://user/default/skills",
    mode="semantic_and_vectors",
    wait=False,
)
print(result["status"])
```

```python
result = client.reindex(
    uri="viking://resources",
    mode="prune_orphans",
    dry_run=True,
)
print(result["would_delete_records"])
```

**TypeScript SDK**

```typescript
console.log(await client.reindex("viking://resources/docs/"));
```

**Go SDK**

```go
result, err := client.Reindex(ctx, "viking://resources", &openviking.ReindexOptions{
    Mode: "vectors_only",
    Wait: true,
})
if err != nil {
    return err
}
fmt.Println(result["status"])
```

```go
result, err := client.Reindex(ctx, "viking://resources", &openviking.ReindexOptions{
    Mode: "prune_orphans",
    Wait: true,
    DryRun: true,
})
if err != nil {
    return err
}
fmt.Println(result["would_delete_records"])
```

**HTTP API**

```
POST /api/v1/content/reindex
```

不存在 `/api/v1/maintenance/reindex` 端点。请使用 `/api/v1/content/reindex`。

```bash
curl -X POST http://localhost:1933/api/v1/content/reindex \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -d '{
    "uri": "viking://resources",
    "mode": "prune_orphans",
    "wait": true,
    "dry_run": true
  }'
```

**CLI**

```bash
openviking reindex viking://resources --mode vectors_only
```

```bash
openviking reindex viking://user/default/skills --mode semantic_and_vectors --wait false
```

```bash
openviking reindex viking://resources --mode prune_orphans --dry-run
```

**同步响应（`wait=true`）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "status": "completed",
    "object_type": "resource",
    "scanned_records": 120,
    "rebuilt_records": 118,
    "deleted_records": 0,
    "would_delete_records": 0,
    "unsupported_records": 2,
    "failed_records": 0,
    "duration_ms": 1284,
    "warnings": []
  },
  "time": 0.1
}
```

**异步响应（`wait=false`）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "object_type": "resource",
    "status": "accepted",
    "task_id": "task_xxx"
  },
  "time": 0.1
}
```

使用返回的 task 查询后台任务：

```bash
curl -X GET http://localhost:1933/api/v1/tasks/task_xxx \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default"
```

Reindex 后台任务的 `task_type` 为 `admin_reindex`，`resource_id` 等于请求中的 `uri`，也可以这样列出：

```text
GET /api/v1/tasks?task_type=admin_reindex&resource_id=viking://resources
```

任务记录持久化在 `/local/{account_id}/_system/tasks/{user_id}/{task_id}.json`，服务重启后仍可查询。

**结果字段**

| 字段 | 说明 |
|------|------|
| status | 同步完成时为 `completed`，后台执行时为 `accepted` |
| uri | 解析路径变量后的请求 URI |
| object_type | 推断出的目标类型，例如 `resource`、`skill`、`memory`、`user_namespace`、`skill_namespace` 或 `global_namespace` |
| mode | 实际执行的 reindex 模式 |
| scanned_records | 被检查的记录或语义源数量 |
| rebuilt_records | 成功重建的向量记录数量 |
| deleted_records | `prune_orphans` 实际删除的向量记录数量；`dry_run=true` 时为 `0` |
| would_delete_records | `prune_orphans` dry-run 模式下将会删除的向量记录数量 |
| unsupported_records | 因没有可用向量来源而跳过的记录数量 |
| failed_records | 重建失败的记录数量 |
| duration_ms | 同步执行耗时，单位毫秒 |
| warnings | 可恢复的单条记录级 warning |
| task_id | 后台任务 ID，仅 `wait=false` 时返回 |

**行为说明**

- `vectors_only` 和 `semantic_and_vectors` 是非破坏式的，采用重建/覆盖写入，不需要先 drop 向量集合。
- `prune_orphans` 除非设置 `dry_run=true`，否则会删除源文件已经不存在的向量记录。
- 对 `viking://` 发起 reindex 时，会向下分发到支持的顶层命名空间，并显式排除 `session`。
- 命名空间级 reindex，例如 `viking://user`，会继续传播到其支持的子内容类型。
- 如果只是 embedding 模型或向量索引需要刷新，应使用 `vectors_only`。
- 如果语义产物本身也需要重建，再做重向量化，应使用 `semantic_and_vectors`。
- 如果文件系统曾绕过正常 API 发生删除，向量库可能还残留已删除路径的记录，应使用 `prune_orphans`。
- 同一个 URI 和 owner 同时只能运行一个 reindex 任务。对同一目标的并发请求会返回 conflict。
- 对 resource 文件，文本文件在没有 summary 时可以使用文件正文；非文本文件需要已生成的 summary 或已有向量记录 fallback，否则会计为 unsupported。

**当前限制**

- Reindex 会使用当前系统中“尽可能可恢复”的输入进行重建，不保证所有场景都能逐字节回放历史当时的 embedding 输入。
- Memory 的 semantic reindex 基于当前已持久化的 memory 树，不会重建最初按时间顺序执行的记忆抽取流水线。

---

## 相关文档

- [文件系统](03-filesystem.md) - 目录与文件操作
- [检索](06-retrieval.md) - 语义搜索与模式搜索
- [后台任务](17-tasks.md) - 跟踪异步 reindex 任务
