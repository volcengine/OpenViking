# 文件系统

OpenViking 提供类 Unix 的文件系统操作来管理上下文。

## WebDAV（Phase 1）

OpenViking Server 也提供了一个面向资源文件的精简 WebDAV 适配层：

```text
/webdav/resources
```

Phase 1 有意把范围控制得比较小：

- 仅开放 `resources` 命名空间，不暴露 memories、skills、sessions 等其他空间。
- 以文本写入为主，当前 `PUT` 只接受 UTF-8 文本内容。
- 只实现一小部分 WebDAV 方法：`OPTIONS`、`PROPFIND`、`GET`、`HEAD`、`PUT`、`DELETE`、`MKCOL`、`MOVE`。
- 语义侧边文件保持内部可见。`.abstract.md`、`.overview.md`、`.relations.json`、`.path.ovlock` 这些派生文件不会出现在 WebDAV 列表中，也不能被直接访问。

行为说明：

- 通过 WebDAV 新建文件时，会对该文件路径触发 OpenViking 的语义生成。
- 通过 WebDAV 覆盖已有文件时，会像 `write()` 一样刷新相关语义和向量。
- 用户自己创建的点目录或点文件仍然可见，只有上面列出的保留内部文件名会被隐藏。

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

**说明**

- `read()` 只接受文件 URI。传入已存在的目录 URI 时返回 `INVALID_ARGUMENT`（`400`），而不是 `NOT_FOUND`。
- 公开 URI 参数只接受 `resources`、`user`、`agent`、`session` 作用域。`temp`、`queue` 等内部作用域会返回 `INVALID_URI`。

**Python SDK (Embedded / HTTP)**

```python
content = client.read("viking://resources/docs/api.md")
print(f"Content:\n{content}")
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
| uri | str | 是 | - | 已存在文件的 URI |
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

### ls()

列出目录内容。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |
| simple | bool | 否 | False | 仅返回相对路径 |
| recursive | bool | 否 | False | 递归列出所有子目录 |
| output | str | 否 | `agent` | 输出格式：`agent` 或 `original` |
| abs_limit | int | 否 | 256 | `agent` 输出中的摘要长度限制 |
| show_all_hidden | bool | 否 | False | 像 `-a` 一样包含隐藏文件 |
| node_limit | int | 否 | 1000 | 最大返回节点数 |
| limit | int | 否 | None | `node_limit` 的别名 |

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
entries = client.ls("viking://resources/")
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['name']} - {type_str}")
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
| output | str | 否 | `agent` | 输出格式：`agent` 或 `original` |
| abs_limit | int | 否 | 256 | `agent` 输出中的摘要长度限制 |
| show_all_hidden | bool | 否 | False | 像 `-a` 一样包含隐藏文件 |
| node_limit | int | 否 | 1000 | 最大返回节点数 |
| limit | int | 否 | None | `node_limit` 的别名 |
| level_limit | int | 否 | 3 | 最大目录遍历深度 |

**Python SDK (Embedded / HTTP)**

```python
entries = client.tree("viking://resources/")
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['rel_path']} - {type_str}")
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

获取文件或目录的状态信息。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |

**Python SDK (Embedded / HTTP)**

```python
info = client.stat("viking://resources/docs/api.md")
print(f"Size: {info['size']}")
print(f"Is directory: {info['isDir']}")
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
```

**响应**

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

`isLocked` 字段反映路径当前是否被 path lock 持有：路径自身存在有效的 `.path.ovlock`，或者任一祖先目录持有 SUBTREE 锁。当 LockManager 不可用或查询失败时返回 `false`，调用方可据此避免先写入再观察到 `ResourceBusyError`。

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

删除文件或目录。

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

**响应**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/old.md"
  },
  "time": 0.1
}
```

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

---

### grep()

按模式搜索内容。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要搜索的 Viking URI |
| pattern | str | 是 | - | 搜索模式（正则表达式） |
| case_insensitive | bool | 否 | False | 忽略大小写 |
| exclude_uri | str | 否 | None | 搜索时要排除的 URI 前缀 |
| node_limit | int | 否 | None | 最大搜索节点数 |
| level_limit | int | 否 | 5 | 最大目录遍历深度 |

**Python SDK (Embedded / HTTP)**

```python
results = client.grep(
    "viking://resources/",
    "authentication",
    case_insensitive=True
)

print(f"Found {results['count']} matches")
for match in results['matches']:
    print(f"  {match['uri']}:{match['line']}")
    print(f"    {match['content']}")
```

**HTTP API**

```
POST /api/v1/search/grep
```

```bash
curl -X POST http://localhost:1933/api/v1/search/grep \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/",
    "pattern": "authentication",
    "case_insensitive": true
  }'
```

**CLI**

```bash
openviking grep viking://resources/ "authentication" [--ignore-case]
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "matches": [
      {
        "uri": "viking://resources/docs/auth.md",
        "line": 15,
        "content": "User authentication is handled by..."
      }
    ],
    "count": 1
  },
  "time": 0.1
}
```

---

### glob()

按模式匹配文件。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| pattern | str | 是 | - | Glob 模式（例如 `**/*.md`） |
| uri | str | 否 | "viking://" | 起始 URI |
| node_limit | int | 否 | None | 最大返回匹配数 |

**Python SDK (Embedded / HTTP)**

```python
# 查找所有 Markdown 文件
results = client.glob("**/*.md", "viking://resources/")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# 查找所有 Python 文件
results = client.glob("**/*.py", "viking://resources/")
print(f"Found {results['count']} Python files")
```

**HTTP API**

```
POST /api/v1/search/glob
```

```bash
curl -X POST http://localhost:1933/api/v1/search/glob \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "pattern": "**/*.md",
    "uri": "viking://resources/"
  }'
```

**CLI**

```bash
openviking glob "**/*.md" [--uri viking://resources/]
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "matches": [
      "viking://resources/docs/api.md",
      "viking://resources/docs/guide.md"
    ],
    "count": 2
  },
  "time": 0.1
}
```

---

### link()

创建资源之间的关联。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| from_uri | str | 是 | - | 源 URI |
| to_uris | str 或 List[str] | 是 | - | 目标 URI |
| reason | str | 否 | "" | 关联原因 |

**Python SDK (Embedded / HTTP)**

```python
# 单个关联
client.link(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/",
    reason="Security best practices for authentication"
)

# 多个关联
client.link(
    "viking://resources/docs/api/",
    [
        "viking://resources/docs/auth/",
        "viking://resources/docs/errors/"
    ],
    reason="Related documentation"
)
```

**HTTP API**

```
POST /api/v1/relations/link
```

```bash
# 单个关联
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uris": "viking://resources/docs/security/",
    "reason": "Security best practices for authentication"
  }'

# 多个关联
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/api/",
    "to_uris": ["viking://resources/docs/auth/", "viking://resources/docs/errors/"],
    "reason": "Related documentation"
  }'
```

**CLI**

```bash
openviking link viking://resources/docs/auth/ viking://resources/docs/security/ --reason "Security best practices"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### relations()

获取资源的关联关系。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | Viking URI |

**Python SDK (Embedded / HTTP)**

```python
relations = client.relations("viking://resources/docs/auth/")
for rel in relations:
    print(f"Related: {rel['uri']}")
    print(f"  Reason: {rel['reason']}")
```

**HTTP API**

```
GET /api/v1/relations?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking relations viking://resources/docs/auth/
```

**响应**

```json
{
  "status": "ok",
  "result": [
    {"uri": "viking://resources/docs/security/", "reason": "Security best practices"},
    {"uri": "viking://resources/docs/errors/", "reason": "Error handling"}
  ],
  "time": 0.1
}
```

---

### unlink()

移除关联关系。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| from_uri | str | 是 | - | 源 URI |
| to_uri | str | 是 | - | 要取消关联的目标 URI |

**Python SDK (Embedded / HTTP)**

```python
client.unlink(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/"
)
```

**HTTP API**

```
DELETE /api/v1/relations/link
```

```bash
curl -X DELETE http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uri": "viking://resources/docs/security/"
  }'
```

**CLI**

```bash
openviking unlink viking://resources/docs/auth/ viking://resources/docs/security/
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### export_ovpack

将资源树导出为 `.ovpack` 文件。

#### 1. API 实现介绍

将指定 URI 下的所有资源打包成 `.ovpack` 格式文件，用于备份或迁移。需要 ROOT 或 ADMIN 权限。

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

**权限要求**：ROOT 或 ADMIN

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

将 `.ovpack` 文件导入到指定位置，用于恢复或迁移数据。需要 ROOT 或 ADMIN 权限。

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

**权限要求**：ROOT 或 ADMIN

**行为说明**：
- API 已不再接受 `vectorize` 或 `force`。
- `vector_mode=auto` 会在存在兼容 dense 快照时直接恢复，否则重新向量化；`recompute` 总是忽略包内向量；`require` 要求必须存在兼容 dense 快照，否则导入失败。
- dense 快照兼容性会比较 embedding provider、model、input、query/document 参数和维度。
- Session 导入只恢复 session 文件状态，不触发向量化。
- `on_conflict=fail` 且目标 root 已存在时，会返回结构化的 `409 CONFLICT`。
- `on_conflict=overwrite` 会替换已有目标 root。`on_conflict=skip` 会保留已有目标 root，并直接返回该路径，不写入包内容。`skip` 是 root 级跳过，不是文件级补齐。
- 默认拒绝没有 manifest 的包，因为这类包无法提供内容完整性校验。
- 带 manifest entries 的包如果缺少内容文件或目录、混入额外文件或目录、文件大小不同、单文件 `sha256` 不同，或整体 `content_sha256` 缺失/不匹配，都会被拒绝导入。
- manifest `format_version` 不是当前支持版本的包会被拒绝。
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
`resources`、`user`、`agent`、`session`，不包含 `temp`、`queue` 等内部运行态数据。
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

CLI：

```bash
ov backup ./backups/openviking.ovpack
ov backup ./backups/openviking.ovpack --include-vectors
```

---

### restore_ovpack

恢复 `backup_ovpack` 生成的备份包到原始公开 scope root。普通 import 不接受备份包。
向量处理遵循 `vector_mode`；session 只恢复文件状态。

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

CLI：

```bash
ov restore ./backups/openviking.ovpack --on-conflict overwrite
ov restore ./backups/openviking.ovpack --on-conflict overwrite --vector-mode require
```

---

## 相关文档

- [Viking URI](../concepts/04-viking-uri.md) - URI 规范
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
- [Resources](02-resources.md) - 资源管理
