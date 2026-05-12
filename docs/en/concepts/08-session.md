# Session Management

Session manages conversation messages, tracks context usage, and extracts long-term memories.

## Overview

**Lifecycle**: Create → Interact → Commit

Getting a session by ID does not auto-create it by default. Use `client.get_session(..., auto_create=True)` when you want missing sessions to be created automatically.

```python
session = client.session(session_id="chat_001")
session.add_message("user", [TextPart("...")])
session.commit()
```

## Core API

| Method | Description |
|--------|-------------|
| `add_message(role, parts)` | Add message |
| `used(contexts, skill)` | Record used contexts/skills |
| `commit()` | Commit: archive (sync) + summary generation and memory extraction (async background) |
| `get_task(task_id)` | Query background task status |

### add_message

```python
session.add_message(
    "user",
    [TextPart("How to configure embedding?")]
)

session.add_message(
    "assistant",
    [
        TextPart("Here's how..."),
        ContextPart(uri="viking://user/memories/profile.md"),
    ]
)
```

### used

```python
# Record used contexts
session.used(contexts=["viking://user/memories/profile.md"])

# Record used skill
session.used(skill={
    "uri": "viking://agent/skills/code-search",
    "input": "search config",
    "output": "found 3 files",
    "success": True
})
```

### commit

```python
result = session.commit()
# {
#   "status": "accepted",
#   "task_id": "uuid-xxx",
#   "archive_uri": "viking://session/.../history/archive_001",
#   "archived": True
# }

# Poll background task progress
task = client.get_task(result["task_id"])
# task["status"]: "pending" | "running" | "completed" | "failed"
# sum(task["result"]["memories_extracted"].values()): 3
```

## Message Structure

### Message

```python
@dataclass
class Message:
    id: str              # msg_{UUID}
    role: str            # "user" | "assistant"
    parts: List[Part]    # Message parts
    created_at: datetime
```

### Part Types

| Type | Description |
|------|-------------|
| `TextPart` | Text content |
| `ContextPart` | Context reference (URI + abstract) |
| `ToolPart` | Tool call (input + output) |

## Compression Strategy

### Archive Flow

commit() executes in two phases:

**Phase 1 (synchronous, returns immediately)**:
1. Increment compression_index
2. Write messages to archive directory (`messages.jsonl`)
3. Clear current messages list
4. Return `task_id`

**Phase 2 (asynchronous background)**:
5. Generate structured summary (LLM) → write `.abstract.md` and `.overview.md`
6. Extract long-term memories
7. Write `memory_diff.json` (memory change audit log) to archive directory
8. Update active_count
9. Write `.done` completion marker

### Summary Format

```markdown
# Session Summary

**One-line overview**: [Topic]: [Intent] | [Result] | [Status]

## Analysis
Key steps list

## Primary Request and Intent
User's core goal

## Key Concepts
Key technical concepts

## Pending Tasks
Unfinished tasks
```

## Memory Extraction

### 8 Categories

| Category | Belongs to | Description | Mergeable |
|----------|------------|-------------|-----------|
| **profile** | user | User identity/attributes | ✅ |
| **preferences** | user | User preferences | ✅ |
| **entities** | user | Entities (people/projects) | ✅ |
| **events** | user | Events/decisions | ❌ |
| **cases** | agent | Problem + solution | ❌ |
| **patterns** | agent | Reusable patterns | ✅ |
| **tools** | agent | Tool usage knowledge and best practices | ✅ |
| **skills** | agent | Skill execution knowledge and workflow strategies | ✅ |

### Extraction Flow

```
Messages → LLM Extract → Candidate Memories
              ↓
Vector Pre-filter → Find Similar Memories
              ↓
LLM Dedup Decision → candidate(skip/create/none) + item(merge/delete)
              ↓
Write to AGFS → Vectorize
```

### Dedup Decisions

| Level | Decision | Description |
|------|----------|-------------|
| Candidate | `skip` | Candidate is duplicate, skip and do nothing |
| Candidate | `create` | Create candidate memory (optionally delete conflicting existing memories first) |
| Candidate | `none` | Do not create candidate; resolve existing memories by item decisions |
| Per-existing item | `merge` | Merge candidate content into specified existing memory |
| Per-existing item | `delete` | Delete specified conflicting existing memory |

## Memory Diff

Each `session.commit()` writes a `memory_diff.json` to the archive directory, recording all memory changes from that commit for auditing and rollback.

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

| Field | Description |
|-------|-------------|
| `archive_uri` | Archive directory URI for this commit |
| `extracted_at` | ISO 8601 timestamp of extraction |
| `operations.adds` | New memories created (no `before`) |
| `operations.updates` | Modified memories (with `before` and `after`) |
| `operations.deletes` | Deleted memories (with `deleted_content`) |
| `summary` | Counts per operation type |

An empty `memory_diff.json` (all counts zero) is written even when no memory operations occurred.

## Storage Structure

```
viking://session/{session_id}/
├── messages.jsonl            # Current messages
├── .abstract.md              # Current abstract
├── .overview.md              # Current overview
├── history/
│   ├── archive_001/
│   │   ├── messages.jsonl    # Written in Phase 1
│   │   ├── .abstract.md      # Written in Phase 2 (background)
│   │   ├── .overview.md      # Written in Phase 2 (background)
│   │   ├── memory_diff.json  # Written in Phase 2 (background, memory change audit)
│   │   └── .done             # Phase 2 completion marker
│   └── archive_NNN/
└── tools/
    └── {tool_id}/tool.json

viking://user/memories/
├── profile.md                # Append-only user profile
├── preferences/
├── entities/
└── events/

viking://agent/memories/
├── cases/
├── patterns/
├── tools/
└── skills/
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Three context types
- [Context Extraction](./06-extraction.md) - Extraction flow
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
