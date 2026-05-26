# 会话和记忆管理

会话用于管理对话状态、跟踪上下文使用情况，并提取长期记忆。会话采用分层存储（L0/L1/L2）来优化 token 使用：
- L0（abstract）: 会话概览摘要
- L1（overview）: 关键决策和总结
- L2（messages）: 完整消息

## API 参考

### create_session()

#### 1. API 实现介绍

创建新会话。会话是对话的容器，用于存储消息、跟踪上下文使用情况，并支持提交以提取长期记忆。

**处理流程**：
1. 生成或使用提供的 session_id
2. 初始化会话元数据（创建时间、用户信息等）
3. 在存储中创建会话目录结构
4. 返回会话信息

**代码入口**：
- `openviking/session/session.py:Session.__init__()` - Session 核心类
- `openviking/server/routers/sessions.py:create_session()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.create_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:new_session()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 否 | None | 会话 ID。如果为 None，则创建一个自动生成 ID 的新会话 |

#### 3. 使用示例

**HTTP API**

```http
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

**Python SDK**

```python
import openviking as ov

# 使用 HTTP 客户端
client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# 创建新会话（自动生成 ID）
result = await client.create_session()
print(f"Session ID: {result['session_id']}")

# 创建指定 ID 的新会话
result = await client.create_session(session_id="my-custom-session-id")
print(f"Session ID: {result['session_id']}")
```

**CLI**

```bash
ov session new
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "user": {
      "account_id": "default",
      "user_id": "alice",
      "agent_id": "default"
    }
  },
  "time": 0.1
}
```

---

### list_sessions()

#### 1. API 实现介绍

列出当前用户的所有会话。返回会话 ID 和 URI 信息，用于进一步操作会话。

**代码入口**：
- `openviking/server/routers/sessions.py:list_sessions()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.list_sessions()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:list_sessions()` - CLI 命令

#### 2. 接口和参数说明

**参数**

无参数。

#### 3. 使用示例

**HTTP API**

```http
GET /api/v1/sessions
```

```bash
curl -X GET http://localhost:1933/api/v1/sessions \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

sessions = await client.list_sessions()
for s in sessions:
    print(f"{s['session_id']} -> {s['uri']}")
```

**CLI**

```bash
ov session list
```

**响应示例**

```json
{
  "status": "ok",
  "result": [
    {
      "session_id": "a1b2c3d4",
      "uri": "viking://session/alice/a1b2c3d4",
      "is_dir": true
    },
    {
      "session_id": "e5f6g7h8",
      "uri": "viking://session/alice/e5f6g7h8",
      "is_dir": true
    }
  ],
  "time": 0.1
}
```

---

### get_session()

#### 1. API 实现介绍

获取会话详情，包括元数据、消息统计、提交历史等。支持在会话不存在时自动创建。

**返回字段说明**：
- `message_count`: 当前 live session 中尚未归档的消息数
- `total_message_count`: 已归档消息与当前 live 消息的累计总数（旧会话可能不返回此字段）
- `commit_count`: 成功提交的次数
- `memories_extracted`: 各类记忆的提取数量统计
- `last_commit_at`: 最后一次提交的时间

**代码入口**：
- `openviking/session/session.py:Session.load()` - 会话加载
- `openviking/server/routers/sessions.py:get_session()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.get_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:get_session()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| auto_create | bool | 否 | False | 会话不存在时是否自动创建 |

#### 3. 使用示例

**HTTP API**

```http
GET /api/v1/sessions/{session_id}?auto_create=false
```

```bash
curl -X GET http://localhost:1933/api/v1/sessions/a1b2c3d4 \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# 获取已有会话（不存在时抛 NotFoundError）
info = await client.get_session("a1b2c3d4")
print(f"Live Messages: {info['message_count']}")
print(f"Total Messages: {info.get('total_message_count', 'n/a')}")
print(f"Commits: {info['commit_count']}")

# 获取或创建会话
info = await client.get_session("a1b2c3d4", auto_create=True)
```

**CLI**

```bash
ov session get a1b2c3d4
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "created_at": "2026-03-23T10:00:00+08:00",
    "updated_at": "2026-03-23T11:30:00+08:00",
    "message_count": 5,
    "total_message_count": 20,
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
      "account_id": "default",
      "user_id": "alice",
      "agent_id": "default"
    },
    "pending_tokens": 450
  }
}
```

---

### get_session_context()

#### 1. API 实现介绍

获取供上下文组装使用的会话上下文。该接口返回最新的归档摘要和当前活跃消息，用于 LLM 上下文构建。

**返回字段说明**：
- `latest_archive_overview`: 最新一个已完成归档的 overview 文本，在 token budget 足够时返回
- `pre_archive_abstracts`: 保持 API 向下兼容，返回空数组
- `messages`: 最新已完成归档之后的所有未完成归档消息，再加上当前 live session 消息
- `estimatedTokens`: 预估总 token 数
- `stats`: 统计信息

**token budget 分配策略**：
1. 先分配给当前活跃消息
2. 剩余预算优先给最新归档的 overview
3. pre_archive_abstracts 目前不返回

**代码入口**：
- `openviking/session/session.py:Session.get_session_context()` - 核心实现
- `openviking/server/routers/sessions.py:get_session_context()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.get_session_context()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:get_session_context()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| token_budget | int | 否 | 128000 | active messages 之后留给 assembled archive payload 的非负 token 预算 |

#### 3. 使用示例

**HTTP API**

```http
GET /api/v1/sessions/{session_id}/context?token_budget=128000
```

```bash
curl -X GET "http://localhost:1933/api/v1/sessions/a1b2c3d4/context?token_budget=128000" \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

context = await client.get_session_context("a1b2c3d4", token_budget=128000)
print(context["latest_archive_overview"])
print(len(context["messages"]))
```

**CLI**

```bash
ov session get-session-context a1b2c3d4 --token-budget 128000
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "latest_archive_overview": "# Session Summary\n\n**Overview**: User discussed deployment and auth setup.",
    "pre_archive_abstracts": [],
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
      "includedArchives": 1,
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

#### 1. API 实现介绍

获取某次已完成归档的完整内容。该接口通常配合 `get_session_context()` 使用，当需要查看更早的归档详情时调用。

**代码入口**：
- `openviking/session/session.py:Session.get_session_archive()` - 核心实现
- `openviking/server/routers/sessions.py:get_session_archive()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.get_session_archive()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:get_session_archive()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| archive_id | str | 是 | - | 归档 ID，例如 `archive_002` |

#### 3. 使用示例

**HTTP API**

```http
GET /api/v1/sessions/{session_id}/archives/{archive_id}
```

```bash
curl -X GET "http://localhost:1933/api/v1/sessions/a1b2c3d4/archives/archive_002" \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

archive = await client.get_session_archive("a1b2c3d4", "archive_002")
print(archive["archive_id"])
print(archive["overview"])
print(len(archive["messages"]))
```

**CLI**

```bash
ov session get-session-archive a1b2c3d4 archive_002
```

**响应示例**

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

**错误响应**

如果 archive 不存在、未完成，或者不属于该 session，接口返回 404：

```json
{
  "status": "error",
  "error": {
    "code": "NOT_FOUND",
    "message": "Archive archive_002 not found"
  }
}
```

---

### delete_session()

#### 1. API 实现介绍

删除会话及其所有数据，包括消息、归档历史、记忆等。删除操作不可逆。

**代码入口**：
- `openviking/server/routers/sessions.py:delete_session()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.delete_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:delete_session()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 要删除的会话 ID |

#### 3. 使用示例

**HTTP API**

```http
DELETE /api/v1/sessions/{session_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/sessions/a1b2c3d4 \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# 删除会话
await client.delete_session("a1b2c3d4")
```

**CLI**

```bash
ov session delete a1b2c3d4
```

**响应示例**

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

#### 1. API 实现介绍

向会话中添加消息。支持两种模式：简单文本模式和 Parts 模式（支持文本、上下文引用、工具调用等）。

**Part 类型**：
- `TextPart`: 纯文本内容
- `ContextPart`: 上下文引用，指向资源或记忆
- `ToolPart`: 工具调用和结果

**代码入口**：
- `openviking/session/session.py:Session.add_message()` - 核心实现
- `openviking/server/routers/sessions.py:add_message()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.add_message()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:add_message()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| role | str | 是 | - | 消息角色："user" 或 "assistant" |
| parts | List[Part] | 条件必填 | - | 消息部分列表（Python SDK 必填；HTTP API 可选，与 content 二选一） |
| content | str | 条件必填 | - | 消息文本内容（HTTP API 简单模式，与 parts 二选一） |
| created_at | str | 否 | None | 可选的 ISO 8601 时间戳，会原样保存到消息中 |
| role_id | str | 否 | None | 可选的显式参与者 ID，省略时由服务器推导 |

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
    skill_uri="viking://agent/skills/search-web/",
    tool_input={"query": "OAuth best practices"},
    tool_output="",
    tool_status="pending"  # "pending"、"running"、"completed"、"error"
)
```

#### 3. 使用示例

**HTTP API**

```http
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

**Python SDK**

```python
import openviking as ov
from openviking.message import TextPart, ContextPart

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# 简单模式：添加用户消息
await client.add_message(
    session_id="a1b2c3d4",
    role="user",
    content="How do I authenticate users?"
)

# Parts 模式：添加带有上下文引用的助手消息
await client.add_message(
    session_id="a1b2c3d4",
    role="assistant",
    parts=[
        TextPart(text="Based on the documentation, you can configure embedding..."),
        ContextPart(
            uri="viking://resources/docs/auth/",
            context_type="resource",
            abstract="Authentication guide"
        )
    ]
)
```

**CLI**

```bash
ov session add-message a1b2c3d4 --role user --content "How do I authenticate users?"
```

**响应示例**

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

#### 1. API 实现介绍

记录会话中实际使用的上下文和技能。调用 `commit()` 时，会根据此使用数据更新资源的 `active_count`，用于优化未来的检索排序。

**代码入口**：
- `openviking/session/session.py:Session.used()` - 核心实现
- `openviking/server/routers/sessions.py:record_used()` - HTTP 路由

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |
| contexts | List[str] | 否 | None | 实际使用的上下文 URI 列表 |
| skill | Dict[str, Any] | 否 | None | 技能使用记录，包含 `uri`、`input`、`output`、`success` 字段 |

#### 3. 使用示例

**HTTP API**

```http
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
  -d '{"skill": {"uri": "viking://agent/skills/search-web/", "input": {"query": "OAuth"}, "output": "Results...", "success": true}}'
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# 记录使用的上下文
await client.session_used(
    session_id="a1b2c3d4",
    contexts=["viking://resources/docs/auth/"]
)

# 记录使用的技能
await client.session_used(
    session_id="a1b2c3d4",
    skill={
        "uri": "viking://agent/skills/search-web/",
        "input": {"query": "OAuth"},
        "output": "Results...",
        "success": True
    }
)
```

**响应示例**

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

#### 1. API 实现介绍

提交会话。归档消息（Phase 1）立即完成，摘要生成和记忆提取（Phase 2）在后台异步执行。返回 `task_id` 用于查询后台任务进度。

**两阶段提交流程**：
- **Phase 1（同步）**: 快照当前消息，清空 live session，创建归档目录，写入原始消息
- **Phase 2（异步）**: 生成摘要（L0/L1），提取长期记忆，更新关系和 active_count

**注意事项**：
- 同一 session 的多次快速连续 commit 会被接受；每次请求都会拿到独立的 `task_id`
- 后台 Phase 2 会按 archive 顺序串行推进：`archive_N+1` 会等待 `archive_N` 写出 `.done` 后再继续
- 如果更早的 archive 已失败且没有 `.done`，后续 commit 会直接返回错误，直到该失败被处理

**代码入口**：
- `openviking/session/session.py:Session.commit_async()` - 核心实现
- `openviking/server/routers/sessions.py:commit_session()` - HTTP 路由
- `openviking_cli/client/base.py:BaseClient.commit_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:commit_session()` - CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 要提交的会话 ID |

#### 3. 使用示例

**HTTP API**

```http
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

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# commit 立即返回 task_id，后台异步执行摘要生成和记忆提取
result = await client.commit_session("a1b2c3d4")
print(f"Status: {result['status']}")
print(f"Task ID: {result['task_id']}")

# 查询后台任务进度
task = await client.get_task(result["task_id"])
if task["status"] == "completed":
    memories = task["result"]["memories_extracted"]
    total = sum(memories.values())
    print(f"Memories extracted: {total}")
```

**CLI**

```bash
ov session commit a1b2c3d4
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "session_id": "a1b2c3d4",
    "status": "accepted",
    "task_id": "uuid-xxx",
    "archive_uri": "viking://session/alice/a1b2c3d4/history/archive_001",
    "archived": true
  }
}
```

---

### extract()

#### 1. API 实现介绍

仅 HTTP API。立即对已有会话触发一次记忆提取，不会额外创建新的 commit 任务。

**代码入口**：
- `openviking/server/routers/sessions.py:extract_session()` - HTTP 路由

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 要提取记忆的会话 ID |

#### 3. 使用示例

**HTTP API**

```http
POST /api/v1/sessions/{session_id}/extract
```

```bash
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/extract \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
```

**响应示例**

该接口会直接返回本次提取产生的记忆写入结果列表。列表项的具体结构取决于该会话实际提取出了哪些记忆。

---

### get_task()

#### 1. API 实现介绍

查询后台任务状态（如 commit 的摘要生成和记忆提取进度）。

**任务状态**：
- `pending`: 任务等待执行
- `running`: 任务执行中
- `completed`: 任务成功完成
- `failed`: 任务失败

**代码入口**：
- `openviking/server/routers/tasks.py:get_task()` - HTTP 路由

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_id | str | 是 | - | 任务 ID（由 commit 返回） |

#### 3. 使用示例

**HTTP API**

```http
GET /api/v1/tasks/{task_id}
```

```bash
curl -X GET http://localhost:1933/api/v1/tasks/uuid-xxx \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

task = await client.get_task(task_id="uuid-xxx")
print(f"Status: {task['status']}")
```

**响应示例（进行中）**

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

**响应示例（完成）**

```json
{
  "status": "ok",
  "result": {
    "task_id": "uuid-xxx",
    "task_type": "session_commit",
    "status": "completed",
    "result": {
      "session_id": "a1b2c3d4",
      "archive_uri": "viking://session/alice/a1b2c3d4/history/archive_001",
      "memories_extracted": {
        "profile": 1,
        "preferences": 2,
        "entities": 1,
        "cases": 1
      },
      "active_count_updated": 2,
      "token_usage": {
        "llm": {
          "prompt_tokens": 5200,
          "completion_tokens": 1800,
          "total_tokens": 7000
        },
        "embedding": {
          "total_tokens": 1500
        },
        "total": {
          "total_tokens": 8500
        }
      }
    }
  }
}
```

---

### list_tasks()

#### 1. API 实现介绍

仅 HTTP API。列出当前调用方可见的后台任务，支持按类型、状态、资源过滤。

**代码入口**：
- `openviking/server/routers/tasks.py:list_tasks()` - HTTP 路由

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_type | str | 否 | None | 按任务类型过滤，例如 `session_commit` |
| status | str | 否 | None | 按任务状态过滤：`pending`、`running`、`completed`、`failed` |
| resource_id | str | 否 | None | 按资源 ID 过滤，例如会话 ID |
| limit | int | 否 | 50 | 最多返回的任务条数 |

#### 3. 使用示例

**HTTP API**

```http
GET /api/v1/tasks?task_type=session_commit&status=running&limit=20
```

```bash
curl -X GET "http://localhost:1933/api/v1/tasks?task_type=session_commit&status=running&limit=20" \
  -H "X-API-Key: your-key"
```

**响应示例**

```json
{
  "status": "ok",
  "result": [
    {
      "task_id": "uuid-xxx",
      "task_type": "session_commit",
      "status": "running",
      "resource_id": "a1b2c3d4",
      "created_at": 1770000000.0,
      "updated_at": 1770000005.0,
      "result": null,
      "error": null
    }
  ]
}
```

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
viking://session/{user_id}/{session_id}/
├── .abstract.md              # L0：会话概览
├── .overview.md              # L1：关键决策
├── .meta.json                # 元数据
├── .relations.json           # 关联上下文
├── messages.jsonl            # 当前消息
├── tools/                    # 工具执行记录
│   └── {tool_id}/
│       └── tool.json
└── history/                  # 归档历史
    ├── archive_001/
    │   ├── messages.jsonl    # Phase 1 写入
    │   ├── .abstract.md      # Phase 2 写入（后台）
    │   ├── .overview.md      # Phase 2 写入（后台）
    │   ├── .meta.json        # 归档元数据
    │   ├── memory_diff.json  # Phase 2 写入（后台，记忆变更时）
    │   ├── .done             # Phase 2 完成标记
    │   └── .failed.json      # Phase 2 失败标记
    └── archive_002/
```

### memory_diff.json 数据结构

每次提交会在归档目录写入 `memory_diff.json`，记录所有记忆变更，便于审计和回溯：

```json
{
  "archive_uri": "viking://session/{session_id}/history/archive_001",
  "extracted_at": "2026-04-21T10:00:00Z",
  "operations": {
    "adds": [
      {
        "uri": "memory/user/xxx/identity.md",
        "memory_type": "identity",
        "after": "新创建的文件内容"
      }
    ],
    "updates": [
      {
        "uri": "memory/user/xxx/context/project.md",
        "memory_type": "context",
        "before": "修改前的文件内容",
        "after": "修改后的文件内容"
      }
    ],
    "deletes": [
      {
        "uri": "memory/user/xxx/context/old.md",
        "memory_type": "context",
        "deleted_content": "被删除的文件内容"
      }
    ]
  },
  "summary": {
    "total_adds": 1,
    "total_updates": 1,
    "total_deletes": 1
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `archive_uri` | str | 本次提交的归档目录 URI |
| `extracted_at` | str | 提取时间的 ISO 8601 格式 |
| `operations.adds` | array | 新增记忆（`uri`、`memory_type`、`after`） |
| `operations.updates` | array | 修改记忆（`uri`、`memory_type`、`before`、`after`） |
| `operations.deletes` | array | 删除记忆（`uri`、`memory_type`、`deleted_content`） |
| `summary.total_adds` | int | 新增记忆数 |
| `summary.total_updates` | int | 修改记忆数 |
| `summary.total_deletes` | int | 删除记忆数 |

即使没有记忆操作，也会写入空结构的 `memory_diff.json`（所有计数为零）。

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

**Python SDK**

```python
import openviking as ov
from openviking.message import TextPart, ContextPart

# 初始化客户端
client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# 创建新会话
session_result = await client.create_session()
session_id = session_result["session_id"]
print(f"Session created: {session_id}")

# 添加用户消息
await client.add_message(
    session_id=session_id,
    role="user",
    content="How do I configure embedding?"
)

# 使用会话上下文进行搜索
results = await client.search("embedding configuration", session_id=session_id)

# 添加带有上下文引用的助手回复
if results.resources:
    await client.add_message(
        session_id=session_id,
        role="assistant",
        parts=[
            TextPart(text="Based on the documentation, you can configure embedding..."),
            ContextPart(
                uri=results.resources[0].uri,
                context_type="resource",
                abstract=results.resources[0].abstract
            )
        ]
    )

    # 跟踪实际使用的上下文
    await client.session_used(
        session_id=session_id,
        contexts=[results.resources[0].uri]
    )

# 提交会话（立即返回，后台执行摘要生成和记忆提取）
commit_result = await client.commit_session(session_id)
print(f"Task ID: {commit_result['task_id']}")

# 可选：等待后台任务完成
task = await client.get_task(commit_result["task_id"])
if task and task["status"] == "completed":
    memories = task["result"]["memories_extracted"]
    total = sum(memories.values())
    print(f"Memories extracted: {total}")
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
session_info = await client.get_session(session_id)
if session_info["message_count"] > 10:
    await client.commit_session(session_id)
```

### 跟踪实际使用的内容

```python
# 仅标记实际有帮助的上下文
if context_was_useful:
    await client.session_used(session_id=session_id, contexts=[ctx.uri])
```

### 使用会话上下文进行搜索

```python
# 结合对话上下文可获得更好的搜索结果
results = await client.search(query, session_id=session_id)
```

---

## 相关文档

- [上下文类型](../concepts/02-context-types.md) - 记忆类型
- [检索](06-retrieval.md) - 结合会话进行搜索
- [资源管理](02-resources.md) - 资源管理
