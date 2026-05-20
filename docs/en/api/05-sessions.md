# Sessions

Sessions manage conversation state, track context usage, and extract long-term memories. Sessions use tiered storage (L0/L1/L2) to optimize token usage:
- L0 (abstract): Session overview summary
- L1 (overview): Key decisions
- L2 (messages): Complete messages

## API Reference

### create_session()

#### 1. API Implementation Introduction

Create a new session. Sessions are containers for conversations, storing messages, tracking context usage, and supporting commits for long-term memory extraction.

**Processing Flow:**
1. Generate or use provided session_id
2. Initialize session metadata (creation time, user info, etc.)
3. Create session directory structure in storage
4. Return session info

**Code Entries:**
- `openviking/session/session.py:Session.__init__()` - Core Session class
- `openviking/server/routers/sessions.py:create_session()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.create_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:new_session()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | No | None | Session ID. Creates new session with auto-generated ID if None |

#### 3. Usage Examples

**HTTP API**

```http
POST /api/v1/sessions
```

```bash
# Create new session (auto-generated ID)
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"

# Create new session with specified ID
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"session_id": "my-custom-session-id"}'
```

**Python SDK**

```python
import openviking as ov

# Use HTTP client
client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# Create new session (auto-generated ID)
result = await client.create_session()
print(f"Session ID: {result['session_id']}")

# Create new session with specified ID
result = await client.create_session(session_id="my-custom-session-id")
print(f"Session ID: {result['session_id']}")
```

**CLI**

```bash
ov session new
```

**Response Example**

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

#### 1. API Implementation Introduction

List all sessions for the current user. Returns session IDs and URI info for further operations.

**Code Entries:**
- `openviking/server/routers/sessions.py:list_sessions()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.list_sessions()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:list_sessions()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

None.

#### 3. Usage Examples

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

**Response Example**

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

#### 1. API Implementation Introduction

Get session details including metadata, message statistics, commit history, etc. Supports auto-creating sessions when they don't exist.

**Return Fields Description:**
- `message_count`: Number of current live, unarchived messages
- `total_message_count`: Cumulative count of archived and current live messages (older sessions may omit this field)
- `commit_count`: Number of successful commits
- `memories_extracted`: Count statistics of extracted memories by category
- `last_commit_at`: Time of last commit

**Code Entries:**
- `openviking/session/session.py:Session.load()` - Session loading
- `openviking/server/routers/sessions.py:get_session()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.get_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:get_session()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| auto_create | bool | No | False | Whether to auto-create the session if it does not exist |

#### 3. Usage Examples

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

# Get existing session (raises NotFoundError if not found)
info = await client.get_session("a1b2c3d4")
print(f"Live Messages: {info['message_count']}")
print(f"Total Messages: {info.get('total_message_count', 'n/a')}")
print(f"Commits: {info['commit_count']}")

# Get or create session
info = await client.get_session("a1b2c3d4", auto_create=True)
```

**CLI**

```bash
ov session get a1b2c3d4
```

**Response Example**

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

#### 1. API Implementation Introduction

Get the assembled session context used for LLM context building. This endpoint returns the latest archive overview and current live messages.

**Return Fields Description:**
- `latest_archive_overview`: The `overview` of the latest completed archive, when it fits the token budget
- `pre_archive_abstracts`: Kept for backward compatibility, returns empty array
- `messages`: All incomplete archive messages after the latest completed archive, plus current live session messages
- `estimatedTokens`: Estimated total tokens
- `stats`: Statistics

**Token Budget Allocation Strategy:**
1. First allocate to current live messages
2. Remaining budget prioritizes the latest archive overview
3. Pre-archive abstracts are not currently returned

**Code Entries:**
- `openviking/session/session.py:Session.get_session_context()` - Core implementation
- `openviking/server/routers/sessions.py:get_session_context()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.get_session_context()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:get_session_context()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| token_budget | int | No | 128000 | Non-negative token budget for assembled archive payload after active `messages` |

#### 3. Usage Examples

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

**Response Example**

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

#### 1. API Implementation Introduction

Get the full contents of one completed archive for a session. This endpoint is typically used with `get_session_context()` when you need to view older archive details.

**Code Entries:**
- `openviking/session/session.py:Session.get_session_archive()` - Core implementation
- `openviking/server/routers/sessions.py:get_session_archive()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.get_session_archive()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:get_session_archive()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| archive_id | str | Yes | - | Archive ID such as `archive_002` |

#### 3. Usage Examples

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

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "archive_id": "archive_002",
    "abstract": "User discussed deployment and authentication setup.",
    "overview": "# Session Summary\n\n**Overview**: User discussed deployment and auth setup.",
    "messages": [
      {
        "id": "msg_archive_1",
        "role": "user",
        "parts": [
          {"type": "text", "text": "How should I deploy this service?"}
        ],
        "created_at": "2026-03-24T08:55:01Z"
      },
      {
        "id": "msg_archive_2",
        "role": "assistant",
        "parts": [
          {"type": "text", "text": "Use the staged deployment flow and verify auth first."}
        ],
        "created_at": "2026-03-24T08:55:18Z"
      }
    ]
  }
}
```

**Error Response**

If the archive does not exist, is incomplete, or does not belong to the session, the API returns 404:

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

#### 1. API Implementation Introduction

Delete a session and all its data, including messages, archive history, memories, etc. Deletion is irreversible.

**Code Entries:**
- `openviking/server/routers/sessions.py:delete_session()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.delete_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:delete_session()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID to delete |

#### 3. Usage Examples

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

# Delete session
await client.delete_session("a1b2c3d4")
```

**CLI**

```bash
ov session delete a1b2c3d4
```

**Response Example**

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

#### 1. API Implementation Introduction

Add a message to the session. Supports two modes: simple text mode and Parts mode (supporting text, context references, tool calls, etc.).

**Part Types:**
- `TextPart`: Pure text content
- `ContextPart`: Context reference pointing to resources or memories
- `ToolPart`: Tool call and result

**Code Entries:**
- `openviking/session/session.py:Session.add_message()` - Core implementation
- `openviking/server/routers/sessions.py:add_message()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.add_message()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:add_message()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| role | str | Yes | - | Message role: "user" or "assistant" |
| parts | List[Part] | Conditional | - | List of message parts (Required for Python SDK; Optional for HTTP API, mutually exclusive with content) |
| content | str | Conditional | - | Message text content (HTTP API simple mode, mutually exclusive with parts) |
| created_at | str | No | None | Optional ISO 8601 timestamp to persist on the message |
| role_id | str | No | None | Optional explicit participant ID, server-derived if omitted |

> **Note**: HTTP API supports two modes:
> 1. **Simple mode**: Use `content` string (backward compatible)
> 2. **Parts mode**: Use `parts` array (full Part support)
>
> If both `content` and `parts` are provided, `parts` takes precedence.

**Part Types (Python SDK)**

```python
from openviking.message import TextPart, ContextPart, ToolPart

# Text content
TextPart(text="Hello, how can I help?")

# Context reference
ContextPart(
    uri="viking://resources/docs/auth/",
    context_type="resource",  # "resource", "memory", or "skill"
    abstract="Authentication guide..."
)

# Tool call
ToolPart(
    tool_id="call_123",
    tool_name="search_web",
    skill_uri="viking://agent/skills/search-web/",
    tool_input={"query": "OAuth best practices"},
    tool_output="",
    tool_status="pending"  # "pending", "running", "completed", "error"
)
```

#### 3. Usage Examples

**HTTP API**

```http
POST /api/v1/sessions/{session_id}/messages
```

**Simple Mode (Backward Compatible)**

```bash
# Add user message
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "user",
    "content": "How do I authenticate users?"
  }'
```

**Parts Mode (Full Part Support)**

```bash
# Add assistant message with context reference
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

# Add assistant message with tool call
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

# Simple mode: Add user message
await client.add_message(
    session_id="a1b2c3d4",
    role="user",
    content="How do I authenticate users?"
)

# Parts mode: Add assistant message with context reference
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

**Response Example**

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

#### 1. API Implementation Introduction

Record actually used contexts and skills in the session. When `commit()` is called, `active_count` is updated based on this usage data to optimize future retrieval ranking.

**Code Entries:**
- `openviking/session/session.py:Session.used()` - Core implementation
- `openviking/server/routers/sessions.py:record_used()` - HTTP route

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| contexts | List[str] | No | None | List of context URIs that were actually used |
| skill | Dict[str, Any] | No | None | Skill usage record with keys: `uri`, `input`, `output`, `success` |

#### 3. Usage Examples

**HTTP API**

```http
POST /api/v1/sessions/{session_id}/used
```

```bash
# Record used contexts
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"contexts": ["viking://resources/docs/auth/"]}'

# Record used skill
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"skill": {"uri": "viking://agent/skills/search-web/", "input": {"query": "OAuth"}, "output": "Results...", "success": true}}'
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# Record used contexts
await client.session_used(
    session_id="a1b2c3d4",
    contexts=["viking://resources/docs/auth/"]
)

# Record used skill
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

**Response Example**

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

#### 1. API Implementation Introduction

Commit a session. Message archiving (Phase 1) completes immediately. Summary generation and memory extraction (Phase 2) run asynchronously in the background. Returns a `task_id` for polling progress.

**Two-Phase Commit Flow:**
- **Phase 1 (Synchronous)**: Snapshot current messages, clear live session, create archive directory, write original messages
- **Phase 2 (Asynchronous)**: Generate summaries (L0/L1), extract long-term memories, update relations and active_count

**Notes:**
- Rapid consecutive commits on the same session are accepted; each request gets its own `task_id`.
- Background Phase 2 work is serialized by archive order: archive `N+1` waits until archive `N` writes `.done`.
- If an earlier archive failed and left no `.done`, later commit requests fail with `FAILED_PRECONDITION` until that failure is resolved.

**Code Entries:**
- `openviking/session/session.py:Session.commit_async()` - Core implementation
- `openviking/server/routers/sessions.py:commit_session()` - HTTP route
- `openviking_cli/client/base.py:BaseClient.commit_session()` - Python SDK
- `crates/ov_cli/src/commands/session.rs:commit_session()` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID to commit |

#### 3. Usage Examples

**HTTP API**

```http
POST /api/v1/sessions/{session_id}/commit
```

```bash
# Commit session (returns immediately)
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"

# Poll task status
curl -X GET http://localhost:1933/api/v1/tasks/{task_id} \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
import openviking as ov

client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# Commit returns immediately with task_id; summary + memory extraction runs in background
result = await client.commit_session("a1b2c3d4")
print(f"Status: {result['status']}")
print(f"Task ID: {result['task_id']}")

# Poll background task progress
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

**Response Example**

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

#### 1. API Implementation Introduction

HTTP API only. Trigger memory extraction immediately for an existing session without creating a new commit task.

**Code Entries:**
- `openviking/server/routers/sessions.py:extract_session()` - HTTP route

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID to extract memories from |

#### 3. Usage Examples

**HTTP API**

```http
POST /api/v1/sessions/{session_id}/extract
```

```bash
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/extract \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
```

**Response Example**

The endpoint returns the extracted memory write results as a JSON list. The exact item shape depends on which memories were produced for that session.

---

### get_task()

#### 1. API Implementation Introduction

Query background task status (e.g., commit summary generation and memory extraction progress).

**Task Statuses:**
- `pending`: Task waiting to execute
- `running`: Task in progress
- `completed`: Task successfully completed
- `failed`: Task failed

**Code Entries:**
- `openviking/server/routers/tasks.py:get_task()` - HTTP route

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_id | str | Yes | - | Task ID (returned by commit) |

#### 3. Usage Examples

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

**Response Example (in progress)**

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

**Response Example (completed)**

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

`memories_extracted` in the completed task result reports per-category counts for this commit only. Sum its values when you want the total for this commit.

---

### list_tasks()

#### 1. API Implementation Introduction

HTTP API only. List background tasks visible to the current caller, supporting filtering by type, status, resource.

**Code Entries:**
- `openviking/server/routers/tasks.py:list_tasks()` - HTTP route

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_type | str | No | None | Filter by task type, for example `session_commit` |
| status | str | No | None | Filter by task status: `pending`, `running`, `completed`, `failed` |
| resource_id | str | No | None | Filter by task resource ID, for example a session ID |
| limit | int | No | 50 | Maximum number of task records to return |

#### 3. Usage Examples

**HTTP API**

```http
GET /api/v1/tasks?task_type=session_commit&status=running&limit=20
```

```bash
curl -X GET "http://localhost:1933/api/v1/tasks?task_type=session_commit&status=running&limit=20" \
  -H "X-API-Key: your-key"
```

**Response Example**

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

## Session Properties

| Property | Type | Description |
|----------|------|-------------|
| uri | str | Session Viking URI (`viking://session/{session_id}/`) |
| messages | List[Message] | Current messages in the session |
| stats | SessionStats | Session statistics |
| summary | str | Compression summary |
| usage_records | List[Usage] | Context and skill usage records |

---

## Session Storage Structure

```
viking://session/{user_id}/{session_id}/
+-- .abstract.md              # L0: Session overview
+-- .overview.md              # L1: Key decisions
+-- messages.jsonl            # Current messages
+-- tools/                    # Tool executions
|   +-- {tool_id}/
|       +-- tool.json
+-- .meta.json                # Metadata
+-- .relations.json           # Related contexts
+-- history/                  # Archived history
    +-- archive_001/
    |   +-- messages.jsonl    # Written in Phase 1
    |   +-- .abstract.md      # Written in Phase 2 (background)
    |   +-- .overview.md      # Written in Phase 2 (background)
    |   +-- .meta.json        # Archive metadata
    |   +-- memory_diff.json  # Written in Phase 2 (background, on memory changes)
    |   +-- .done             # Phase 2 completion marker
    |   +-- .failed.json      # Phase 2 failure marker
    +-- archive_002/
```

### memory_diff.json Structure

Each commit writes a `memory_diff.json` to the archive directory, recording all memory changes for auditing and rollback:

```json
{
  "archive_uri": "viking://session/{session_id}/history/archive_001",
  "extracted_at": "2026-04-21T10:00:00Z",
  "operations": {
    "adds": [
      {
        "uri": "memory/user/xxx/identity.md",
        "memory_type": "identity",
        "after": "Newly created file content"
      }
    ],
    "updates": [
      {
        "uri": "memory/user/xxx/context/project.md",
        "memory_type": "context",
        "before": "Content before modification",
        "after": "Content after modification"
      }
    ],
    "deletes": [
      {
        "uri": "memory/user/xxx/context/old.md",
        "memory_type": "context",
        "deleted_content": "Deleted file content"
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

| Field | Type | Description |
|-------|------|-------------|
| `archive_uri` | str | Archive directory URI for this commit |
| `extracted_at` | str | ISO 8601 timestamp of extraction |
| `operations.adds` | array | New memories created (`uri`, `memory_type`, `after`) |
| `operations.updates` | array | Modified memories (`uri`, `memory_type`, `before`, `after`) |
| `operations.deletes` | array | Deleted memories (`uri`, `memory_type`, `deleted_content`) |
| `summary.total_adds` | int | Number of new memories |
| `summary.total_updates` | int | Number of modified memories |
| `summary.total_deletes` | int | Number of deleted memories |

An empty `memory_diff.json` (all counts zero) is written even when no memory operations occurred.

---

## Memory Categories

| Category | Location | Description |
|----------|----------|-------------|
| profile | `user/memories/profile.md` | User profile information |
| preferences | `user/memories/preferences/` | User preferences by topic |
| entities | `user/memories/entities/` | Important entities (people, projects) |
| events | `user/memories/events/` | Significant events |
| cases | `agent/memories/cases/` | Problem-solution cases |
| patterns | `agent/memories/patterns/` | Interaction patterns |
| tools | `agent/memories/tools/` | Tool usage knowledge and best practices |
| skills | `agent/memories/skills/` | Skill execution knowledge and workflow strategies |

---

## Full Example

**Python SDK**

```python
import openviking as ov
from openviking.message import TextPart, ContextPart

# Initialize client
client = ov.Client(base_url="http://localhost:1933", api_key="your-key")

# Create new session
session_result = await client.create_session()
session_id = session_result["session_id"]
print(f"Session created: {session_id}")

# Add user message
await client.add_message(
    session_id=session_id,
    role="user",
    content="How do I configure embedding?"
)

# Search with session context
results = await client.search("embedding configuration", session_id=session_id)

# Add assistant message with context reference
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

    # Track actually used contexts
    await client.session_used(
        session_id=session_id,
        contexts=[results.resources[0].uri]
    )

# Commit session (returns immediately; summary + memory extraction runs in background)
commit_result = await client.commit_session(session_id)
print(f"Task ID: {commit_result['task_id']}")

# Optional: poll for completion
task = await client.get_task(commit_result["task_id"])
if task and task["status"] == "completed":
    memories = task["result"]["memories_extracted"]
    total = sum(memories.values())
    print(f"Memories extracted: {total}")
```

**HTTP API**

```bash
# Step 1: Create session
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
# Returns: {"status": "ok", "result": {"session_id": "a1b2c3d4"}}

# Step 2: Add user message
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"role": "user", "content": "How do I configure embedding?"}'

# Step 3: Search with session context
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"query": "embedding configuration", "session_id": "a1b2c3d4"}'

# Step 4: Add assistant message
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"role": "assistant", "content": "Based on the documentation, you can configure embedding..."}'

# Step 5: Record used contexts
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"contexts": ["viking://resources/docs/embedding/"]}'

# Step 6: Commit session (returns immediately with task_id)
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key"
# Returns: {"status": "ok", "result": {"status": "accepted", "task_id": "uuid-xxx", ...}}

# Step 7: Poll background task progress (optional)
curl -X GET http://localhost:1933/api/v1/tasks/uuid-xxx \
  -H "X-API-Key: your-key"
```

## Best Practices

### Commit Regularly

```python
# Commit after significant interactions
session_info = await client.get_session(session_id)
if session_info["message_count"] > 10:
    await client.commit_session(session_id)
```

### Track What's Actually Used

```python
# Only mark contexts that were actually helpful
if context_was_useful:
    await client.session_used(session_id=session_id, contexts=[ctx.uri])
```

### Use Session Context for Search

```python
# Better search results with conversation context
results = await client.search(query, session_id=session_id)
```

---

## Related Documentation

- [Context Types](../concepts/02-context-types.md) - Memory types
- [Retrieval](06-retrieval.md) - Search with session
- [Resources](02-resources.md) - Resource management
