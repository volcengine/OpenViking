# Session Management

Session manages conversation messages, tracks context usage, and extracts long-term memories.

## Overview

**Lifecycle**: Create → Interact → Commit

Getting a session by ID does not create it. Create the session first, then use
`client.session(session_id=...)` to append messages or commit it.

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
session_info = client.create_session(session_id="chat_001")
session = client.session(session_id=session_info["session_id"])
session.add_message(role="user", content="...")
session.commit()
```

## Core API

| Method | Description |
|--------|-------------|
| `add_message(role, content=None, parts=None, options=None, peer_id=None)` | Add message |
| `commit()` | Commit: archive (sync) + summary generation and memory extraction (async background) |
| `client.get_task(task_id)` | Query background task status |

### add_message

```python
from openviking_sdk import ContextPart, ImagePart, TextPart

session.add_message(
    role="user",
    content="How to configure embedding?",
)

session.add_message(
    role="assistant",
    parts=[
        TextPart(text="Here's how..."),
        ContextPart(
            uri="viking://~/memories/profile.md",
            context_type="memory",
            abstract="User profile",
        ),
    ]
)

session.add_message(
    role="user",
    parts=[
        TextPart(text="Remember this studio layout."),
        ImagePart(url="https://example.com/studio.png", detail="auto"),
    ]
)
```

### commit

```python
result = session.commit()
# {
#   "status": "accepted",
#   "task_id": "uuid-xxx",
#   "archive_uri": "viking://user/{user_id}/sessions/.../history/archive_001",
#   "archived": True
# }

# A task is created only for an archive; no-op commits return skipped and task_id: null.
task_id = result.get("task_id")
if task_id:
    task = client.get_task(task_id=task_id)
    print(task)  # One status check does not mean the task has finished.
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
| `ImagePart` | Image URL content. During memory extraction, OpenViking can describe it with the configured VLM. |
| `ContextPart` | Context reference (URI + abstract) |
| `ToolPart` | Tool call (input + output) |

## Compression Strategy

### Archive Flow

commit() executes in two phases:

**Phase 1 (archive preparation within the request)**:
1. Allocate the archive number and split archived and retained messages under a path lock
2. Persist archive messages (`messages.jsonl`) and enqueue processing in the durable queue
3. Update the live message list; default commits archive all messages, while retention parameters can keep recent messages or turns
4. Return `task_id` when an archive was created, to track background processing

**Phase 2 (asynchronous background)**:
5. Generate structured summary (LLM) → write `.abstract.md` and `.overview.md`
6. Extract long-term memories
7. Write `memory_diff.json` (memory change audit log) to archive directory
8. Write `.done` completion marker

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

### Memory Types

After a session is committed, OpenViking uses the conversation and active memory policy to extract information that can improve future interactions. It stores the result in the current user's memory space. When a conversation involves a stable Peer, relevant memories can also be stored in that Peer's space.

OpenViking includes memory types such as `profile`, `preferences`, `entities`, `events`, `identity`, `soul`, `cases`, `trajectories`, and `experiences`, and supports custom types for application-specific needs. See [Context Types](./02-context-types.md) for the complete purpose and path mapping.

Within `memory_policy.memory_types`, `experiences` enables the complete Agent Evolution pipeline and automatically activates `cases` and `trajectories`. If `experiences` is absent, explicitly supplied `cases` and `trajectories` entries are ignored without an error.

### Extraction Flow

```text
Archived messages + enabled memory schemas
    → ExtractLoop reads relevant memories and produces update operations
    → MemoryUpdater validates and applies additions, edits, and deletions
    → Persist memories, update indexes, and record memory_diff.json
```

Existing memories inform extraction. An update can merge or edit existing files, create files, or delete them. Memory schemas, write permissions, and output validation constrain these operations; a commit does not necessarily add a memory. Subsequent training produces trajectories, experiences, or enabled session skills only when extraction yields a case.

<a id="dedup-decisions"></a>

### Checking Update Results

Wait for the commit task, then inspect `memory_diff.json`. Additions, updates, and deletions describe actual file changes. `skipped_operations` records proposed operations skipped by validation or policy. Updates that leave content unchanged are excluded from effective changes in the diff.

## Memory Diff

Background processing writes `memory_diff.json` to the archive directory, recording memory changes for auditing and review. It may not exist when `task_id` is returned; check task completion as described in the [Sessions API](../api/05-sessions.md).

```json
{
  "archive_uri": "viking://user/{user_id}/sessions/{session_id}/history/archive_001",
  "extracted_at": "2026-04-21T10:00:00Z",
  "operations": {
    "adds": [
      {
        "uri": "viking://user/alice/memories/identity.md",
        "memory_type": "identity",
        "after": "Newly created file content"
      }
    ],
    "updates": [
      {
        "uri": "viking://user/alice/memories/entities/project.md",
        "memory_type": "entities",
        "before": "Content before modification",
        "after": "Content after modification"
      }
    ],
    "deletes": [
      {
        "uri": "viking://user/alice/memories/entities/old.md",
        "memory_type": "entities",
        "deleted_content": "Deleted file content"
      }
    ]
  },
  "skipped_operations": [
    {
      "memory_type": "events",
      "page_id": 101,
      "reason_code": "invalid_ranges",
      "reason": "No valid event range could be resolved"
    }
  ],
  "summary": {
    "total_adds": 1,
    "total_updates": 1,
    "total_deletes": 1,
    "total_skipped": 1
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
| `skipped_operations` | Intentionally skipped operations and their stable reason codes; these are not file changes |
| `summary` | Counts per operation type |

An empty `memory_diff.json` (all counts zero) is written when no applied or intentionally skipped operations occurred.

## Storage Structure

```
viking://user/{user_id}/sessions/{session_id}/
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

viking://~/memories/
├── profile.md
├── identity.md
├── soul.md
├── preferences/
├── entities/
├── events/
├── cases/
├── trajectories/
└── experiences/
```

`viking://~/sessions/{session_id}` uses the home alias and is expanded to
`viking://user/{user_id}/sessions/{session_id}` for the authenticated caller.
The uid-less spelling `viking://user/sessions/{session_id}` is no longer accepted
and returns an error pointing at the `viking://~/...` form. The old
`viking://session/{session_id}` form is still accepted as a backward-compatible
alias for the same session path and is not a separate storage root.

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Three context types
- [Context Extraction](./06-extraction.md) - Extraction flow
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
