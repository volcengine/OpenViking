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
| 飞书/Lark | URL（`*.feishu.cn`、`*.larksuite.com`、`*.larkoffice.com`） | 云端文档解析（`lark-oapi`） |

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
| path | str | 是 | - | SDK/CLI 可传本地路径、目录路径或 URL；裸 HTTP 仅支持远端 URL |
| temp_file_id | str | 否 | None | `POST /api/v1/resources/temp_upload` 返回的上传 ID，用于裸 HTTP 导入本地文件 |
| to | str | 否 | None | 精确目标 Viking URI（必须在 `resources` 作用域内） |
| parent | str | 否 | None | 资源将被放入的父级 URI，不能与 `to` 同时使用 |
| reason | str | 否 | "" | 添加该资源的原因（可提升搜索相关性） |
| instruction | str | 否 | "" | 特殊处理指令 |
| wait | bool | 否 | False | 等待语义处理完成 |
| timeout | float | 否 | None | 超时时间（秒），仅在 wait=True 时生效 |
| watch_interval | float | 否 | 0 | 定时更新间隔（分钟）。>0 开启/更新定时任务；<=0 关闭（停用）定时任务。仅在指定 `to` 时生效 |

`POST /api/v1/resources` 的 JSON 字段名是 `to`；CLI 对应参数是 `--to`。

**本地文件和目录如何处理**

- Python SDK 和 CLI 可以直接接收本地文件和目录路径。处于 HTTP 模式时，它们会先自动上传，再调用服务端 API。
- 裸 HTTP 调用可以按两类理解：
  - 远端资源：直接传 `path`，例如 `https://example.com/doc.pdf`
  - 本地文件：先调用 `POST /api/v1/resources/temp_upload`，再把返回的 `temp_file_id` 传给目标 API
  - 本地目录：先自行打成 `.zip`，上传该压缩包，再把返回的 `temp_file_id` 传给目标 API
- `POST /api/v1/resources` 不接受 `./guide.md`、`/tmp/guide.md`、`/tmp/my-dir/` 这类宿主机本地路径。

**增量更新（Incremental Update）**

当你为同一个资源 URI 反复调用 `add_resource()` 时，系统会走“增量更新”而不是每次全量重建：

- **触发条件**：请求里显式指定 `to`，且该 `to` 在知识库中已存在。
- **总体思路**：每次导入都会先把新内容解析/构建成一棵“临时资源树”，随后在异步语义处理阶段，将临时树与 `to` 对应的现有资源树进行对比，只对发生变化的部分做重算与同步。
- **语义阶段的增量**：
  - 对**未变化的文件**：复用已有 L0（摘要）与向量索引记录，跳过向量化。
  - 对**发生变化的文件**：重新生成摘要/向量索引。
  - 对**目录级 L0/L1（abstract/overview）**：若目录下子项及其变更状态不变，则复用已有结果并跳过向量化；否则重算并更新。
- **落盘与索引同步**：语义 DAG 结束后会对临时树与 `to` 做一次 top-down diff，同步三类变更：新增（添加新文件/目录）、删除（移除消失项）、更新（覆盖变化项）。同步过程中会同时联动更新向量库中的记录：删除项会删除对应向量记录；移动/覆盖会同步更新向量记录的 URI 映射，从而完成“文件树与向量索引的一致性增量更新”。

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
    "path": "https://example.com/guide.md",
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
    to="viking://resources/external/",
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
    "to": "viking://resources/external/",
    "reason": "External API documentation",
    "wait": true
  }'
```

**CLI**

```bash
openviking add-resource https://example.com/api-docs.md --to viking://resources/external/ --reason "External API documentation"
```

**示例：用裸 HTTP 添加本地文件**

如果你直接调用 HTTP API，本地文件要先上传，再使用 `temp_file_id`。

```bash
# 第一步：上传本地文件
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./documents/guide.md' \
  | jq -r '.result.temp_file_id'
)

# 第二步：用 temp_file_id 添加资源
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"reason\": \"User guide documentation\",
    \"wait\": true
  }"
```

**示例：用裸 HTTP 添加本地目录**

如果你直接调用 HTTP API，本地目录需要先自行打成 zip。CLI 和 SDK 会自动完成这一步。

```bash
# 第一步：先把本地目录打成 zip
cd ./documents
zip -r /tmp/guide.zip ./guide

# 第二步：上传 zip 文件
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@/tmp/guide.zip' \
  | jq -r '.result.temp_file_id'
)

# 第三步：用上传后的目录压缩包添加资源
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"reason\": \"Import local directory\",
    \"wait\": true
  }"
```

**示例：添加飞书/Lark 云端文档**

[飞书](https://www.feishu.cn)及其国际版 [Lark](https://www.larksuite.com) 是国内科技公司广泛使用的协作平台。OpenViking 可以通过 URL 直接导入飞书云端文档。

支持的文档类型：

| 类型 | URL 格式 |
|------|----------|
| 文档 | `https://*.feishu.cn/docx/{id}` |
| 知识库 | `https://*.feishu.cn/wiki/{token}` |
| 电子表格 | `https://*.feishu.cn/sheets/{token}` |
| 多维表格 | `https://*.feishu.cn/base/{token}` |

> **前置配置**：安装可选依赖 `pip install 'openviking[bot-feishu]'`
>
> 通过 `ov.conf` 配置凭据（详见[配置文档](../../zh/guides/01-configuration.md#feishu)），或设置环境变量：
> ```bash
> export FEISHU_APP_ID="cli_xxx"
> export FEISHU_APP_SECRET="xxx"
> ```

**Python SDK (Embedded / HTTP)**

```python
# 导入飞书文档
result = client.add_resource(
    "https://example.feishu.cn/docx/doxcnABC123",
    reason="项目设计文档"
)
client.wait_processed()

# 导入知识库页面（自动解析为底层文档类型）
client.add_resource("https://example.feishu.cn/wiki/wikiXYZ")

# 导入电子表格
client.add_resource("https://example.feishu.cn/sheets/shtcn456")
```

**CLI**

```bash
# 导入飞书文档
openviking add-resource "https://example.feishu.cn/docx/doxcnABC123" --reason "项目设计文档"

# 导入知识库页面
openviking add-resource "https://example.feishu.cn/wiki/wikiXYZ"

# 增量更新到已有 URI
openviking add-resource "https://example.feishu.cn/docx/doxcnABC123" --to viking://resources/design-doc
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
  -d '{"path": "https://example.com/guide.md", "wait": true}'

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

**示例：开启定时更新（watch_interval）**

`watch_interval` 的单位为分钟，用于对指定目标 URI 定期触发更新处理：

- `watch_interval > 0`：创建（或重新激活并更新）该 `to` 的定时任务
- `watch_interval <= 0`：关闭（停用）该 `to` 的定时任务
- 只有在指定 `to` / CLI `--to` 时才会创建定时任务

如果同一个 `to` 已存在激活中的定时任务，再次以 `watch_interval > 0` 提交会返回冲突错误；需要先将 `watch_interval` 设为 `0`（取消/停用）后再重新设置新的间隔。

**Python SDK (Embedded / HTTP)**

```python
client.add_resource(
    "./documents/guide.md",
    to="viking://resources/documents/guide.md",
    watch_interval=60,
)
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "to": "viking://resources/documents/guide.md",
    "watch_interval": 60
  }'
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --to viking://resources/documents/guide.md --watch-interval 60

# 取消监控
openviking add-resource ./documents/guide.md --to viking://resources/documents/guide.md --watch-interval 0
```

---

### export_ovpack()

将资源树导出为 `.ovpack` 文件。

**SDK / CLI 参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要导出的 Viking URI |
| to | str | 是 | - | 目标文件路径 |

**裸 HTTP 请求体**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要导出的 Viking URI |

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
    "uri": "viking://resources/my-project/"
  }'
```

**CLI**

```bash
openviking export viking://resources/my-project/ ./exports/my-project.ovpack
```

**响应**

该 HTTP 接口会直接以下载流的形式返回 `.ovpack` 文件（`Content-Type: application/zip`），不会返回 JSON 包装体。

---

### import_ovpack()

导入 `.ovpack` 文件。

**SDK / CLI 参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| file_path | str | 是 | - | 本地 `.ovpack` 文件路径 |
| parent | str | 是 | - | 目标父级 URI |
| force | bool | 否 | False | 覆盖已有资源 |
| vectorize | bool | 否 | True | 导入后触发向量化 |

**裸 HTTP 请求体**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| temp_file_id | str | 是 | - | `POST /api/v1/resources/temp_upload` 返回的上传 ID |
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
# 第一步：上传本地 ovpack 文件
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/my-project.ovpack' \
  | jq -r '.result.temp_file_id'
)

# 第二步：使用 temp_file_id 导入
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"force\": true,
    \"vectorize\": true
  }"
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
