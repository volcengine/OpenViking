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

Get session details.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID |

**Python SDK (Embedded / HTTP)**

```python
# Load existing session
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

**Response**

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
| parts | List[Part] | Yes | - | List of message parts (SDK) |
| content | str | Yes | - | Message text content (HTTP API) |

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

```bash
# Add user message
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/messages \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "role": "user",
    "content": "How do I authenticate users?"
  }'

# Add assistant message
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

### commit()

Commit a session by archiving messages and extracting memories.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | str | Yes | - | Session ID to commit |

**Python SDK (Embedded / HTTP)**

```python
session = client.session(session_id="a1b2c3d4")
session.load()

# Commit archives messages and extracts memories
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

**Response**

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
    |   +-- messages.jsonl
    |   +-- .abstract.md
    |   +-- .overview.md
    +-- archive_002/
```

---

## Memory Categories

| Category | Location | Description |
|----------|----------|-------------|
| profile | `user/memories/.overview.md` | User profile information |
| preferences | `user/memories/preferences/` | User preferences by topic |
| entities | `user/memories/entities/` | Important entities (people, projects) |
| events | `user/memories/events/` | Significant events |
| cases | `agent/memories/cases/` | Problem-solution cases |
| patterns | `agent/memories/patterns/` | Interaction patterns |

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

# Commit session (archive messages, extract memories)
result = session.commit()
print(f"Memories extracted: {result['memories_extracted']}")

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

# Step 5: Commit session
curl -X POST http://localhost:1933/api/v1/sessions/a1b2c3d4/commit \
  -H "Content-Type: application/json" \
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
