# 技能

技能是供智能体读取的任务指令与配套资源。OpenViking 负责存储、检索和管理技能；读取后的激活、工具权限与执行由使用它的 Agent/Harness 负责。本文以当前仓库的 HTTP API、SDK 和 `ov` CLI 实现为准。

## 核心概念

### 技能类型

OpenViking 支持多种技能定义格式：

1. **结构化技能数据**：包含 name、description、content 等字段的字典
2. **SKILL.md 文件**：带有 YAML frontmatter 的 Markdown 文件
3. **MCP Tool 格式**：自动检测并转换为技能文档，不会连接 MCP Server 或注册可执行工具
4. **Skill 包与集合**：包含 `SKILL.md` 和辅助文件的目录、ZIP，或 Git 仓库 / GitHub tree URL

### 技能存储结构

技能支持当前用户私有根 `viking://user/{user_id}/skills/` 和账户内共享根 `viking://agent/skills/`。新增时的目标优先级为：请求 `target_uri` → 用户 `add_targets.skill_uri` → 服务端 `user_config_defaults.add_targets.skill_uri` → 当前用户私有根。所有访问仍受当前身份和权限约束。

家目录别名 `viking://~/skills/` 始终按认证身份展开为当前用户的私有根，不随默认新增目标改变。无 uid 的 `viking://user/skills/` 和 peer-scoped skills 均不支持。私有根的包结构如下；共享根使用相同结构：

```
viking://user/{user_id}/skills/
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

### 整包处理与索引

新增 Skill 默认处理整个包，不需要额外开关。主目录 L0 仍来自技能元数据，L1 仍只根据 `SKILL.md` 生成；子目录沿用 resource 的方式生成 L0、L1。目录 L0、L1 和支持的文件 L2 都建立索引，包内嵌套的 `SKILL.md` 作为普通附件处理。

辅助文件会进入已有的摘要和 embedding 处理流程，受现有隐藏文件过滤、长度上限和媒体配置约束。图片使用模型支持的图片或文字输入；音视频使用文字摘要，无法理解时沿用文件名回退。`wait=true` 等待整个包处理完成。升级不会自动重建旧 Skill；首次补齐应使用 `semantic_and_vectors` 重建，仅重建向量不会生成缺失的目录摘要。

### SKILL.md 格式

技能可以使用带有 YAML frontmatter 的 SKILL.md 文件来定义：

```markdown
---
name: skill-name
description: Brief description of the skill
allowed-tools: Read Bash(python3 *)
tags:
  - tag1
  - tag2
metadata:
  author: example-team
  vikingbot:
    requires:
      bins: [python3]
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
| name | str | 非空，最多 64 个 ASCII 字母、数字、下划线或连字符；建议 kebab-case |
| description | str | 简要描述 |

**可选字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| allowed-tools | str / List[str] | 空格分隔的工具声明，也兼容字符串列表；括号内可含空格，由消费方解释和执行策略 |
| tags | List[str] | 用于分类的标签 |
| metadata | object | 原样保留的扩展字段，如 `metadata.vikingbot.requires`；OpenViking 不自动安装这些依赖 |

`SKILL.md` 使用带连字符的 **`allowed-tools`**，解析后的结构化数据和 API 摘要使用 **`allowed_tools`**。不要在 frontmatter 中用下划线拼写替代它。未声明和显式空声明可能在 Harness 中有不同权限含义，摘要里的 `allowed_tools: []` 不能区分二者；执行前应读取完整 `SKILL.md`。正文与扩展字段的消费方式见 [VikingBot Skills](../../../bot/docs/zh/concepts/06-skills.md)。

### MCP 格式自动转换

OpenViking 会自动检测并将 MCP Tool 定义转换为技能格式。

**检测规则**：如果字典包含 `inputSchema` 字段，则被视为 MCP 格式。

**转换过程**：
1. 将名称中的下划线替换为连字符（不会自动转换 camelCase）
2. 描述保持不变
3. 从 `inputSchema.properties` 中提取参数
4. 从 `inputSchema.required` 中标记必填字段
5. 生成 Markdown 内容

**转换示例**：

输入（MCP 格式）：
```json
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

输出（Python 字典形式）：
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
4. 将包存储到选定的用户私有或账户共享 skills 根
5. 默认返回 `task_id`，用于查询后台向量化任务

**代码入口**：
- `sdk/python/openviking_sdk/client.py:AsyncHTTPClient.add_skill` - Python SDK 入口
- `openviking_cli/client/http.py` - 兼容导入入口，转发到 Python SDK
- `openviking/server/routers/resources.py:add_skill` - HTTP 路由
- `openviking/server/mcp_endpoint.py:add_skill` - MCP 工具
- `openviking/server/skill_ingest.py:install_skills` - HTTP 路由、MCP 工具与 skill 签名上传共用的安装实现
- `openviking/service/resource_service.py:ResourceService.add_skill` - 核心服务实现
- `openviking/server/routers/skills.py` - 列表、检索、读取、校验、更新和删除
- `crates/ov_cli/src/commands/skills.rs` - CLI Skill 命令处理

#### 2. 接口和参数说明

**HTTP 请求体参数**（`POST /api/v1/skills`）：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| data | Any | 否 | - | 内联 SKILL.md、结构化/MCP 数据或 Git URL；与 `temp_file_id` 选择一个 |
| temp_file_id | string | 否 | - | 临时上传文件 ID（通过 `temp_upload` 获取）；与 `data` 选择一个 |
| wait | bool | 否 | False | 是否等待技能处理完成 |
| timeout | float | 否 | None | 超时时间（秒），仅 `wait=True` 时生效 |
| telemetry | TelemetryRequest | 否 | False | 是否返回遥测数据 |
| target_uri | string | 否 | 按目标优先级解析 | 目标 skills 根，例如 `viking://~/skills` 或 `viking://agent/skills` |
| skills | List[str] | 否 | `[]` | 从目录/Git 集合按目录名选择 Skill；空列表或 `["*"]` 选择全部 |
| list_only | bool | 否 | False | 只列出来源中的 Skill，不安装；需可解析的文件、目录或 Git 来源，不能使用内联字典 |
| source_metadata | object | 否 | 自动生成 | 来源跟踪信息，保存在 `.source.json`；不是 frontmatter 的 `metadata` |

至少提供 `data` 或 `temp_file_id`。当前服务端两者同时存在时优先消费临时上传；客户端应只传一种，避免来源混淆。请求体不接受未知字段。

**补充说明**：

- **本地文件处理**：
  - Python SDK 和 CLI 可以直接接收本地 `SKILL.md` 文件或目录。处于 HTTP 模式时，它们会先自动上传，再调用服务端 API。
  - 裸 HTTP 调用可以：
    1. 在 `data` 中直接传结构化 skill 数据
    2. 在 `data` 中直接传原始 `SKILL.md` 内容
    3. 直接传 Git 仓库或 GitHub tree URL，让服务端解析来源
    4. 先调用 `POST /api/v1/resources/temp_upload` 上传本地 `SKILL.md` 或目录 ZIP，再调用 `POST /api/v1/skills` 并传入 `temp_file_id`
  - `temp_upload` 默认使用本地临时存储；只有在明确需要分布式共享临时上传时，才传 `upload_mode=shared`。Python HTTP client 可以在 `ovcli.conf` 中设置 `upload.mode = "shared"`；Rust `ov` CLI 则使用 `OPENVIKING_UPLOAD_MODE=shared`。
  - `POST /api/v1/skills` 不接受在 `data` 中直接传宿主机本地路径。
  - MCP 客户端使用 `add_skill` 工具：`data` 传 SKILL.md 文本，`path` 传 Git URL 或本地路径。传本地路径时工具返回一次性的签名 `temp_upload` URL；客户端把 SKILL.md 或 ZIP POST 上去后，服务端按 token 绑定的 `target_uri`、`skills`、`list_only` 完成安装，上传响应里就是安装结果。

- **目标规则**：
  - 新增使用 `target_uri` 指定 skills 根，不接受 `to`、`parent` 或 `root_uri` 作为 HTTP 请求字段。CLI 的 `-p/--parent-auto-create` 映射到 `target_uri`。
  - 不支持 peer-scoped skill 根；actor peer 过滤只作用于 peer memories/resources，不作用于 peer skills。
  - 列出、读取、删除或搜索技能时，使用家目录别名 `viking://~/skills/...` 访问自己的技能；无 uid 的 `viking://user/skills/...` 写法会报错并提示正确写法。

- **支持的数据格式**：
  1. **字典（技能格式）**：包含 `name`、`description`、`content` 等字段
  2. **字典（MCP Tool 格式）**：包含 `name`、`description`、`inputSchema` 字段，会自动检测并转换
  3. **字符串（SKILL.md 内容）**：完整的 SKILL.md 内容
  4. **路径（SDK/CLI 自动上传）**：SKILL.md 文件、Skill 目录/集合或 ZIP；单个 Markdown 文件不包含同目录辅助文件
  5. **Git URL**：仓库或 Skill 子目录；集合中的 `skills` 选择器使用目录名，非 frontmatter 名称。已发现 `SKILL.md` 的目录拥有整个子树，内部嵌套 `SKILL.md` 作为附件保留，不再单独发现

本地 `.json` 文件不会自动解码成结构化 Skill。应先解析 JSON 并把对象传入 `data`，或改用带 frontmatter 的 `SKILL.md`。

#### 3. 使用示例

单个 Skill 导入默认返回 `task_id`。通过 [任务 API](17-tasks.md) 查询状态，任务为 `completed` 后再检索；`wait=true` 则等待队列处理并返回 `queue_status`。多 Skill 导入返回 `installed` 数组，每个结果独立携带任务信息，不保证批次原子性。`list_only=true` 只返回来源中的 `skills` 与 `total`，不创建导入任务。

HTTP 字段和 SDK 参数并非同名同层级：Python `add_skill(data, wait=False, timeout=None, options=None)` / `update_skill(skill_name, data, ...)` 将 `target_uri`、`telemetry` 放在 `options` 中；`skills`、`list_only`、`source_metadata`（新增）及 `from_source`（更新）等字段放在 `options["extra"]` 中。TypeScript 使用 `targetUri` 和 `extra`，Go 使用 `TargetURI` 和 `Extra`。TypeScript 的本地路径自动上传仅适用于 Node.js。

```python
# 预览本地集合；不写入 OpenViking
listing = client.add_skill(
    "./skills",
    options={"extra": {"list_only": True}},
)
print(listing["skills"])

# 选择目录名，并安装到共享根（需相应权限）
result = client.add_skill(
    "./skills",
    wait=True,
    options={
        "target_uri": "viking://agent/skills",
        "extra": {"skills": ["search-web", "calculator"]},
    },
)
```

**TypeScript SDK**

```typescript
const result = await client.addSkill("./my-skill");
console.log(result.task_id);
```

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
    }
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
    -F "file=@./skills/my-skill/SKILL.md" \
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
import openviking_sdk as ov

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
result = client.add_skill(data=skill)
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
result = client.add_skill(data=mcp_tool)
print(f"Added: {result['uri']}")

# 方式 3：从本地 SKILL.md 文件添加
result = client.add_skill(data="./skills/search-web/SKILL.md")
print(f"Added: {result['uri']}")

# 方式 4：从包含 SKILL.md 的目录添加（辅助文件会一并包含）
result = client.add_skill(data="./skills/code-runner/")
print(f"Added: {result['uri']}")
print(f"Auxiliary files: {result['auxiliary_files']}")

# 查询上一次导入任务的状态
print(client.get_task(result["task_id"]))
```

**Go SDK**

```go
result, err := client.AddSkill(ctx, "./skills/my-skill/", nil)
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**：

`ov add-skill` 与 `ov skills add` 使用同一套参数和导入流程。
技能集合可以用 `--list` 查看、`--skill` 选择；批量导入需要确认，或使用 `--yes`。

```bash
# 从独立 skills 分支导入一个技能；也可以写成 ov skills add
ov add-skill https://github.com/volcengine/OpenViking/tree/skills/llm-wiki

# 查看本地技能集合，再选择导入
ov add-skill ./examples/compile/ov-compile-skills --list
ov add-skill ./examples/compile/ov-compile-skills --skill llm-wiki daily-report --yes

# 添加技能（从文件或目录）
ov add-skill ./skills/search-web/SKILL.md
ov add-skill ./skills/code-runner/

# 指定共享目标，并等待处理完成
ov skills add ./skills/code-runner/ -p viking://agent/skills --wait

# 使用提交时返回的 task_id 查询进度
ov task status TASK_ID

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
    "root_uri": "viking://user/alice/skills/my-skill",
    "uri": "viking://user/alice/skills/my-skill",
    "name": "my-skill",
    "auxiliary_files": 2,
    "task_id": "uuid-xxx"
  }
}
```

**CLI 响应（默认表格格式）**：
```
Note: Skill processing may continue in the background.
Use 'ov task status <task_id>' to check progress, or 'ov task list' to see all tasks.
status          success
root_uri        viking://user/alice/skills/my-skill
uri             viking://user/alice/skills/my-skill
name            my-skill
auxiliary_files 2
task_id         uuid-xxx
```

**CLI 响应（JSON 格式，使用 -o json）**：
```json
{
  "status": "success",
  "root_uri": "viking://user/alice/skills/my-skill",
  "uri": "viking://user/alice/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2,
  "task_id": "uuid-xxx"
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 成功导入结果为 `success`；同步失败使用 HTTP 错误响应 |
| `root_uri` | string | 技能在 OpenViking 中的 canonical 最终 URI（同 `uri`）|
| `uri` | string | 技能在 OpenViking 中的 canonical 最终 URI（同 `root_uri`）|
| `name` | string | 技能名称 |
| `auxiliary_files` | number | 技能附带的辅助文件数量 |
| `task_id` | string | 默认异步模式下返回的后台处理任务 ID；通过任务 API 查询最终状态 |
| `queue_status` | object | `wait=True` 时按队列名（如 `Semantic`、`Embedding`）返回 `processed`、`requeue_count`、`error_count` 和 `errors` |

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

具体错误码取决于失败阶段，例如名称或目标参数无效为 `INVALID_ARGUMENT` / `INVALID_URI`，找不到 Skill 为 `NOT_FOUND`，完整性清单超限为 `RESOURCE_EXHAUSTED`，等待超时为 `DEADLINE_EXCEEDED`。`wait=false` 返回成功只表示同步导入阶段完成，后台失败需通过任务状态确认。标准响应可能带可选 `telemetry` / `profile`，没有通用 `time` 字段；SDK 通常直接返回 `result` 对象。

## 技能管理操作

Python HTTP SDK 和 Go SDK 都暴露专用技能管理方法。Python 方法包括
`list_skills`、`find_skills`、`validate_skill`、`get_skill`、`update_skill`
和 `delete_skill`；Go 方法包括 `ListSkills`、`FindSkills`、`ValidateSkill`、
`GetSkill`、`UpdateSkill` 和 `DeleteSkill`。通用文件系统、内容和检索方法仍可用于 URI 级访问。

### 列出技能

`GET /api/v1/skills` 的查询参数为 `node_limit=1000` 和可选 `target_uri`。省略目标时合并私有与共享根，同名 Skill 都保留，以 `root_uri` 区分。当前服务端实际固定每个根的目录扫描 `node_limit=1000`，尚未把请求的 `node_limit` 传到扫描层，不应依赖它作为全局条数限制或分页参数。

**Python SDK**：

```python
skills = client.list_skills(node_limit=1000)
for skill in skills["skills"]:
    print(skill["name"])
```

**TypeScript SDK**

```typescript
const skills = await client.listSkills();
console.log(skills);
```

**Go SDK**：

```go
skills, err := client.ListSkills(ctx, nil)
_ = skills
```

**HTTP API**：

```bash
curl -X GET "http://localhost:1933/api/v1/skills?node_limit=1000" \
  -H "X-API-Key: your-key"
```

### 读取技能

`GET /api/v1/skills/{skill_name}`：

| 查询参数 | 默认值 | 含义 |
|----------|--------|------|
| `target_uri` | 未指定 | 优先查找的 skills 根 |
| `level` | 未指定 | `0` 返回 abstract，`1` 返回 overview，`2` 返回 SKILL.md；未指定时返回三个层级 |
| `include_content` | 未指定 | `true` 额外返回正文；`false` 在未指定 level 时关闭正文；`level=2` 始终返回正文 |
| `include_files` | `true` | 返回文件和目录清单，默认不计算哈希 |
| `include_integrity` | `false` | 在树锁内读取快照；与 `include_files=true` 一起使用才生成文件哈希与 revision |
| `include_source` | `false` | 返回 `.source.json` 来源信息；没有记录时 `source.tracked=false` |

按名称读取、更新、删除共用查找逻辑：默认先找用户私有 Skill，再找共享 Skill；显式指定 `target_uri` 时先找目标根，未找到时仍会回退到私有/共享根。当前 `target_uri` 对这三个操作不是严格的“仅限该根”保证。处理同名 Skill 时，应先读取并检查返回的 `root_uri`。

**Python SDK**：

```python
skill = client.get_skill(
    skill_name="search-web",
    include_content=True,
    include_files=True,
)
print(skill["name"])
print(skill.get("content"))
```

**TypeScript SDK**

```typescript
const skill = await client.getSkill("my-skill");
console.log(skill);
```

**Go SDK**：

```go
skill, err := client.GetSkill(ctx, "search-web", &openviking.GetSkillOptions{
    IncludeContent: openviking.Bool(true),
    IncludeFiles:   openviking.Bool(true),
})
_ = skill
```

**HTTP API**：

```bash
curl -X GET "http://localhost:1933/api/v1/skills/search-web?include_content=true&include_files=true" \
  -H "X-API-Key: your-key"
```

### 完整性清单与远程使用

远程 Harness 需要下载脚本或二进制文件时，可先请求完整性清单：

```python
skill = client.get_skill(
    "search-web",
    target_uri="viking://~/skills",
    include_content=True,
    include_files=True,
    include_integrity=True,
)
print(skill["root_uri"], skill["revision"], skill["content_sha256"])
for entry in skill["files"]:
    if not entry["is_dir"]:
        print(entry["path"], entry["uri"], entry["size"], entry["sha256"])
```

HTTP 对应查询为 `GET /api/v1/skills/search-web?include_content=true&include_files=true&include_integrity=true`。当前 Python SDK 暴露 `include_integrity`；TypeScript、Go 的 `getSkill` / `GetSkill` options 及 `ov skills show` 暂无此选项，需要此能力时使用 HTTP 或 Python SDK。

| 返回字段 | 含义 |
|----------|------|
| `content_sha256` | 返回的 SKILL.md 正文的 SHA-256；只要返回 `content` 就有该字段，不要求开启完整性模式 |
| `revision` | 根据排序后的包清单（路径、目录标记、文件大小与哈希）计算的版本标识，不是 Git commit 或 metadata.version |
| `files[].name/path/uri` | 条目名称、包内相对路径、canonical URI |
| `files[].is_dir/kind` | 目录标记；kind 为 `definition`、`summary`、`auxiliary` 或 `directory` |
| `files[].size/sha256` | 完整性模式下非目录文件的字节数与 SHA-256；普通清单不包含这些字段 |

清单包含 `SKILL.md`、摘要和辅助文件/目录，不包含 `.source.json`。完整性 API 上限为 **512 个条目（包含目录）、单文件 16 MiB、总文件字节数 64 MiB**，读取并发为 8；超限返回 `RESOURCE_EXHAUSTED`。正文、清单和 revision 在同一次树锁保护的读取中取得，但后续下载仍可能遇到更新，消费方应校验文件哈希并复查 revision。

检索与 `get_skill` 只返回内容和清单，不会执行脚本或把文件安装到 Agent 沙箱。Harness 可按需远程读文本，在工具需要本地路径时下载资源。VikingBot 的完整流程见 [Skills](../../../bot/docs/zh/concepts/06-skills.md)。MCP 客户端通过 `find` 工具传 `context_type="skill"` 得到同样的包级行为，见 [MCP 集成](../guides/06-mcp-integration.md)。

### 搜索技能

`POST /api/v1/skills/find` 请求体：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `query` | 必填 | 检索文本 |
| `limit` | `10` | 最多返回的不同 Skill 数量，包含私有与共享空间的合并结果 |
| `score_threshold` | `null` | 最低分数，未指定时使用底层检索默认行为 |
| `level` | `null` | 参与匹配的层级列表，例如 `[0]`；未指定时不限制层级，与读取接口的单个整数不同 |
| `target_uri` | `null` | 限定检索范围；省略时分别检索私有与共享根 |
| `telemetry` | `false` | 遥测配置 |

包内命中按完整 Skill 根 URI 合并，使用最高最终得分排序，再截取 `limit` 个 Skill。不同空间的同名 Skill 分别保留。`total` 是本次返回数组长度，不是所有匹配项的总数。

每个 Skill 返回包内最终得分最高的一条命中。`uri`、`level`、`score`、`abstract` 直接使用该命中的原有字段，不增加额外返回字段。按 Skill 合并和补页用于专用 `skills/find`，以及只传 `context_type="skill"` 的 MCP `find` 工具；REST `find` / `search` 仍按命中内容返回。MCP `search` 同样按命中内容检索，只在渲染答案时把同一个包合成一条。

| 返回字段 | 含义 |
| --- | --- |
| `uri` / `level` | 实际命中地址及层级：L0 指向 `.abstract.md`，L1 指向 `.overview.md`，L2 指向具体文件 |
| `score` | 该命中的最终得分，也是所属 Skill 的排序得分 |
| `abstract` | 该命中记录已有的摘要，沿用原搜索规则 |
| `name` / `description` / `tags` / `allowed_tools` | 专用 `skills/find` 从 Skill 主目录单独读取的元数据 |
| `root_uri` / `skill_md_uri` | 专用 `skills/find` 返回的 Skill 根目录和主 `SKILL.md` 地址 |

专用 `skills/find` 的 `uri` 从原先的包根地址调整为实际命中地址；MCP `find` 工具的行为不同，它把每条 Skill 命中改写成 `<包根>/SKILL.md`。列表和按名称读取接口保持原样。`level=[2]` 只让文件参与匹配，返回的 `level` 为 `2`、`uri` 指向包内得分最高的文件。

通用检索中 `read_content=true` 继续读取实际返回的 `uri`——对只传 `context_type="skill"` 的 MCP `find` 来说，这个 URI 是包的 `SKILL.md`，不是实际命中的文件。专用 `skills/find` 不支持该参数。

上表的 URI 规则适用于语义检索。通用 `find` 仅按 `filter` 筛选时，仍保留索引记录的 URI、返回 `score=0`，不为 L0、L1 补摘要文件后缀；MCP `find` 遇到这种调用也留在通用路径上，因为包级检索必须带检索文本，但仍会把每条 skill 命中改写成它的 `SKILL.md`。

搜索范围、层级和权限限制先作用于包内命中，再合并 Skill；根目录也必须可访问。

**Python SDK**：

```python
results = client.find_skills(query="search the internet", limit=5)

for skill in results["skills"]:
    print(skill["name"], skill["score"])
```

**TypeScript SDK**

```typescript
const skills = await client.findSkills("database migration");
console.log(skills);
```

**Go SDK**：

```go
results, err := client.FindSkills(ctx, "search the internet", &openviking.FindSkillsOptions{
    Limit: 5,
})
_ = results
```

**HTTP API**：

```bash
curl -X POST http://localhost:1933/api/v1/skills/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "search the internet",
    "limit": 5
  }'
```

### 校验和更新技能

`POST /api/v1/skills/validate` 接收 `data`（必填的结构化数据或完整 SKILL.md 文本）、`strict=false`、`source_path=null`、`skill_dir_name=null` 和 `target_uri=null`。`source_path` 仅作为报告信息，不会让服务端读取本地文件；当前 `target_uri` 被接受但不参与格式校验。SDK 的 `validate_skill` 也不自动上传路径，应先读取文件内容。

缺少名称/描述和无效 YAML 会产生 errors。名称/目录不一致、名称长度或字符不合规、描述超过 1024 字符在普通模式为 warnings，`strict=true` 时升级为 errors；正文超过 500 行在严格模式仍只是 warning。该接口不安装 Skill、不验证工具是否已注册或依赖是否可执行，不能替代实际导入与 Harness 检查。

`PUT /api/v1/skills/{skill_name}` 替换整个包，接收以下请求体：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `data` / `temp_file_id` | 未指定 | 新内容或上传包，使用方式与新增相同 |
| `from_source` | `false` | 根据已记录的 Git 来源更新；不能与 data/temp_file_id 同用 |
| `target_uri` | 未指定 | 优先查找的 skills 根，参见读取接口的回退规则 |
| `source_metadata` | 自动生成 | 更新来源信息 |
| `wait` / `timeout` | `false` / `null` | 是否等待后台处理及超时秒数 |
| `telemetry` | `false` | 遥测配置 |

必须提供新内容/上传包，或设 `from_source=true`。新内容的名称必须与 URL 中的 `skill_name` 一致；更新不是重命名或局部 patch。先解析并检查新包，再备份替换；同步失败会尝试恢复旧包。需保留的辅助文件应随新包一起提交。

更新的文件、隐私配置和任务准备完成后才启动后台。需要恢复旧包时，包括 `wait=true` 超时，先取消本次摘要和索引任务，确认已开始的写入退出，再恢复原文件、索引及隐私配置；恢复失败会明确报告。超时响应可能晚于 `timeout`，但只等待取消收尾，不继续处理完整包。`wait=false` 已成功返回后的后台失败不自动恢复；新增 Skill 的等待超时仍只结束等待。

**Python SDK**：

```python
validated = client.validate_skill(data={"name": "search-web", "description": "..."})
print(validated["valid"], validated["errors"], validated["warnings"])
updated = client.update_skill(
    skill_name="search-web",
    data="./skills/search-web",
)
```

**TypeScript SDK**

```typescript
const result = await client.validateSkill({
  name: "search-web",
  description: "Search the web for current information",
  content: "# search-web\n\nSearch the web for current information.",
});
console.log(result);
```

**Go SDK**：

```go
validated, err := client.ValidateSkill(ctx, map[string]any{
    "name":        "search-web",
    "description": "...",
}, nil)
updated, err := client.UpdateSkill(ctx, "search-web", "./skills/search-web", nil)
_, _ = validated, updated
```

**HTTP API**：

```bash
# 校验技能数据
curl -X POST http://localhost:1933/api/v1/skills/validate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"data": {"name": "search-web", "description": "..."}}'

# 使用新的技能内容替换现有技能
curl -X PUT http://localhost:1933/api/v1/skills/search-web \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "search-web",
      "description": "Search the web for current information",
      "content": "# search-web\n\nUpdated instructions."
    }
  }'
```

从来源更新仅适用于已记录 Git 来源的 Skill，使用记录的仓库、ref 与子目录重新获取内容；本地上传没有可自动拉取的 Git 来源：

```python
updated = client.update_skill(
    "search-web",
    data=None,
    wait=True,
    options={"extra": {"from_source": True}},
)
```

```bash
curl -X PUT http://localhost:1933/api/v1/skills/search-web \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"from_source": true, "wait": true}'
```

### 删除技能

`DELETE /api/v1/skills/{skill_name}` 接受可选查询参数 `target_uri`，递归删除解析到的 Skill 包，并处理关联 Skill privacy 配置。目标根回退规则与读取相同。

**Python SDK**：

```python
client.delete_skill(skill_name="old-skill")
```

**TypeScript SDK**

```typescript
await client.deleteSkill("my-skill");
```

**Go SDK**：

```go
deleted, err := client.DeleteSkill(ctx, "old-skill")
_ = deleted
```

**HTTP API**：

```bash
curl -X DELETE "http://localhost:1933/api/v1/skills/old-skill" \
  -H "X-API-Key: your-key"
```

### CLI 管理命令

```bash
ov skills list -p viking://~/skills
ov skills find "search the internet" --level 0 --limit 5 -p viking://~/skills
ov skills show search-web --level 2 --files --source -p viking://~/skills
ov skills validate ./skills/search-web --strict
ov skills update search-web -p viking://~/skills --wait --yes
ov skills remove old-skill -p viking://~/skills --yes
```

`list/find/show` 的 `-p` 长选项为 `--uri`；`add/update/remove` 为 `--parent-auto-create`。`show` 默认返回各文本层级，文件清单需 `--files`，来源需 `--source`。`validate` 在本地校验，不安装；`update` 从记录的 Git 来源刷新，非 Git 来源在交互模式可要求补充路径/URL。

`ov skills update` 不带名称时尝试更新全部已安装 Skill，并在 `skipped` 中报告无法更新项。`remove` 不带名称时进入交互选择，`--all` 才表示全部。更新和删除需要确认，脚本中显式使用 `--yes`。

### 技能管理响应

列出和搜索都返回 `skills` 数组与 `total`。未指定 `target_uri` 时使用 `root_uris` 表示用户私有与 Agent 共享两个检索根；指定后返回单个 `root_uri`。

```json
{
  "status": "ok",
  "result": {
    "root_uris": [
      "viking://user/default/skills",
      "viking://agent/skills"
    ],
    "skills": [
      {
        "type": "skill",
        "name": "search-web",
        "uri": "viking://user/default/skills/search-web",
        "root_uri": "viking://user/default/skills/search-web",
        "skill_md_uri": "viking://user/default/skills/search-web/SKILL.md",
        "description": "Search the web for current information",
        "tags": [],
        "allowed_tools": [],
        "score": 0.87,
        "match_reason": "semantic",
        "level": 0
      }
    ],
    "total": 1
  }
}
```

读取单个技能时，`result` 返回上述技能元数据，并按 `level` 与 `include_*` 参数补充 `abstract`、`overview`、`content`、`files` 和 `source`。

校验返回 `valid`、`strict`、规范化后的元数据、`body_lines`、`errors` 和 `warnings`。校验不通过时仍返回成功响应包，但 `valid=false`：

```json
{
  "status": "ok",
  "result": {
    "valid": false,
    "strict": false,
    "name": "search-web",
    "description": "",
    "tags": [],
    "allowed_tools": [],
    "body_lines": 0,
    "errors": [
      {
        "rule": "description_required",
        "message": "description is required",
        "field": "description"
      }
    ],
    "warnings": []
  }
}
```

更新成功时返回与 `add_skill` 相同的处理结果，并额外包含 `"action": "update"`。删除成功返回：

```json
{
  "status": "ok",
  "result": {
    "name": "old-skill",
    "uri": "viking://user/default/skills/old-skill",
    "root_uri": "viking://user/default/skills/old-skill",
    "estimated_deleted_count": 4,
    "privacy_deleted": false
  }
}
```

`estimated_deleted_count` 仅在底层文件系统提供删除数量估算时出现。

## 最佳实践

### 清晰的描述

```python
# 好 - 具体且可操作
skill = {
    "name": "search-web",
    "description": "Search the web for current information using Google",
    # 其他技能字段
}

# 不够好 - 过于模糊
skill = {
    "name": "search",
    "description": "Search",
    # 其他技能字段
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
- [VikingBot Skills](../../../bot/docs/zh/concepts/06-skills.md) - 本地/远程 Skill 的激活、metadata 与执行
