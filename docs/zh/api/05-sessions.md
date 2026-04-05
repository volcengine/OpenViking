# 会话管理

会话用于管理对话状态、跟踪上下文使用情况，并提取长期记忆。

## API 参考

### create_session()

创建新会话。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 否 | None | 会话 ID。如果为 None，则创建一个自动生成 ID 的新会话 |

**Python SDK (Embedded / HTTP)**

```python
# 创建新会话（自动生成 ID）
session = client.session()
print(f"Session URI: {session.uri}")

# 创建指定 ID 的新会话
session = client.create_session(session_id="my-custom-session-id")
print(f"Session ID: {session['session_id']}")
```

**HTTP API**

```
POST /api/v1/sessions
```

```bash
# 创建新会话（自动生成 ID）
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"

# 创建指定 ID 的新会话
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"session_id": "my-custom-session-id"}'
```

**CLI**

```bash
openviking session new
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "user": "alice"
  },
  "time": 0.1
}
```

---

### list_sessions()

列出所有会话。

**Python SDK (Embedded / HTTP)**

```python
sessions = client.ls("viking://session/")
for s in sessions:
    print(f"{s['name']}")
```

**HTTP API**

```
GET /api/v1/sessions
```

```bash
curl -X GET http://localhost:1933/api/v1/sessions \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking session list
```

**响应**

```json
{
  "status": "ok",
  "result": [
    {"session_id": "a1b2c3d4", "user": "alice"},
    {"session_id": "e5f6g7h8", "user": "bob"}
  ],
  "time": 0.1
}
```

---

### get_session()

获取会话详情。默认当会话不存在时返回 NOT_FOUND 错误，可通过 `auto_create=True` 自动创建。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| auto_create | bool | 否 | False | 会话不存在时是否自动创建 |

**Python SDK (Embedded / HTTP)**

```python
# 获取已有会话（不存在时抛 NotFoundError）
info = client.get_session("a1b2c3d4")
print(f"Messages: {info['message_count']}, Commits: {info['commit_count']}")

# 获取或创建会话
info = client.get_session("a1b2c3d4", auto_create=True)
```

**HTTP API**

```
GET /api/v1/sessions/{session_id}?auto_create=false
```

```bash
curl -X GET http://localhost:1933/api/v1/sessions/a1b2c3d4 \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking session get a1b2c3d4
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "created_at": "2026-03-23T10:00:00+08:00",
    "updated_at": "2026-03-23T11:30:00+08:00",
    "message_count": 5,
    "commit_count": 3,
    "memories_extracted": {
      "profile": 1,
      "preferences": 2,
      "entities": 3,
      "events": 1,
      "cases": 2,
      "patterns": 1,
      "tools": 0,
      "skills": 0,
      "total": 10
    },
    "last_commit_at": "2026-03-23T11:00:00+08:00",
    "llm_token_usage": {
      "prompt_tokens": 5200,
      "completion_tokens": 1800,
      "total_tokens": 7000
    },
    "user": {
      "user_id": "alice",
      "agent_id": "default"
    }
  }
}
```

---

### get_session_context()

获取供上下文组装使用的会话上下文。

该接口返回：
- `latest_archive_overview`：最新一个已完成归档的 `overview` 文本，在 token budget 足够时返回
- `pre_archive_abstracts`：已完成归档的轻量列表，每项只包含 `archive_id` 和 `abstract`
- `messages`：最新已完成归档之后的所有未完成归档消息，再加上当前 live session 消息
- `stats`：返回结果对应的 token 与纳入统计

说明：
- 没有可用 completed archive，或最新 overview 超出 token budget 时，`latest_archive_overview` 返回空字符串。
- `token_budget` 会在 active `messages` 之后作用于 assembled archive payload：`latest_archive_overview` 优先级高于 `pre_archive_abstracts`，预算紧张时先淘汰最旧的 abstracts。
- 只有最终实际返回的 archive 内容，才会计入 `estimatedTokens` 和 `stats.archiveTokens`。
- 当前每次有消息的 session commit 都会在 Phase 2 生成 archive 摘要；只有带 `.done` 标记的 completed archive 才会被这里返回。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| token_budget | int | 否 | 128000 | active `messages` 之后留给 assembled archive payload 的 token 预算 |

**Python SDK (Embedded / HTTP)**

```python
context = await client.get_session_context("a1b2c3d4", token_budget=128000)
print(context["latest_archive_overview"])
print(context["pre_archive_abstracts"])
print(len(context["messages"]))

session = client.session("a1b2c3d4")
context = await session.get_session_context(token_budget=128000)
```

**HTTP API**

```
GET /api/v1/sessions/{session_id}/context?token_budget=128000
```

```bash
curl -X GET "http://localhost:1933/api/v1/sessions/a1b2c3d4/context?token_budget=128000" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov session get-session-context a1b2c3d4 --token-budget 128000
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "latest_archive_overview": "# Session Summary\n\n**Overview**: User discussed deployment and auth setup.",
    "pre_archive_abstracts": [
      {
        "archive_id": "archive_002",
        "abstract": "用户讨论了部署和鉴权配置。"
      },
      {
        "archive_id": "archive_001",
        "abstract": "用户之前讨论了仓库初始化和鉴权配置。"
      }
    ],
    "messages": [
      {
        "id": "msg_pending_1",
        "role": "user",
        "parts": [
          {"type": "text", "text": "Pending user message"}
        ],
        "created_at": "2026-03-24T09:10:11Z"
      },
      {
        "id": "msg_live_1",
        "role": "assistant",
        "parts": [
          {"type": "text", "text": "Current live message"}
        ],
        "created_at": "2026-03-24T09:10:20Z"
      }
    ],
    "estimatedTokens": 160,
    "stats": {
      "totalArchives": 2,
      "includedArchives": 2,
      "droppedArchives": 0,
      "failedArchives": 0,
      "activeTokens": 98,
      "archiveTokens": 62
    }
  }
}
```

---

### get_session_archive()

获取某次已完成归档的完整内容。

该接口通常配合 `get_session_context()` 返回的 `pre_archive_abstracts[*].archive_id` 使用。

该接口返回：
- `archive_id`：被展开的 archive ID
- `abstract`：该 archive 的轻量摘要
- `overview`：该 archive 的完整 overview
- `messages`：该次 archive 对应的完整消息内容

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| archive_id | str | 是 | - | 归档 ID，例如 `archive_002` |

**Python SDK (Embedded / HTTP)**

```python
archive = await client.get_session_archive("a1b2c3d4", "archive_002")
print(archive["archive_id"])
print(archive["overview"])
print(len(archive["messages"]))

session = client.session("a1b2c3d4")
archive = await session.get_archive("archive_002")
```

**HTTP API**

```
GET /api/v1/sessions/{session_id}/archives/{archive_id}
```

```bash
curl -X GET "http://localhost:1933/api/v1/sessions/a1b2c3d4/archives/archive_002" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov session get-session-archive a1b2c3d4 archive_002
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "archive_id": "archive_002",
    "abstract": "用户讨论了部署流程和鉴权配置。",
    "overview": "# Session Summary\n\n**Overview**: 用户讨论了部署流程和鉴权配置。",
    "messages": [
      {
        "id": "msg_archive_1",
        "role": "user",
        "parts": [
          {"type": "text", "text": "这个服务应该怎么部署？"}
        ],
        "created_at": "2026-03-24T08:55:01Z"
      },
      {
        "id": "msg_archive_2",
        "role": "assistant",
        "parts": [
          {"type": "text", "text": "建议先走分阶段部署，再核验鉴权链路。"}
        ],
        "created_at": "2026-03-24T08:55:18Z"
      }
    ]
  }
}
```

如果 archive 不存在、未完成，或者不属于该 session，接口返回 `404`。

---

### delete_session()

删除会话。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 要删除的会话 ID |

**Python SDK (Embedded / HTTP)**

```python
client.rm("viking://session/a1b2c3d4/", recursive=True)
```

**HTTP API**

```
DELETE /api/v1/sessions/{session_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/sessions/a1b2c3d4 \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking session delete a1b2c3d4
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4"
  },
  "time": 0.1
}
```

---

### add_message()

向会话中添加消息。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| role | str | 是 | - | 消息角色："user" 或 "assistant" |
| parts | List[Part] | 条件必填 | - | 消息部分列表（Python SDK 必填；HTTP API 可选，与 content 二选一） |
| content | str | 条件必填 | - | 消息文本内容（HTTP API 简单模式，与 parts 二选一） |

> **注意**：HTTP API 支持两种模式：
> 1. **简单模式**：使用 `content` 字符串（向后兼容）
> 2. **Parts 模式**：使用 `parts` 数组（完整 Part 支持）
>
> 如果同时提供 `content` 和 `parts`，`parts` 优先。

**Part 类型（Python SDK）**

```python
from openviking.message import TextPart, ContextPart, ToolPart

# 文本内容
TextPart(text="Hello, how can I help?")

# 上下文引用
ContextPart(
    uri="viking://resources/docs/auth/",
    context_type="resource",  # "resource"、"memory" 或 "skill"
    abstract="Authentication guide..."
)

# 工具调用
ToolPart(
    tool_id="call_123",
    tool_name="search_web",
    skill_uri="viking://skills/search-web/",
    tool_input={"query": "OAuth best practices"},
    tool_output="",
    tool_status="pending"  # "pending"、"running"、"completed"、"error"
)
```

**Python SDK (Embedded / HTTP)**

```python
from openviking.message import TextPart

session = client.session()

# 添加用户消息
session.add_message("user", [
    TextPart(text="How do I authenticate users?")
])

# 添加助手回复
session.add_message("assistant", [
    TextPart(text="You can use OAuth 2.0 for authentication...")
])
```

**HTTP API**

```
POST /api/v1/sessions/{session_id}/messages
```

**简单模式（向后兼容）**

```bash
# 添加用户消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "user",
    "content": "How do I authenticate users?"
  }'
```

**Parts 模式（完整 Part 支持）**

```bash
# 添加带有上下文引用的助手消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "assistant",
    "parts": [
      {"type": "text", "text": "Based on the authentication guide..."},
      {"type": "context", "uri": "viking://resources/docs/auth/", "context_type": "resource", "abstract": "Auth guide"}
    ]
  }'

# 添加带有工具调用的助手消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "assistant",
    "parts": [
      {"type": "text", "text": "Let me search for that..."},
      {"type": "tool", "tool_id": "call_123", "tool_name": "search_web", "tool_input": {"query": "OAuth"}, "tool_status": "completed", "tool_output": "Results..."}
    ]
  }'
```

**CLI**

```bash
openviking session add-message a1b2c3d4 --role user --content "How do I authenticate users?"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "message_count": 2
  },
  "time": 0.1
}
```

---

### used()

记录会话中实际使用的上下文和技能。调用 `commit()` 时，会根据此使用数据更新 `active_count`。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| contexts | List[str] | 否 | None | 实际使用的上下文 URI 列表 |
| skill | Dict[str, Any] | 否 | None | 技能使用记录，包含 `uri`、`input`、`output`、`success` 字段 |

**Python SDK (Embedded / HTTP)**

```python
session = client.session(session_id="a1b2c3d4")
session.load()

# 记录使用的上下文
session.used(contexts=["viking://resources/docs/auth/"])

# 记录使用的技能
session.used(skill={
    "uri": "viking://skills/search-web/",
    "input": {"query": "OAuth"},
    "output": "Results...",
    "success": True
})
```

**HTTP API**

```
POST /api/v1/sessions/{session_id}/used
```

```bash
# 记录使用的上下文
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"contexts": ["viking://resources/docs/auth/"]}'

# 记录使用的技能
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"skill": {"uri": "viking://skills/search-web/", "input": {"query": "OAuth"}, "output": "Results...", "success": true}}'
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "contexts_used": 1,
    "skills_used": 0
  },
  "time": 0.1
}
```

---

### commit()

提交会话。归档消息（Phase 1）立即完成，摘要生成和记忆提取（Phase 2）在后台异步执行。返回 `task_id` 用于查询后台任务进度。

说明：
- 同一 session 的多次快速连续 commit 会被接受；每次请求都会拿到独立的 `task_id`。
- 后台 Phase 2 会按 archive 顺序串行推进：`archive N+1` 会等待 `archive N` 写出 `.done` 后再继续。
- 如果更早的 archive 已失败且没有 `.done`，后续 commit 会直接返回 `FAILED_PRECONDITION`，直到该失败被处理。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 要提交的会话 ID |

**Python SDK (Embedded / HTTP)**

```python
session = client.session(session_id="a1b2c3d4")
session.load()

# commit 立即返回 task_id，后台异步执行摘要生成和记忆提取
result = session.commit()
print(f"Status: {result['status']}")       # "accepted"
print(f"Task ID: {result['task_id']}")

# 查询后台任务进度
task = client.get_task(result["task_id"])
if task["status"] == "completed":
    print(f"Memories extracted: {sum(task['result']['memories_extracted'].values())}")
```

**HTTP API**

```
POST /api/v1/sessions/{session_id}/commit
```

```bash
# 提交会话（立即返回）
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"

# 查询任务状态
curl -X GET http://localhost:1933/api/v1/tasks/{task_id} \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking session commit a1b2c3d4
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "status": "accepted",
    "task_id": "uuid-xxx",
    "archive_uri": "viking://session/a1b2c3d4/history/archive_001",
    "archived": true
  }
}
```

---

### get_task()

查询后台任务状态（如 commit 的摘要生成和记忆提取进度）。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_id | str | 是 | - | 任务 ID（由 commit 返回） |

**Python SDK (Embedded / HTTP)**

```python
task = client.get_task(task_id)
print(f"Status: {task['status']}")  # "pending" | "running" | "completed" | "failed"
```

**HTTP API**

```
GET /api/v1/tasks/{task_id}
```

```bash
curl -X GET http://localhost:1933/api/v1/tasks/uuid-xxx \
  -H "X-API-Key: your-key"
```

**响应（进行中）**

```json
{
  "status": "ok",
  "result": {
    "task_id": "uuid-xxx",
    "task_type": "session_commit",
    "status": "running"
  }
}
```

**响应（完成）**

```json
{
  "status": "ok",
  "result": {
    "task_id": "uuid-xxx",
    "task_type": "session_commit",
    "status": "completed",
    "result": {
      "session_id": "a1b2c3d4",
      "archive_uri": "viking://session/a1b2c3d4/history/archive_001",
      "memories_extracted": {
        "profile": 1,
        "preferences": 2,
        "entities": 1,
        "cases": 1
      },
      "active_count_updated": 2
    }
  }
}
```

完成态任务结果里的 `memories_extracted` 表示本次 commit 的分类计数；如果只需要本次 commit 的总数，请把这些值求和。

---

## 会话属性

| 属性 | 类型 | 说明 |
|------|------|------|
| uri | str | 会话 Viking URI（`viking://session/{session_id}/`） |
| messages | List[Message] | 会话中的当前消息 |
| stats | SessionStats | 会话统计信息 |
| summary | str | 压缩摘要 |
| usage_records | List[Usage] | 上下文和技能使用记录 |

---

## 会话存储结构

```
viking://session/{session_id}/
+-- .abstract.md              # L0：会话概览
+-- .overview.md              # L1：关键决策
+-- messages.jsonl            # 当前消息
+-- tools/                    # 工具执行记录
|   +-- {tool_id}/
|       +-- tool.json
+-- .meta.json                # 元数据
+-- .relations.json           # 关联上下文
+-- history/                  # 归档历史
    +-- archive_001/
    |   +-- messages.jsonl    # Phase 1 写入
    |   +-- .abstract.md      # Phase 2 写入（后台）
    |   +-- .overview.md      # Phase 2 写入（后台）
    |   +-- .done             # Phase 2 完成标记
    +-- archive_002/
```

---

## 记忆分类

| 分类 | 位置 | 说明 |
|------|------|------|
| profile | `user/memories/profile.md` | 用户个人信息 |
| preferences | `user/memories/preferences/` | 按主题分类的用户偏好 |
| entities | `user/memories/entities/` | 重要实体（人物、项目等） |
| events | `user/memories/events/` | 重要事件 |
| cases | `agent/memories/cases/` | 问题-解决方案案例 |
| patterns | `agent/memories/patterns/` | 交互模式 |
| tools | `agent/memories/tools/` | 工具使用经验与最佳实践 |
| skills | `agent/memories/skills/` | 技能执行经验与工作流策略 |

---

## 完整示例

**Python SDK (Embedded / HTTP)**

```python
import openviking as ov
from openviking.message import TextPart, ContextPart

# 初始化客户端
client = ov.OpenViking(path="./my_data")
client.initialize()

# 创建新会话
session = client.session()

# 添加用户消息
session.add_message("user", [
    TextPart(text="How do I configure embedding?")
])

# 使用会话上下文进行搜索
results = client.search("embedding configuration", session=session)

# 添加带上下文引用的助手回复
session.add_message("assistant", [
    TextPart(text="Based on the documentation, you can configure embedding..."),
    ContextPart(
        uri=results.resources[0].uri,
        context_type="resource",
        abstract=results.resources[0].abstract
    )
])

# 跟踪实际使用的上下文
session.used(contexts=[results.resources[0].uri])

# 提交会话（立即返回，后台执行摘要生成和记忆提取）
result = session.commit()
print(f"Task ID: {result['task_id']}")

# 可选：等待后台任务完成
task = client.get_task(result["task_id"])
if task and task["status"] == "completed":
    print(f"Memories extracted: {sum(task['result']['memories_extracted'].values())}")

client.close()
```

**HTTP API**

```bash
# 步骤 1：创建会话
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
# 返回：{"status": "ok", "result": {"session_id": "a1b2c3d4"}}

# 步骤 2：添加用户消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"role": "user", "content": "How do I configure embedding?"}'

# 步骤 3：使用会话上下文进行搜索
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"query": "embedding configuration", "session_id": "a1b2c3d4"}'

# 步骤 4：添加助手消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"role": "assistant", "content": "Based on the documentation, you can configure embedding..."}'

# 步骤 5：记录使用的上下文
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"contexts": ["viking://resources/docs/embedding/"]}'

# 步骤 6：提交会话（立即返回 task_id）
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
# 返回：{"status": "ok", "result": {"status": "accepted", "task_id": "uuid-xxx", ...}}

# 步骤 7：查询后台任务进度（可选）
curl -X GET http://localhost:1933/api/v1/tasks/uuid-xxx \
  -H "X-API-Key: your-key"
```

## 最佳实践

### 定期提交

```python
# 在重要交互后提交
if len(session.messages) > 10:
    session.commit()
```

### 跟踪实际使用的内容

```python
# 仅标记实际有帮助的上下文
if context_was_useful:
    session.used(contexts=[ctx.uri])
```

### 使用会话上下文进行搜索

```python
# 结合对话上下文可获得更好的搜索结果
results = client.search(query, session=session)
```

### 继续会话前先加载

```python
# 恢复已有会话时务必先加载
session = client.session(session_id="existing-id")
session.load()
```

---

## 相关文档

- [上下文类型](../concepts/02-context-types.md) - 记忆类型
- [检索](06-retrieval.md) - 结合会话进行搜索
- [资源管理](02-resources.md) - 资源管理
