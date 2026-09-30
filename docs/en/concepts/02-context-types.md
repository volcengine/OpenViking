# Context Types

OpenViking manages three types of context: resources provide reference material, memories retain information from interactions, and skills describe how to carry out tasks.

## Overview

| Type | Purpose | Lifecycle | Initiative |
|------|---------|-----------|------------|
| **Resource** | Knowledge and rules | Retained until updated or deleted | User adds |
| **Memory** | Preferences, facts, and task experience | Long-term, dynamically updated | Extracted from sessions or recorded explicitly |
| **Skill** | Task instructions and supporting resources | Long-term, updatable | User or system adds |

## Example Setup

The examples below use the synchronous Python SDK and an existing server. `add_resource` and `add_skill` take `wait=True` to block until processing finishes. Session commits return before memory extraction finishes and have no built-in wait, so this helper polls the commit task before dependent searches. A polling timeout does not cancel the server task.

```python
import time
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")


def wait_for_task(task_id):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        task = client.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} not found")
        if task["status"] == "completed":
            return task
        if task["status"] in {"failed", "cancelled"}:
            raise RuntimeError(task)
        time.sleep(1)
    raise TimeoutError(f"Task {task_id} is still running")
```

## Resource

Resources are external knowledge that Agents can reference.

### Characteristics

- **User-driven**: Resource information actively added by users to supplement LLM knowledge, such as product manuals and code repositories
- **Explicit updates**: Re-import changed content, or use a Watch to refresh supported remote sources
- **Structured storage**: Organized by project or topic in directory hierarchy, with multi-layer information extraction

### Examples

- API docs, product manuals
- FAQ databases, code repositories
- Research papers, technical specs

### Usage

```python
# Add resource
client.add_resource(
    path="https://docs.example.com/api.pdf",
    options={"reason": "API documentation"},
    wait=True,
    timeout=300,
)

# Search resources
results = client.find(
    query="authentication methods",
    target_uri="viking://resources/",
)
```

## Memory

Memories are durable knowledge learned from interactions and task execution. They are stored in the current User or Peer namespace, not in a separate `viking://agent/memories` directory.

### Characteristics

- **Agent-driven**: Memory information actively extracted and recorded by Agent
- **Dynamic updates**: Continuously updated from interactions by Agent
- **Personalized**: Learned for specific users and stable peers

### Built-in Memory Types

| Type | Default location | Description |
|------|------------------|-------------|
| **profile** | `~/memories/profile.md` | Basic user information |
| **preferences** | `~/memories/preferences/` | User preferences organized by topic |
| **entities** | `~/memories/entities/` | Knowledge about people, projects, organizations, and other entities |
| **events** | `~/memories/events/` | Decisions, milestones, and other event records |
| **identity** | `~/memories/identity.md` | Assistant name, persona, temperament, and self-introduction |
| **soul** | `~/memories/soul.md` | Assistant principles, boundaries, style, and continuity |
| **cases** | `~/memories/cases/` | Task cases used for training and evaluation |
| **trajectories** | `~/memories/trajectories/` | Reusable task-execution trajectories |
| **experiences** | `~/memories/experiences/` | Reusable experience distilled from execution outcomes |

The `~/...` entries above use the home alias `viking://~`, which the server expands to `viking://user/{user_id}/...` for the authenticated caller. When the memory policy permits Peer memory, supported types may instead be written under `viking://user/{user_id}/peers/{peer_id}/memories/...`. Applications can extend or adjust memory types with custom templates.

The schema-defined `memories/tools/` and `memories/skills/` types are disabled. They are separate from standalone Skills stored under `viking://user/{user_id}/skills/{skill_name}/SKILL.md`, which remain supported.

### Usage

```python
from openviking_sdk import TextPart

# Memories are auto-extracted from sessions
session_info = client.create_session()
session = client.session(session_id=session_info["session_id"])
session.add_message(
    role="user",
    parts=[TextPart(text="I prefer dark mode")],
)
commit = session.commit()  # Starts background memory extraction
if commit.get("task_id"):
    wait_for_task(commit["task_id"])

# Search memories
results = client.find(
    query="UI preferences",
    target_uri="viking://~/memories/"
)
```

## Skill (Capabilities / AgentDefinedContextType)

A Skill uses `SKILL.md` and supporting files to describe a task’s steps, constraints, and resources. An agent reads the Skill and carries out the task using its own tools. Execution experience can be stored separately as memory.

### Characteristics

- **Task instructions**: Steps and constraints for completing a type of work
- **Maintainable**: Skill content can be updated; execution experience is stored separately
- **Read on demand**: The agent selects skills for its current task

### Storage Location

```
viking://~/skills/{skill-name}/     # Default storage path
├── .abstract.md          # L0: Short description
├── .overview.md          # L1: Directory overview (after generation)
├── SKILL.md              # L2: Skill definition
└── scripts               # L2: Supporting implementation

viking://agent/skills/{skill-name}/    # Override via -p/--parent-auto-create, public/shared (account global)
├── .abstract.md          # L0: Short description
├── .overview.md          # L1: Directory overview (after generation)
├── SKILL.md              # L2: Skill definition
└── scripts               # L2: Supporting implementation
```

### AgentDefinedContextType Subtypes

The table below lists the design categories for shared capabilities. Skills are supported and installed in the user-private directory by default, with an option to use `viking://agent/skills/`. The other categories are planned and do not imply available APIs:

| Subtype | Location | Description |
|---------|----------|-------------|
| **Skill** | `agent/skills/` | Traditional workflow definitions, such as search and code generation |
| **Endpoint** | `agent/endpoints/` | Communication endpoint configuration (a2a, anp, etc.) (planned) |
| **Tool** | `agent/tools/` | Tool configuration (mcp, etc.) (planned) |
| **Payment** | `agent/payments/` | Payment capability configuration (ap2, etc.) (planned) |

### Usage

```python
# Add skill (defaults to viking://~/skills/)
client.add_skill(
    data={
        "name": "search-web",
        "description": "Search the web for information",
        "content": "# search-web\n...",
    },
    wait=True,
    timeout=300,
)

# Search user skills
results = client.find(
    query="web search",
    target_uri="viking://~/skills/"
)

# Search global agent skills
results = client.find(
    query="web search",
    target_uri="viking://agent/skills/",
)
```

Install into the account-shared skills directory with the CLI (requires write access to that path):

```bash
ov skills add ./skills/search-web -p viking://agent/skills
```

## Unified Search

A single retrieval can return resources, memories, and skills within its search scope. The default includes the current user's space and shared resources; add `viking://agent/skills` explicitly to include shared Skills:

```python
# Search across all context types
results = client.find(
    query="user authentication",
    target_uri=["viking://~", "viking://resources", "viking://agent/skills"],
)

for context in results.get("memories", []):
    print(f"Memory: {context['uri']}")
for context in results.get("resources", []):
    print(f"Resource: {context['uri']}")
for context in results.get("skills", []):
    print(f"Skill: {context['uri']}")
```

Close the client when these operations are finished:

```python
client.close()
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - URI specification
- [Session Management](./08-session.md) - Memory extraction mechanism
