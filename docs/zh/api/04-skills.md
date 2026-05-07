# 技能

技能是智能体可以调用的能力定义。本模块提供技能的添加和管理功能。

## 核心概念

### 技能类型

OpenViking 支持多种技能定义格式：

1. **结构化技能数据**：包含 name、description、content 等字段的字典
2. **SKILL.md 文件**：带有 YAML frontmatter 的 Markdown 文件
3. **MCP Tool 格式**：自动检测并转换为 OpenViking 技能格式

### 技能存储结构

技能存储在 `viking://agent/skills/` 路径下：

```
viking://agent/skills/
+-- search-web/
|   +-- .abstract.md      # L0：简要描述
|   +-- .overview.md      # L1：参数和使用概览
|   +-- SKILL.md          # L2：完整文档
|   +-- [auxiliary files] # 其他辅助文件
+-- calculator/
|   +-- .abstract.md
|   +-- .overview.md
|   +-- SKILL.md
+-- ...
```

### SKILL.md 格式

技能可以使用带有 YAML frontmatter 的 SKILL.md 文件来定义：

```markdown
---
name: skill-name
description: Brief description of the skill
allowed_tools:
  - Tool1
  - Tool2
tags:
  - tag1
  - tag2
---

# Skill Name

Full skill documentation in Markdown format.

## Parameters
- **param1** (type, required): Description
- **param2** (type, optional): Description

## Usage
When and how to use this skill.

## Examples
Concrete examples of skill invocation.
```

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 技能名称（建议使用 kebab-case）|
| description | str | 简要描述 |

**可选字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| allowed_tools | List[str] | 该技能可使用的工具 |
| tags | List[str] | 用于分类的标签 |

### MCP 格式自动转换

OpenViking 会自动检测并将 MCP Tool 定义转换为技能格式。

**检测规则**：如果字典包含 `inputSchema` 字段，则被视为 MCP 格式。

**转换过程**：
1. 名称转换为 kebab-case
2. 描述保持不变
3. 从 `inputSchema.properties` 中提取参数
4. 从 `inputSchema.required` 中标记必填字段
5. 生成 Markdown 内容

**转换示例**：

输入（MCP 格式）：
```python
{
    "name": "search_web",
    "description": "Search the web",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query"
            },
            "limit": {
                "type": "integer",
                "description": "Max results"
            }
        },
        "required": ["query"]
    }
}
```

输出（技能格式）：
```python
{
    "name": "search-web",
    "description": "Search the web",
    "content": """---
name: search-web
description: Search the web
---

# search-web

Search the web

## Parameters

- **query** (string) (required): Search query
- **limit** (integer) (optional): Max results

## Usage

This tool wraps the MCP tool `search-web`. Call this when the user needs functionality matching the description above.
"""
}
```

## API 参考

### add_skill

向知识库添加技能。

#### 1. API 实现介绍

技能是一种特殊的资源，用于定义智能体可以执行的操作或工具。

**处理流程**：
1. 接收技能数据或上传的临时文件
2. 检测数据格式（结构化数据、SKILL.md 内容、MCP 格式）
3. 解析技能定义
4. 存储到 `viking://agent/skills/` 路径下
5. 如指定 `wait=True`，等待向量化完成

**代码入口**：
- `openviking/client/local.py:LocalClient.add_skill` - SDK 入口（嵌入式）
- `openviking_cli/client/http.py:AsyncHTTPClient.add_skill` - SDK 入口（HTTP）
- `openviking/server/routers/resources.py:add_skill` - HTTP 路由
- `openviking/service/resource_service.py:ResourceService.add_skill` - 核心服务实现
- `crates/ov_cli/src/handlers.rs:handle_add_skill` - CLI 处理

#### 2. 接口和参数说明

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| data | Any | 否 | - | 内联技能内容或结构化数据。与 `temp_file_id` 二选一 |
| temp_file_id | string | 否 | - | 临时上传文件 ID（通过 `temp_upload` 获取）。与 `data` 二选一 |
| wait | bool | 否 | False | 是否等待技能处理完成 |
| timeout | float | 否 | None | 超时时间（秒），仅 `wait=True` 时生效 |
| telemetry | TelemetryRequest | 否 | False | 是否返回遥测数据 |

**补充说明**：

- **本地文件处理**：
  - Python SDK 和 CLI 可以直接接收本地 `SKILL.md` 文件或目录。处于 HTTP 模式时，它们会先自动上传，再调用服务端 API。
  - 裸 HTTP 调用有三种推荐方式：
    1. 在 `data` 中直接传结构化 skill 数据
    2. 在 `data` 中直接传原始 `SKILL.md` 内容
    3. 先调用 `POST /api/v1/resources/temp_upload` 上传本地 `SKILL.md` 文件/zip 目录，再调用 `POST /api/v1/skills` 并传入 `temp_file_id`
  - `POST /api/v1/skills` 不接受在 `data` 中直接传宿主机本地路径。

- **支持的数据格式**：
  1. **字典（技能格式）**：包含 `name`、`description`、`content` 等字段
  2. **字典（MCP Tool 格式）**：包含 `name`、`description`、`inputSchema` 字段，会自动检测并转换
  3. **字符串（SKILL.md 内容）**：完整的 SKILL.md 内容
  4. **路径（文件或目录）**：指向 `SKILL.md` 文件的路径，或包含 `SKILL.md` 的目录路径（辅助文件会一并包含）

#### 3. 使用示例

**HTTP API**：

```
POST /api/v1/skills
Content-Type: application/json
```

```bash
# 使用内联结构化数据
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "search-web",
      "description": "Search the web for current information",
      "content": "# search-web\n\nSearch the web for current information.\n\n## Parameters\n- **query** (string, required): Search query\n- **limit** (integer, optional): Max results, default 10"
    },
    "wait": true
  }'

# 使用内联 SKILL.md 内容
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": "---\nname: my-skill\ndescription: My custom skill\n---\n\n# My Skill\n\nSkill content here."
  }'

# 使用 MCP Tool 格式（自动检测并转换）
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "calculator",
      "description": "Perform mathematical calculations",
      "inputSchema": {
        "type": "object",
        "properties": {
          "expression": {
            "type": "string",
            "description": "Mathematical expression to evaluate"
          }
        },
        "required": ["expression"]
      }
    }
  }'

# 使用本地文件（需先使用 temp_upload 上传）
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F "file=@./skills/my-skill.json" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\"
  }"
```

**Python SDK**：

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# 方式 1：使用结构化技能数据
skill = {
    "name": "search-web",
    "description": "Search the web for current information",
    "content": """# search-web

Search the web for current information.

## Parameters
- **query** (string, required): Search query
- **limit** (integer, optional): Max results, default 10
"""
}
result = client.add_skill(skill)
print(f"Added: {result['root_uri']}")

# 方式 2：使用 MCP Tool 格式（自动检测并转换）
mcp_tool = {
    "name": "calculator",
    "description": "Perform mathematical calculations",
    "inputSchema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate"
            }
        },
        "required": ["expression"]
    }
}
result = client.add_skill(mcp_tool)
print(f"Added: {result['uri']}")

# 方式 3：从本地 SKILL.md 文件添加
result = client.add_skill("./skills/search-web/SKILL.md")
print(f"Added: {result['uri']}")

# 方式 4：从包含 SKILL.md 的目录添加（辅助文件会一并包含）
result = client.add_skill("./skills/code-runner/")
print(f"Added: {result['uri']}")
print(f"Auxiliary files: {result['auxiliary_files']}")

# 等待处理完成
result = client.add_skill("./skills/my-skill/", wait=True)
client.wait_processed()
```

**CLI**：

```bash
# 添加技能（从文件或目录）
ov add-skill ./skills/my-skill.json
ov add-skill ./skills/search-web/SKILL.md
ov add-skill ./skills/code-runner/

# 等待处理完成
ov add-skill ./skills/my-skill/ --wait

# 使用 JSON 输出格式
ov add-skill ./skills/my-skill/ -o json
```

**响应示例**：

**HTTP API 响应 (JSON)**：
```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://agent/skills/my-skill/",
    "uri": "viking://agent/skills/my-skill/",
    "name": "my-skill",
    "auxiliary_files": 2,
    "queue_status": {
      "pending": 0,
      "processing": 0,
      "completed": 1
    }
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "time": 0.1
}
```

**CLI 响应（默认表格格式）**：
```
Note: Skill is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status          success
root_uri        viking://agent/skills/my-skill
uri             viking://agent/skills/my-skill
name            my-skill
auxiliary_files 2
```

**CLI 响应（JSON 格式，使用 -o json）**：
```json
{
  "status": "success",
  "root_uri": "viking://agent/skills/my-skill",
  "uri": "viking://agent/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 处理状态：`success` 成功，`error` 失败 |
| `root_uri` | string | 技能在 OpenViking 中的最终 URI（同 `uri`）|
| `uri` | string | 技能在 OpenViking 中的最终 URI（同 `root_uri`）|
| `name` | string | 技能名称 |
| `auxiliary_files` | number | 技能附带的辅助文件数量 |
| `queue_status` | object | （可选，仅当 `wait=True` 时）队列处理状态，包含 `pending`、`processing`、`completed` 计数 |

#### 4. 错误处理

**同步处理错误**：

如果 skill 解析或处理同步失败，裸 HTTP 会返回标准错误 envelope，并使用非 2xx HTTP 状态码：

```json
{
  "status": "error",
  "error": {
    "code": "PROCESSING_ERROR",
    "message": "Skill parse error: invalid skill metadata"
  }
}
```

Python HTTP SDK 会把该响应映射为对应异常（`ProcessingError`）。

## 技能管理操作

### 列出技能

**Python SDK**：

```python
# 列出所有技能
skills = client.ls("viking://agent/skills/")
for skill in skills:
    print(f"{skill['name']}")

# 简单列表（仅名称）
names = client.ls("viking://agent/skills/", simple=True)
print(names)
```

**HTTP API**：

```bash
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://agent/skills/" \
  -H "X-API-Key: your-key"
```

### 读取技能内容

**Python SDK**：

```python
uri = "viking://agent/skills/search-web/"

# L0：简要描述
abstract = client.abstract(uri)
print(f"Abstract: {abstract}")

# L1：参数和使用概览
overview = client.overview(uri)
print(f"Overview: {overview}")

# L2：完整技能文档
content = client.read(uri)
print(f"Content: {content}")
```

**HTTP API**：

```bash
# L0：简要描述
curl -X GET "http://localhost:1933/api/v1/content/abstract?uri=viking://agent/skills/search-web/" \
  -H "X-API-Key: your-key"

# L1：参数和使用概览
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://agent/skills/search-web/" \
  -H "X-API-Key: your-key"

# L2：完整技能文档
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://agent/skills/search-web/" \
  -H "X-API-Key: your-key"
```

### 搜索技能

**Python SDK**：

```python
# 语义搜索技能
results = client.find(
    "search the internet",
    target_uri="viking://agent/skills/",
    limit=5
)

for ctx in results.skills:
    print(f"Skill: {ctx.uri}")
    print(f"Score: {ctx.score:.3f}")
    print(f"Description: {ctx.abstract}")
```

**HTTP API**：

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "search the internet",
    "target_uri": "viking://agent/skills/",
    "limit": 5
  }'
```

### 删除技能

**Python SDK**：

```python
client.rm("viking://agent/skills/old-skill/", recursive=True)
```

**HTTP API**：

```bash
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://agent/skills/old-skill/&recursive=true" \
  -H "X-API-Key: your-key"
```

## 最佳实践

### 清晰的描述

```python
# 好 - 具体且可操作
skill = {
    "name": "search-web",
    "description": "Search the web for current information using Google",
    ...
}

# 不够好 - 过于模糊
skill = {
    "name": "search",
    "description": "Search",
    ...
}
```

### 命名一致性建议

技能名称使用 kebab-case：
- `search-web`（推荐）
- `searchWeb`（避免）
- `search_web`（避免）

## 相关文档

- [资源管理](02-resources.md) - 资源的添加和管理
- [文件系统](03-filesystem.md) - 文件和目录操作
- [上下文类型](../concepts/02-context-types.md) - 技能概念
- [检索](06-retrieval.md) - 查找技能
- [会话](05-sessions.md) - 跟踪技能使用情况
