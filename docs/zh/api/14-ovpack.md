# OVPack

OVPack API 用于导入、导出、备份和恢复 OpenViking 数据。

## API 参考

### export_ovpack

将资源树导出为 `.ovpack` 文件。

#### 1. API 实现介绍

将指定 URI 下的所有资源打包成 `.ovpack` 格式文件，用于备份或迁移。ROOT、ADMIN 和 USER 角色均可使用，仍受常规 URI 访问控制约束。

**处理流程**：
1. 验证用户权限
2. 遍历指定 URI 下的资源
3. 写入内容文件和 OVPack manifest
4. 打包成 zip 格式（.ovpack）
5. 以文件流形式返回

**格式说明**：
- 导出的 ZIP 会把用户内容原样放在 `<root>/files/` 下，并把内部元数据放在 `<root>/_ovpack/` 下。
- manifest 位于 `<root>/_ovpack/manifest.json`。
- `entries[].path` 是相对导出 root 的路径；`""` 表示 root 目录本身。
- 文件条目包含 `size` 和 `sha256`；`content_sha256` 覆盖按路径排序后的文件列表（`path`、`size`、`sha256`）。
- `_ovpack/index_records.jsonl` 保存可迁移的索引标量。`include_vectors=true` 时，`_ovpack/dense.f32` 保存纯 dense float32 向量快照和 embedding 元数据；底层 `VectorIndex.IndexType` 为 hybrid 时不支持向量快照导出。
- `id`、`uri`、`account_id`、`created_at`、`updated_at`、`active_count` 等运行态字段会在目标环境重新生成，不从包内恢复。
- OVPack 不额外设置包大小、文件数量或目录深度上限；实际可处理规模由 ZIP、存储后端和运行环境决定。

**代码入口**：
- `openviking/server/routers/pack.py:export_ovpack` - HTTP 路由
- `openviking/service/pack_service.py` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_export` - CLI 处理

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | string | 是 | - | 要导出的 Viking URI |
| include_vectors | boolean | 否 | false | 导出纯 dense 向量快照；底层 index type 为 hybrid 时会拒绝 |

**权限要求**：ROOT、ADMIN 或 USER

#### 3. 使用示例


**HTTP API**

```
POST /api/v1/pack/export
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{
    "uri": "viking://resources/my-project/",
    "include_vectors": false
  }' \
  --output my-project.ovpack
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-admin-key")
client.initialize()

# 导出到本地文件（HTTP SDK 会自动处理下载）
# 注意：导出功能主要通过 CLI 使用
```

**TypeScript SDK**

```typescript
const outputPath = await client.exportOVPack(
  "viking://resources/docs/",
  "./exports/docs.ovpack",
  true,
);
console.log(outputPath);
```

**Go SDK**

```go
outPath, err := client.ExportOVPack(
    ctx,
    "viking://resources/my-project/",
    "./exports/my-project.ovpack",
    &openviking.PackOptions{IncludeVectors: false},
)
if err != nil {
    return err
}
fmt.Println(outPath)
```

**CLI**

```bash
# 导出资源
ov export viking://resources/my-project/ ./exports/my-project.ovpack

# 导出 dense 向量快照
ov export viking://resources/my-project/ ./exports/my-project.ovpack --include-vectors
```


**响应示例**

此接口直接返回文件流（`Content-Type: application/zip`），不返回 JSON 包装体。

---

### import_ovpack

导入 `.ovpack` 文件。

#### 1. API 实现介绍

将 `.ovpack` 文件导入到指定位置，用于恢复或迁移数据。ROOT、ADMIN 和 USER 角色均可使用，仍受常规 URI 访问控制约束。

**处理流程**：
1. 验证用户权限
2. 解析上传的 `.ovpack` 文件
3. 校验 manifest 元数据、路径、文件和目录集合、文件大小和 checksum
4. 应用 `on_conflict`
5. 导入资源到目标位置，并重建向量

**代码入口**：
- `openviking/server/routers/pack.py:import_ovpack` - HTTP 路由
- `openviking/service/pack_service.py` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_import` - CLI 处理

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| temp_file_id | string | 是 | - | 临时上传文件 ID（通过 [temp_upload](02-resources.md#temp_upload) 获取） |
| parent | string | 是 | - | 目标父级 URI（导入到此处） |
| on_conflict | string | 否 | fail | 冲突策略：`fail`、`overwrite` 或 `skip` |
| vector_mode | string | 否 | auto | 向量处理方式：`auto`、`recompute` 或 `require` |

**权限要求**：ROOT、ADMIN 或 USER

**行为说明**：
- API 已不再接受 `vectorize` 或 `force`。
- `vector_mode=auto` 会在存在兼容 dense 快照时直接恢复，否则重新向量化；`recompute` 总是忽略包内向量；`require` 要求必须存在兼容 dense 快照，否则导入失败。
- dense 快照兼容性会比较 embedding provider、model、input、query/document 参数和维度。
- Session 文件属于 user 命名空间（`viking://user/{user_id}/sessions/...`），恢复后不触发向量化。
- `on_conflict=fail` 且目标 root 已存在时，会返回结构化的 `409 CONFLICT`。
- `on_conflict=overwrite` 会替换已有目标 root。`on_conflict=skip` 会保留已有目标 root，并直接返回该路径，不写入包内容。`skip` 是 root 级跳过，不是文件级补齐。
- 默认拒绝没有 manifest 的包，因为这类包无法提供内容完整性校验。
- 带 manifest entries 的包如果缺少内容文件或目录、混入额外文件或目录、文件大小不同、单文件 `sha256` 不同，或整体 `content_sha256` 缺失/不匹配，都会被拒绝导入。
- manifest `format_version` 不是当前支持版本（`3`）的包会被拒绝。
- `.abstract.md` 和 `.overview.md` 会作为语义侧边文件恢复；`.relations.json` 和 OVPack 内部文件会被排除。
- manifest index 标量中的 `context_type` 如果存在，必须和最终导入路径语义一致。
- `viking://resources/` 这类顶级 scope 包必须导入到 `viking://`。
- OVPack 不额外设置导入包大小、文件数量或目录深度上限；实际可处理规模由 ZIP、存储后端和运行环境决定。

#### 3. 使用示例


**HTTP API**

```
POST /api/v1/pack/import
Content-Type: application/json
```

```bash
# 第一步：上传 .ovpack 文件
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -r '.result.temp_file_id'
)

# 第二步：导入
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"overwrite\",
    \"vector_mode\": \"auto\"
  }"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-admin-key")
client.initialize()

# 导入 .ovpack 文件（HTTP SDK 会自动处理上传）
# 注意：导入功能主要通过 CLI 使用
```

**TypeScript SDK**

```typescript
const uri = await client.importOVPack(
  "./exports/docs.ovpack",
  "viking://resources/",
  {
    onConflict: "overwrite",
    vectorMode: "auto",
  },
);
console.log(uri);
```

**Go SDK**

```go
uri, err := client.ImportOVPack(
    ctx,
    "./exports/my-project.ovpack",
    "viking://resources/imported/",
    &openviking.ImportPackOptions{
        OnConflict: "overwrite",
        VectorMode: "auto",
    },
)
if err != nil {
    return err
}
fmt.Println(uri)
```

**CLI**

```bash
# 导入 .ovpack 文件
ov import ./exports/my-project.ovpack viking://resources/imported/

# 显式冲突策略
ov import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite

# 要求恢复兼容 dense 向量快照
ov import ./exports/my-project.ovpack viking://resources/imported/ --vector-mode require
```


**响应示例**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/imported/my-project/"
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**冲突错误示例**

```json
{
  "status": "error",
  "error": {
    "code": "CONFLICT",
    "message": "Resource already exists at viking://resources/imported/my-project. Use on_conflict='overwrite' to replace it.",
    "details": {
      "resource": "viking://resources/imported/my-project"
    }
  }
}
```

---

### backup_ovpack

将公开 scope root 备份为只能通过 restore 恢复的 `.ovpack` 文件。备份包含
`resources` 和 `user`；session 会通过 user 命名空间下的 `user/{user_id}/sessions`
一起包含，不包含 `temp`、`queue` 等内部运行态数据。
设置 `include_vectors=true` 时，会额外导出兼容的纯 dense 向量快照；底层 index type 为 hybrid 时会拒绝导出向量快照。

```
POST /api/v1/pack/backup
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/backup \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"include_vectors":false}' \
  --output openviking-backup.ovpack
```

Go SDK：

```go
outPath, err := client.BackupOVPack(
    ctx,
    "./backups/openviking.ovpack",
    &openviking.PackOptions{IncludeVectors: true},
)
if err != nil {
    return err
}
fmt.Println(outPath)
```

CLI：

```bash
ov backup ./backups/openviking.ovpack
ov backup ./backups/openviking.ovpack --include-vectors
```

**响应**

HTTP 成功时返回 `application/zip` 字节流，不使用标准 JSON 响应包：

```http
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename="openviking-backup.ovpack"

<ovpack binary body>
```

Go SDK 和 CLI 将字节流写入指定路径，并返回或输出该本地路径。

---

### restore_ovpack

恢复 `backup_ovpack` 生成的备份包到原始公开 scope root。普通 import 不接受备份包。
向量处理遵循 `vector_mode`；user 命名空间下的 session 文件只恢复文件状态，不触发向量化。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| temp_file_id | string | 是 | - | 临时上传文件 ID |
| on_conflict | string | 否 | fail | 冲突策略：`fail`、`overwrite` 或 `skip` |
| vector_mode | string | 否 | auto | 向量处理方式：`auto`、`recompute` 或 `require` |

```
POST /api/v1/pack/restore
Content-Type: application/json
```

```bash
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./backups/openviking.ovpack" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/pack/restore \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{\"temp_file_id\":\"$TEMP_FILE_ID\",\"on_conflict\":\"overwrite\",\"vector_mode\":\"auto\"}"
```

Go SDK：

```go
uri, err := client.RestoreOVPack(
    ctx,
    "./backups/openviking.ovpack",
    &openviking.ImportPackOptions{
        OnConflict: "overwrite",
        VectorMode: "require",
    },
)
if err != nil {
    return err
}
fmt.Println(uri)
```

CLI：

```bash
ov restore ./backups/openviking.ovpack --on-conflict overwrite
ov restore ./backups/openviking.ovpack --on-conflict overwrite --vector-mode require
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://"
  }
}
```

`uri` 是备份恢复到的公开 scope root。

---

## 相关文档

- [OVPack 指南](../guides/09-ovpack.md) - 格式、迁移和操作流程
- [快照](11-snapshot.md) - 工作区版本管理
- [临时上传](02-resources.md#temp_upload) - 上传待导入的包
