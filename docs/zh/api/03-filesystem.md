# 文件系统

OpenViking 提供类 Unix 的文件系统操作来管理上下文。

<a id="webdav"></a><a id="webdav-phase-1"></a>

## API 参考

<a id="abstract"></a><a id="overview"></a><a id="read"></a><a id="write"></a>

### ls()

列出目录内容。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |
| simple | bool | 否 | False | 仅返回相对路径 |
| recursive | bool | 否 | False | 递归列出所有子目录 |
| output | str | 否 | HTTP：`agent`；SDK：`original` | 输出格式：`agent` 或 `original` |
| abs_limit | int | 否 | 256 | `agent` 输出中的摘要长度限制 |
| show_all_hidden | bool | 否 | False | 像 `-a` 一样包含隐藏文件 |
| node_limit | int | 否 | 1000 | 最大返回节点数 |
| limit | int | 否 | None | `node_limit` 的别名 |
| sort_by | str | 否 | None | 在应用 `node_limit` 前，分别按 `name` 或 `mtime` 排序目录组和文件组；目录仍优先 |
| sort_order | str | 否 | `asc` | 排序方向：`asc` 或 `desc` |

**条目结构**

```python
{
    "name": "docs",           # 文件/目录名称
    "size": 4096,             # 大小（字节）
    "mode": 16877,            # 文件模式
    "modTime": "2024-01-01T00:00:00Z",  # ISO 时间戳
    "isDir": True,            # 如果是目录则为 True
    "uri": "viking://resources/docs/",  # Viking URI
    "meta": {}                # 可选元数据
}
```


**Python SDK (Embedded / HTTP)**

```python
entries = client.ls(
    "viking://resources/",
    node_limit=200,
    sort_by="mtime",
    sort_order="desc",
)
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['name']} - {type_str}")
```

**TypeScript SDK**

```typescript
const entries = await client.list("viking://resources/docs/", { simple: true });
console.log(entries);
```

**Go SDK**

```go
entries, err := client.List(ctx, "viking://resources/", nil)
if err != nil {
    return err
}
for _, entry := range entries {
    fmt.Println(entry)
}
```

**HTTP API**

```
GET /api/v1/fs/ls?uri={uri}&simple={bool}&recursive={bool}
```

```bash
# 基本列表
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/" \
  -H "X-API-Key: your-key"

# 简单路径列表
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/&simple=true" \
  -H "X-API-Key: your-key"

# 递归列表
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/&recursive=true" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking ls viking://resources/ [--simple] [--recursive]
```


**响应**

```json
{
  "status": "ok",
  "result": [
    {
      "name": "docs",
      "size": 4096,
      "mode": 16877,
      "modTime": "2024-01-01T00:00:00Z",
      "isDir": true,
      "uri": "viking://resources/docs/"
    }
  ],
  "time": 0.1
}
```

---

### tree()

获取目录树结构。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |
| output | str | 否 | HTTP：`agent`；SDK：`original` | 输出格式：`agent` 或 `original` |
| abs_limit | int | 否 | HTTP：256；SDK：128 | `agent` 输出中的摘要长度限制 |
| show_all_hidden | bool | 否 | False | 像 `-a` 一样包含隐藏文件 |
| node_limit | int | 否 | 1000 | 最大返回节点数 |
| level_limit | int | 否 | 3 | 最大目录遍历深度 |


**Python SDK (Embedded / HTTP)**

```python
entries = client.tree("viking://resources/")
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['rel_path']} - {type_str}")
```

**TypeScript SDK**

```typescript
const tree = await client.tree("viking://resources/docs/", { nodeLimit: 100 });
console.log(tree);
```

**Go SDK**

```go
entries, err := client.Tree(ctx, "viking://resources/", nil)
if err != nil {
    return err
}
for _, entry := range entries {
    fmt.Println(entry["rel_path"], entry["isDir"])
}
```

**HTTP API**

```
GET /api/v1/fs/tree?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/fs/tree?uri=viking://resources/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking tree viking://resources/my-project/
```


**响应**

```json
{
  "status": "ok",
  "result": [
    {
      "name": "docs",
      "size": 4096,
      "isDir": true,
      "rel_path": "docs/",
      "uri": "viking://resources/docs/"
    },
    {
      "name": "api.md",
      "size": 1024,
      "isDir": false,
      "rel_path": "docs/api.md",
      "uri": "viking://resources/docs/api.md"
    }
  ],
  "time": 0.1
}
```

---

### stat()

获取文件或目录的状态信息。对于目录，会返回目录下的项目计数。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |


**Python SDK (Embedded / HTTP)**

```python
info = client.stat("viking://resources/docs/api.md")
print(f"Size: {info['size']}")
print(f"Is directory: {info['isDir']}")

# 对于目录，会返回项目计数
dir_info = client.stat("viking://resources/docs")
if dir_info.get('isDir'):
    print(f"Item count: {dir_info.get('count')}")
```

**TypeScript SDK**

```typescript
const metadata = await client.stat("viking://resources/docs/api.md");
console.log(metadata);
```

**Go SDK**

```go
info, err := client.Stat(ctx, "viking://resources/docs/api.md")
if err != nil {
    return err
}
fmt.Println(info["size"], info["isDir"])
```

**HTTP API**

```
GET /api/v1/fs/stat?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/fs/stat?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking stat viking://resources/my-project/docs/api.md
openviking stat viking://resources/my-project/docs
```


**响应（文件）**

```json
{
  "status": "ok",
  "result": {
    "name": "api.md",
    "size": 1024,
    "mode": 33188,
    "modTime": "2024-01-01T00:00:00Z",
    "isDir": false,
    "isLocked": false,
    "uri": "viking://resources/docs/api.md"
  },
  "time": 0.1
}
```

**响应（目录）**

```json
{
  "status": "ok",
  "result": {
    "name": "docs",
    "size": 4096,
    "mode": 16877,
    "modTime": "2024-01-01T00:00:00Z",
    "isDir": true,
    "isLocked": false,
    "uri": "viking://resources/docs",
    "count": 42
  },
  "time": 0.1
}
```

`isLocked` 字段反映路径当前是否被路径锁持有：路径自身存在有效锁（包括目标路径对应的 exact-path lock），或者任一祖先目录持有 TreeLock。当 LockManager 不可用或查询失败时返回 `false`，调用方可据此避免先写入再观察到 `ResourceBusyError`。

`count` 字段（仅目录）包含该目录下的项目（文件和子目录）估计数量（来自向量索引）。

---

### attrs()

获取文件或目录的逻辑扩展属性。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |


**Python SDK (HTTP)**

```python
attrs = client.attrs("viking://resources/docs/api.md")
print(attrs["attrs"]["tags"])
```

**TypeScript SDK**

```typescript
const attributes = await client.attrs("viking://resources/docs/api.md");
console.log(attributes);
```

**Go SDK**

```go
attrs, err := client.Attrs(ctx, "viking://resources/docs/api.md")
if err != nil {
    return err
}
metadata := attrs["attrs"].(map[string]any)
fmt.Println(metadata["tags"])
```

**HTTP API**

```
GET /api/v1/fs/attrs?uri={uri}
POST /api/v1/fs/attrs/set_tags
```

```bash
curl -X GET "http://localhost:1933/api/v1/fs/attrs?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"

curl -X POST "http://localhost:1933/api/v1/fs/attrs/set_tags" \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"uri":"viking://resources/docs","tags":["team=search"],"mode":"append","recursive":true}'
```

**CLI**

```bash
openviking attrs get viking://resources/docs/api.md
openviking attrs get viking://resources/docs/api.md tags
openviking attrs get viking://user/alice/memories/experiences/foo.md memory.resource_refs
openviking attrs set-tags viking://resources/docs/api.md --tags team=search,env=prod
openviking attrs set-tags viking://resources/docs --tags team=search --mode append --recursive
```

目录目标会更新目录语义记录；`recursive=true` 还会更新已有子文件和子目录语义记录。


**响应（Resource）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/api.md",
    "context_type": "resource",
    "attrs": {
      "tags": ["team=search", "env=prod"]
    }
  }
}
```

**响应（Memory）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://user/alice/memories/experiences/foo.md",
    "context_type": "memory",
    "attrs": {
      "memory": {
        "memory_type": "experiences",
        "name": "foo",
        "tags": ["ui"],
        "resource_refs": ["viking://resources/docs/api.md"]
      },
      "tags": ["team=search"]
    }
  }
}
```

`attrs.memory` 来自 `MEMORY_FIELDS` 元信息，已去掉正文内容。`attrs.tags` 是 `attrs set-tags` 和搜索过滤使用的显式检索标签。

---

### mkdir()

创建目录。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 新目录的 Viking URI |
| description | str | 否 | `null` | 目录初始说明。传入后会写入 `.abstract.md`，并进入目录 L0 向量化队列。 |


**Python SDK (Embedded / HTTP)**

```python
client.mkdir("viking://resources/new-project/")
client.mkdir("viking://resources/new-project/", description="接口文档目录")
```

**TypeScript SDK**

```typescript
await client.mkdir("viking://resources/docs/guides/", "Project guides");
```

**Go SDK**

```go
if err := client.Mkdir(ctx, "viking://resources/new-project/", "接口文档目录"); err != nil {
    return err
}
```

**HTTP API**

```
POST /api/v1/fs/mkdir
```

```bash
curl -X POST http://localhost:1933/api/v1/fs/mkdir \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/new-project/",
    "description": "接口文档目录"
  }'
```

**CLI**

```bash
openviking mkdir viking://resources/new-project/
openviking mkdir viking://resources/new-project/ --description "接口文档目录"
```


**响应**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/new-project/"
  },
  "time": 0.1
}
```

---

### rm()

删除文件或目录。递归删除目录时会返回删除的项目估计数量。

`rm` 是幂等操作：删除一个合法但不存在的 URI 仍会成功。
URI 格式非法、scheme 不支持或使用非公开作用域时返回 `INVALID_URI`。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要删除的 Viking URI |
| recursive | bool | 否 | False | 递归删除目录 |


**Python SDK (Embedded / HTTP)**

```python
# 删除单个文件
client.rm("viking://resources/docs/old.md")

# 递归删除目录
client.rm("viking://resources/old-project/", recursive=True)
```

**TypeScript SDK**

```typescript
await client.remove("viking://resources/docs/old.md", { wait: true });
```

**Go SDK**

```go
err := client.Remove(ctx, "viking://resources/old-project/", &openviking.RemoveOptions{
    Recursive: true,
})
if err != nil {
    return err
}
```

**HTTP API**

```
DELETE /api/v1/fs?uri={uri}&recursive={bool}
```

```bash
# 删除单个文件
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://resources/docs/old.md" \
  -H "X-API-Key: your-key"

# 递归删除目录
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://resources/old-project/&recursive=true" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking rm viking://resources/old.md [--recursive]
```


**响应（单个文件）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/old.md"
  },
  "time": 0.1
}
```

**响应（递归删除）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/old-project/",
    "estimated_deleted_count": 42
  },
  "time": 0.1
}
```

`estimated_deleted_count` 字段（递归删除时）包含删除的项目（文件和目录）估计数量（来自向量索引）。CLI 会在输出中显示此信息。

删除 `viking://resources/...` 时，响应可能包含 `memory_cleanup`，表示删除前已清理引用该资源 URI 的用户记忆。

---

### mv()

移动文件或目录。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| from_uri | str | 是 | - | 源 Viking URI |
| to_uri | str | 是 | - | 目标 Viking URI |


**Python SDK (Embedded / HTTP)**

```python
client.mv(
    "viking://resources/old-name/",
    "viking://resources/new-name/"
)
```

**TypeScript SDK**

```typescript
await client.move(
  "viking://resources/docs/old.md",
  "viking://resources/docs/new.md",
);
```

**Go SDK**

```go
if err := client.Move(ctx, "viking://resources/old-name/", "viking://resources/new-name/"); err != nil {
    return err
}
```

**HTTP API**

```
POST /api/v1/fs/mv
```

```bash
curl -X POST http://localhost:1933/api/v1/fs/mv \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/old-name/",
    "to_uri": "viking://resources/new-name/"
  }'
```

**CLI**

```bash
openviking mv viking://resources/old-name/ viking://resources/new-name/
```


**响应**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/old-name/",
    "to": "viking://resources/new-name/"
  },
  "time": 0.1
}
```

<a id="grep"></a><a id="glob"></a>

<a id="link"></a><a id="relations"></a><a id="unlink"></a>

<a id="export_ovpack"></a><a id="import_ovpack"></a><a id="backup_ovpack"></a><a id="restore_ovpack"></a>

## 相关文档

- [Viking URI](../concepts/04-viking-uri.md) - URI 规范
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
- [Resources](02-resources.md) - 资源管理
