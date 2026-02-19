# 资源管理

资源是智能体可以引用的外部知识。本指南介绍如何添加、管理和检索资源。

## 支持的格式

| 格式 | 扩展名 | 处理方式 |
|------|--------|----------|
| PDF | `.pdf` | 文本和图像提取 |
| Markdown | `.md` | 原生支持 |
| HTML | `.html`, `.htm` | 清洗后文本提取 |
| 纯文本 | `.txt` | 直接导入 |
| JSON/YAML | `.json`, `.yaml`, `.yml` | 结构化解析 |
| 代码 | `.py`, `.js`, `.ts`, `.go`, `.java` 等 | 语法感知解析 |
| 图像 | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | VLM 描述 |
| 视频 | `.mp4`, `.mov`, `.avi` | 帧提取 + VLM |
| 音频 | `.mp3`, `.wav`, `.m4a` | 语音转录 |
| 文档 | `.docx` | 文本提取 |

## 处理流程

```
Input -> Parser -> TreeBuilder -> AGFS -> SemanticQueue -> Vector Index
```

1. **Parser**：根据文件类型提取内容
2. **TreeBuilder**：创建目录结构
3. **AGFS**：将文件存储到虚拟文件系统
4. **SemanticQueue**：异步生成 L0/L1
5. **Vector Index**：建立语义搜索索引

## API 参考

### add_resource()

向知识库添加资源。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| path | str | 是 | - | 本地文件路径、目录路径或 URL |
| target | str | 否 | None | 目标 Viking URI（必须在 `resources` 作用域内） |
| reason | str | 否 | "" | 添加该资源的原因（可提升搜索相关性） |
| instruction | str | 否 | "" | 特殊处理指令 |
| wait | bool | 否 | False | 等待语义处理完成 |
| timeout | float | 否 | None | 超时时间（秒），仅在 wait=True 时生效 |

**Python SDK (Embedded / HTTP)**

```python
result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

client.wait_processed()
```

**HTTP API**

```
POST /api/v1/resources
```

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "./documents/guide.md",
    "reason": "User guide documentation"
  }'
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --reason "User guide documentation"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/documents/guide.md",
    "source_path": "./documents/guide.md",
    "errors": []
  },
  "time": 0.1
}
```

**示例：从 URL 添加**

**Python SDK (Embedded / HTTP)**

```python
result = client.add_resource(
    "https://example.com/api-docs.md",
    target="viking://resources/external/",
    reason="External API documentation"
)
client.wait_processed()
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/api-docs.md",
    "target": "viking://resources/external/",
    "reason": "External API documentation",
    "wait": true
  }'
```

**CLI**

```bash
openviking add-resource https://example.com/api-docs.md --to viking://resources/external/ --reason "External API documentation"
```

**示例：等待处理完成**

**Python SDK (Embedded / HTTP)**

```python
# 方式 1：内联等待
result = client.add_resource("./documents/guide.md", wait=True)
print(f"Queue status: {result['queue_status']}")

# 方式 2：单独等待（适用于批量处理）
client.add_resource("./file1.md")
client.add_resource("./file2.md")
client.add_resource("./file3.md")

status = client.wait_processed()
print(f"All processed: {status}")
```

**HTTP API**

```bash
# 内联等待
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"path": "./documents/guide.md", "wait": true}'

# 批量添加后单独等待
curl -X POST http://localhost:1933/api/v1/system/wait \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{}'
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --wait
```

---

### export_ovpack()

将资源树导出为 `.ovpack` 文件。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要导出的 Viking URI |
| to | str | 是 | - | 目标文件路径 |

**Python SDK (Embedded / HTTP)**

```python
path = client.export_ovpack(
    "viking://resources/my-project/",
    "./exports/my-project.ovpack"
)
print(f"Exported to: {path}")
```

**HTTP API**

```
POST /api/v1/pack/export
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/my-project/",
    "to": "./exports/my-project.ovpack"
  }'
```

**CLI**

```bash
openviking export viking://resources/my-project/ ./exports/my-project.ovpack
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "file": "./exports/my-project.ovpack"
  },
  "time": 0.1
}
```

---

### import_ovpack()

导入 `.ovpack` 文件。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file_path | str | 是 | - | 本地 `.ovpack` 文件路径 |
| parent | str | 是 | - | 目标父级 URI |
| force | bool | 否 | False | 覆盖已有资源 |
| vectorize | bool | 否 | True | 导入后触发向量化 |

**Python SDK (Embedded / HTTP)**

```python
uri = client.import_ovpack(
    "./exports/my-project.ovpack",
    "viking://resources/imported/",
    force=True,
    vectorize=True
)
print(f"Imported to: {uri}")

client.wait_processed()
```

**HTTP API**

```
POST /api/v1/pack/import
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "file_path": "./exports/my-project.ovpack",
    "parent": "viking://resources/imported/",
    "force": true,
    "vectorize": true
  }'
```

**CLI**

```bash
openviking import ./exports/my-project.ovpack viking://resources/imported/ --force
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/imported/my-project/"
  },
  "time": 0.1
}
```

---

## 管理资源

### 列出资源

**Python SDK (Embedded / HTTP)**

```python
# 列出所有资源
entries = client.ls("viking://resources/")

# 列出详细信息
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['name']} - {type_str}")

# 简单路径列表
paths = client.ls("viking://resources/", simple=True)
# Returns: ["project-a/", "project-b/", "shared/"]

# 递归列出
all_entries = client.ls("viking://resources/", recursive=True)
```

**HTTP API**

```
GET /api/v1/fs/ls?uri={uri}&simple={bool}&recursive={bool}
```

```bash
# 列出所有资源
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/" \
  -H "X-API-Key: your-key"

# 简单路径列表
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/&simple=true" \
  -H "X-API-Key: your-key"

# 递归列出
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/&recursive=true" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
# 列出所有资源
openviking ls viking://resources/

# 简单路径列表
openviking ls viking://resources/ --simple

# 递归列出
openviking ls viking://resources/ --recursive
```

**响应**

```json
{
  "status": "ok",
  "result": [
    {
      "name": "project-a",
      "size": 4096,
      "isDir": true,
      "uri": "viking://resources/project-a/"
    }
  ],
  "time": 0.1
}
```

---

### 读取资源内容

**Python SDK (Embedded / HTTP)**

```python
# L0：摘要
abstract = client.abstract("viking://resources/docs/")

# L1：概览
overview = client.overview("viking://resources/docs/")

# L2：完整内容
content = client.read("viking://resources/docs/api.md")
```

**HTTP API**

```bash
# L0：摘要
curl -X GET "http://localhost:1933/api/v1/content/abstract?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"

# L1：概览
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"

# L2：完整内容
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
# L0：摘要
openviking abstract viking://resources/docs/

# L1：概览
openviking overview viking://resources/docs/

# L2：完整内容
openviking read viking://resources/docs/api.md
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

### 移动资源

**Python SDK (Embedded / HTTP)**

```python
client.mv(
    "viking://resources/old-project/",
    "viking://resources/new-project/"
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
    "from_uri": "viking://resources/old-project/",
    "to_uri": "viking://resources/new-project/"
  }'
```

**CLI**

```bash
openviking mv viking://resources/old-project/ viking://resources/new-project/
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/old-project/",
    "to": "viking://resources/new-project/"
  },
  "time": 0.1
}
```

---

### 删除资源

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
# 删除单个文件
openviking rm viking://resources/docs/old.md

# 递归删除目录
openviking rm viking://resources/old-project/ --recursive
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

### 创建链接

**Python SDK (Embedded / HTTP)**

```python
# 链接相关资源
client.link(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/",
    reason="Security best practices for authentication"
)

# 多个链接
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
# 单个链接
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uris": "viking://resources/docs/security/",
    "reason": "Security best practices for authentication"
  }'

# 多个链接
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

### 获取关联

**Python SDK (Embedded / HTTP)**

```python
relations = client.relations("viking://resources/docs/auth/")
for rel in relations:
    print(f"{rel['uri']}: {rel['reason']}")
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

### 删除链接

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

## 最佳实践

### 按项目组织

```
viking://resources/
+-- project-a/
|   +-- docs/
|   +-- specs/
|   +-- references/
+-- project-b/
|   +-- ...
+-- shared/
    +-- common-docs/
```

## 相关文档

- [检索](06-retrieval.md) - 搜索资源
- [文件系统](03-filesystem.md) - 文件系统操作
- [上下文类型](../concepts/02-context-types.md) - 资源概念
