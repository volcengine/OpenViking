# Session Management

Session manages conversation messages, tracks context usage, and extracts long-term memories.

## Overview

**Lifecycle**: Create → Interact → Commit

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
| `commit()` | Commit: archive + memory extraction |

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
#   "status": "committed",
#   "memories_extracted": 5,
#   "active_count_updated": 2,
#   "archived": True
# }
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

Auto-archive on commit():

1. Increment compression_index
2. Copy current messages to archive directory
3. Generate structured summary (LLM)
4. Clear current messages list

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

### 6 Categories

| Category | Belongs to | Description | Mergeable |
|----------|------------|-------------|-----------|
| **profile** | user | User identity/attributes | ✅ |
| **preferences** | user | User preferences | ✅ |
| **entities** | user | Entities (people/projects) | ✅ |
| **events** | user | Events/decisions | ❌ |
| **cases** | agent | Problem + solution | ❌ |
| **patterns** | agent | Reusable patterns | ✅ |

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

## Storage Structure

```
viking://session/{session_id}/
├── messages.jsonl            # Current messages
├── .abstract.md              # Current abstract
├── .overview.md              # Current overview
├── history/
│   ├── archive_001/
│   │   ├── messages.jsonl
│   │   ├── .abstract.md
│   │   └── .overview.md
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
└── patterns/
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Three context types
- [Context Extraction](./06-extraction.md) - Extraction flow
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
