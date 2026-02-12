# 技能

技能是智能体可以调用的能力。本指南介绍如何添加和管理技能。

## API 参考

### add_skill()

向知识库添加技能。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| data | Any | 是 | - | 技能数据（字典、字符串或路径） |
| wait | bool | 否 | False | 等待向量化完成 |
| timeout | float | 否 | None | 超时时间（秒） |

**支持的数据格式**

1. **字典（技能格式）**：
```python
{
    "name": "skill-name",
    "description": "Skill description",
    "content": "Full markdown content",
    "allowed_tools": ["Tool1", "Tool2"],  # 可选
    "tags": ["tag1", "tag2"]  # 可选
}
```

2. **字典（MCP Tool 格式）** - 自动检测并转换：
```python
{
    "name": "tool_name",
    "description": "Tool description",
    "inputSchema": {
        "type": "object",
        "properties": {...},
        "required": [...]
    }
}
```

3. **字符串（SKILL.md 内容）**：
```python
"""---
name: skill-name
description: Skill description
---

# Skill Content
"""
```

4. **路径（文件或目录）**：
   - 单个文件：指向 `SKILL.md` 文件的路径
   - 目录：指向包含 `SKILL.md` 的目录路径（辅助文件会一并包含）

**Python SDK (Embedded / HTTP)**

```python
skill = {
    "name": "search-web",
    "description": "Search the web for current information",
    "content": """
# search-web

Search the web for current information.

## Parameters
- **query** (string, required): Search query
- **limit** (integer, optional): Max results, default 10
"""
}

result = client.add_skill(skill)
print(f"Added: {result['uri']}")
```

**HTTP API**

```
POST /api/v1/skills
```

```bash
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "search-web",
      "description": "Search the web for current information",
      "content": "# search-web\n\nSearch the web for current information.\n\n## Parameters\n- **query** (string, required): Search query\n- **limit** (integer, optional): Max results, default 10"
    }
  }'
```

**CLI**

```bash
openviking add-skill ./my-skill/ [--wait]
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "uri": "viking://agent/skills/search-web/",
    "name": "search-web",
    "auxiliary_files": 0
  },
  "time": 0.1
}
```

**示例：从 MCP Tool 添加**

**Python SDK (Embedded / HTTP)**

```python
# MCP tool 格式会被自动检测并转换
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
```

**HTTP API**

```bash
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
```

**示例：从 SKILL.md 文件添加**

**Python SDK (Embedded / HTTP)**

```python
# 从文件路径添加
result = client.add_skill("./skills/search-web/SKILL.md")
print(f"Added: {result['uri']}")

# 从目录添加（包含辅助文件）
result = client.add_skill("./skills/code-runner/")
print(f"Added: {result['uri']}")
print(f"Auxiliary files: {result['auxiliary_files']}")
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": "./skills/search-web/SKILL.md"
  }'
```

---

## SKILL.md 格式

技能可以使用带有 YAML frontmatter 的 SKILL.md 文件来定义。

**结构**

```markdown
---
name: skill-name
description: Brief description of the skill
allowed-tools:
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

**必填字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 技能名称（建议使用 kebab-case） |
| description | str | 简要描述 |

**可选字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| allowed-tools | List[str] | 该技能可使用的工具 |
| tags | List[str] | 用于分类的标签 |

---

## 管理技能

### 列出技能

**Python SDK (Embedded / HTTP)**

```python
# 列出所有技能
skills = client.ls("viking://agent/skills/")
for skill in skills:
    print(f"{skill['name']}")

# 简单列表（仅名称）
names = client.ls("viking://agent/skills/", simple=True)
print(names)
```

**HTTP API**

```bash
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://agent/skills/" \
  -H "X-API-Key: your-key"
```

### 读取技能内容

**Python SDK (Embedded / HTTP)**

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

**HTTP API**

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

**Python SDK (Embedded / HTTP)**

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

**HTTP API**

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

**Python SDK (Embedded / HTTP)**

```python
client.rm("viking://agent/skills/old-skill/", recursive=True)
```

**HTTP API**

```bash
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://agent/skills/old-skill/&recursive=true" \
  -H "X-API-Key: your-key"
```

---

## MCP 转换

OpenViking 会自动检测并将 MCP tool 定义转换为技能格式。

**检测**

如果字典包含 `inputSchema` 字段，则被视为 MCP 格式：

```python
if "inputSchema" in data:
    # 转换为技能格式
    skill = mcp_to_skill(data)
```

**转换过程**

1. 名称转换为 kebab-case
2. 描述保持不变
3. 从 `inputSchema.properties` 中提取参数
4. 从 `inputSchema.required` 中标记必填字段
5. 生成 Markdown 内容

**转换示例**

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

---

## 技能存储结构

技能存储在 `viking://agent/skills/` 路径下：

```
viking://agent/skills/
+-- search-web/
|   +-- .abstract.md      # L0：简要描述
|   +-- .overview.md      # L1：参数和使用概览
|   +-- SKILL.md          # L2：完整文档
|   +-- [auxiliary files]  # 其他辅助文件
+-- calculator/
|   +-- .abstract.md
|   +-- .overview.md
|   +-- SKILL.md
+-- ...
```

---

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

### 全面的内容

技能内容应包含：
- 清晰的参数描述及类型
- 何时使用该技能
- 具体示例
- 边界情况和限制

### 一致的命名

技能名称使用 kebab-case：
- `search-web`（推荐）
- `searchWeb`（避免）
- `search_web`（避免）

---

## 相关文档

- [上下文类型](../concepts/02-context-types.md) - 技能概念
- [检索](06-retrieval.md) - 查找技能
- [会话](05-sessions.md) - 跟踪技能使用情况
