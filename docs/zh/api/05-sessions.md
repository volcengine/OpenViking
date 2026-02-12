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
```

**HTTP API**

```
POST /api/v1/sessions
```

```bash
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
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

获取会话详情。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 会话 ID |

**Python SDK (Embedded / HTTP)**

```python
# 加载已有会话
session = client.session(session_id="a1b2c3d4")
session.load()
print(f"Loaded {len(session.messages)} messages")
```

**HTTP API**

```
GET /api/v1/sessions/{session_id}
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
    "user": "alice",
    "message_count": 5
  },
  "time": 0.1
}
```

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
| parts | List[Part] | 是 | - | 消息部分列表（SDK） |
| content | str | 是 | - | 消息文本内容（HTTP API） |

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

```bash
# 添加用户消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "user",
    "content": "How do I authenticate users?"
  }'

# 添加助手消息
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "assistant",
    "content": "You can use OAuth 2.0 for authentication..."
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

### commit()

提交会话，归档消息并提取记忆。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_id | str | 是 | - | 要提交的会话 ID |

**Python SDK (Embedded / HTTP)**

```python
session = client.session(session_id="a1b2c3d4")
session.load()

# commit 会归档消息并提取记忆
result = session.commit()
print(f"Status: {result['status']}")
print(f"Memories extracted: {result['memories_extracted']}")
```

**HTTP API**

```
POST /api/v1/sessions/{session_id}/commit
```

```bash
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
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
    "status": "committed",
    "archived": true
  },
  "time": 0.1
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
    |   +-- messages.jsonl
    |   +-- .abstract.md
    |   +-- .overview.md
    +-- archive_002/
```

---

## 记忆分类

| 分类 | 位置 | 说明 |
|------|------|------|
| profile | `user/memories/.overview.md` | 用户个人信息 |
| preferences | `user/memories/preferences/` | 按主题分类的用户偏好 |
| entities | `user/memories/entities/` | 重要实体（人物、项目等） |
| events | `user/memories/events/` | 重要事件 |
| cases | `agent/memories/cases/` | 问题-解决方案案例 |
| patterns | `agent/memories/patterns/` | 交互模式 |

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

# 提交会话（归档消息、提取记忆）
result = session.commit()
print(f"Memories extracted: {result['memories_extracted']}")

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

# 步骤 5：提交会话
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
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
