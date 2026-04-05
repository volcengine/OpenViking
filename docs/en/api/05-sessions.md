# Sessions

Sessions manage conversation state, track context usage, and extract long-term memories.

## API Reference

### create_session()

Create a new session.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | No | None | Session ID. Creates new session with auto-generated ID if None |

**Python SDK (Embedded / HTTP)**

```python
# Create new session (auto-generated ID)
session = client.session()
print(f"Session URI: {session.uri}")

# Create new session with specified ID
session = client.create_session(session_id="my-custom-session-id")
print(f"Session ID: {session['session_id']}")
```

**HTTP API**

```
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

**CLI**

```bash
openviking session new
```

**Response**

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

List all sessions.

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

**Response**

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

Get session details. Returns NOT_FOUND when the session does not exist by default. Pass `auto_create=True` to create it automatically.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| auto_create | bool | No | False | Whether to auto-create the session if it does not exist |

**Python SDK (Embedded / HTTP)**

```python
# Get existing session (raises NotFoundError if not found)
info = client.get_session("a1b2c3d4")
print(f"Messages: {info['message_count']}, Commits: {info['commit_count']}")

# Get or create session
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

**Response**

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

Get the assembled session context used by OpenClaw-style context rebuilding.

This endpoint returns:
- `latest_archive_overview`: the `overview` of the latest completed archive, when it fits the token budget
- `pre_archive_abstracts`: lightweight entries for completed archives, each containing `archive_id` and `abstract`
- `messages`: all incomplete archive messages after the latest completed archive, plus current live session messages
- `stats`: token and inclusion stats for the returned context

Notes:
- `latest_archive_overview` becomes an empty string when no completed archive exists, or when the latest overview does not fit in the token budget.
- `token_budget` is applied to the assembled payload after active `messages`: `latest_archive_overview` has higher priority than `pre_archive_abstracts`, and older abstracts are dropped first when budget is tight.
- Only archive content that is actually returned is counted toward `estimatedTokens` and `stats.archiveTokens`.
- Session commit generates an archive summary during Phase 2 for every non-empty archive attempt. Only archives with a completed `.done` marker are exposed here.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| token_budget | int | No | 128000 | Token budget for assembled archive payload after active `messages` |

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

**Response**

```json
{
  "status": "ok",
  "result": {
    "latest_archive_overview": "# Session Summary\n\n**Overview**: User discussed deployment and auth setup.",
    "pre_archive_abstracts": [
      {
        "archive_id": "archive_002",
        "abstract": "User discussed deployment and authentication setup."
      },
      {
        "archive_id": "archive_001",
        "abstract": "User previously discussed repository bootstrap and authentication setup."
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
    "estimatedTokens": 173,
    "stats": {
      "totalArchives": 2,
      "includedArchives": 2,
      "droppedArchives": 0,
      "failedArchives": 0,
      "activeTokens": 98,
      "archiveTokens": 75
    }
  }
}
```

---

### get_session_archive()

Get the full contents of one completed archive for a session.

This endpoint is intended to work with `pre_archive_abstracts[*].archive_id` returned by `get_session_context()`.

This endpoint returns:
- `archive_id`: the archive ID that was expanded
- `abstract`: the lightweight summary for the archive
- `overview`: the full archive overview
- `messages`: the archived transcript for that archive

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |
| archive_id | str | Yes | - | Archive ID such as `archive_002` |

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

**Response**

```json
{
  "status": "ok",
  "result": {
    "archive_id": "archive_002",
    "abstract": "User discussed deployment and auth setup.",
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

If the archive does not exist, is incomplete, or does not belong to the session, the API returns `404`.

---

### delete_session()

Delete a session.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID to delete |

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

**Response**

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

Add a message to the session.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| role | str | Yes | - | Message role: "user" or "assistant" |
| parts | List[Part] | Conditional | - | List of message parts (Required for Python SDK; Optional for HTTP API, mutually exclusive with content) |
| content | str | Conditional | - | Message text content (HTTP API simple mode, mutually exclusive with parts) |

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
    skill_uri="viking://skills/search-web/",
    tool_input={"query": "OAuth best practices"},
    tool_output="",
    tool_status="pending"  # "pending", "running", "completed", "error"
)
```

**Python SDK (Embedded / HTTP)**

```python
from openviking.message import TextPart

session = client.session()

# Add user message
session.add_message("user", [
    TextPart(text="How do I authenticate users?")
])

# Add assistant response
session.add_message("assistant", [
    TextPart(text="You can use OAuth 2.0 for authentication...")
])
```

**HTTP API**

```
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

**CLI**

```bash
openviking session add-message a1b2c3d4 --role user --content "How do I authenticate users?"
```

**Response**

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

Record actually used contexts and skills in the session. When `commit()` is called, `active_count` is updated based on this usage data.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| contexts | List[str] | No | None | List of context URIs that were actually used |
| skill | Dict[str, Any] | No | None | Skill usage record with keys: `uri`, `input`, `output`, `success` |

**Python SDK (Embedded / HTTP)**

```python
session = client.session(session_id="a1b2c3d4")
session.load()

# Record used contexts
session.used(contexts=["viking://resources/docs/auth/"])

# Record used skill
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
# Record used contexts
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"contexts": ["viking://resources/docs/auth/"]}'

# Record used skill
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/used \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"skill": {"uri": "viking://skills/search-web/", "input": {"query": "OAuth"}, "output": "Results...", "success": true}}'
```

**Response**

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

Commit a session. Message archiving (Phase 1) completes immediately. Summary generation and memory extraction (Phase 2) run asynchronously in the background. Returns a `task_id` for polling progress.

Notes:
- Rapid consecutive commits on the same session are accepted; each request gets its own `task_id`.
- Background Phase 2 work is serialized by archive order: archive `N+1` waits until archive `N` writes `.done`.
- If an earlier archive failed and left no `.done`, later commit requests fail with `FAILED_PRECONDITION` until that failure is resolved.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID to commit |

**Python SDK (Embedded / HTTP)**

```python
session = client.session(session_id="a1b2c3d4")
session.load()

# Commit returns immediately with task_id; summary + memory extraction runs in background
result = session.commit()
print(f"Status: {result['status']}")       # "accepted"
print(f"Task ID: {result['task_id']}")

# Poll background task progress
task = client.get_task(result["task_id"])
if task["status"] == "completed":
    print(f"Memories extracted: {sum(task['result']['memories_extracted'].values())}")
```

**HTTP API**

```
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

**CLI**

```bash
openviking session commit a1b2c3d4
```

**Response**

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

Query background task status (e.g., commit summary generation and memory extraction progress).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| task_id | str | Yes | - | Task ID (returned by commit) |

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

**Response (in progress)**

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

**Response (completed)**

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

`memories_extracted` in the completed task result reports per-category counts for this commit only. Sum its values when you want the total for this commit.

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
viking://session/{session_id}/
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
    |   +-- .done             # Phase 2 completion marker
    +-- archive_002/
```

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

**Python SDK (Embedded / HTTP)**

```python
import openviking as ov
from openviking.message import TextPart, ContextPart

# Initialize client
client = ov.OpenViking(path="./my_data")
client.initialize()

# Create new session
session = client.session()

# Add user message
session.add_message("user", [
    TextPart(text="How do I configure embedding?")
])

# Search with session context
results = client.search("embedding configuration", session=session)

# Add assistant response with context reference
session.add_message("assistant", [
    TextPart(text="Based on the documentation, you can configure embedding..."),
    ContextPart(
        uri=results.resources[0].uri,
        context_type="resource",
        abstract=results.resources[0].abstract
    )
])

# Track actually used contexts
session.used(contexts=[results.resources[0].uri])

# Commit session (returns immediately; summary + memory extraction runs in background)
result = session.commit()
print(f"Task ID: {result['task_id']}")

# Optional: poll for completion
task = client.get_task(result["task_id"])
if task and task["status"] == "completed":
    print(f"Memories extracted: {sum(task['result']['memories_extracted'].values())}")

client.close()
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
if len(session.messages) > 10:
    session.commit()
```

### Track What's Actually Used

```python
# Only mark contexts that were actually helpful
if context_was_useful:
    session.used(contexts=[ctx.uri])
```

### Use Session Context for Search

```python
# Better search results with conversation context
results = client.search(query, session=session)
```

### Load Before Continuing

```python
# Always load when resuming an existing session
session = client.session(session_id="existing-id")
session.load()
```

---

## Related Documentation

- [Context Types](../concepts/02-context-types.md) - Memory types
- [Retrieval](06-retrieval.md) - Search with session
- [Resources](02-resources.md) - Resource management
