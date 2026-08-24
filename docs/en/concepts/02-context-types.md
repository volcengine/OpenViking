# Context Types

Based on a simplified mapping of human cognitive patterns and engineering considerations, OpenViking abstracts context into **three basic types: Resource, Memory, and Skill**, each serving different purposes in Agent applications.

## Overview

| Type | Purpose | Lifecycle | Initiative |
|------|---------|-----------|------------|
| **Resource** | Knowledge and rules | Long-term, relatively static | User adds |
| **Memory** | Agent's cognition | Long-term, dynamically updated | Agent records |
| **Skill** | Declarable agent capability configuration (AgentDefinedContextType) | Long-term, static | User or system adds |

## Resource

Resources are external knowledge that Agents can reference.

### Characteristics

- **User-driven**: Resource information actively added by users to supplement LLM knowledge, such as product manuals and code repositories
- **Static content**: Content rarely changes after addition, usually modified by users
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
session_info = await client.create_session()
session = client.session(session_id=session_info["session_id"])
await session.add_message(
    role="user",
    parts=[TextPart(text="I prefer dark mode")],
)
commit = await session.commit()  # Starts background memory extraction
task = await client.get_task(task_id=commit["task_id"])  # Poll until task["status"] == "completed"

# Search memories
results = await client.find(
    query="UI preferences",
    target_uri="viking://~/memories/"
)
```

## Skill (Capabilities / AgentDefinedContextType)

Skills are capabilities that Agents can invoke, belonging to the **AgentDefinedContextType** category. This includes traditional workflow definitions, communication endpoints, tool configurations, and payment capabilities. Their common characteristic is that they **define how an agent interacts with external systems**, with relatively static runtime definitions, but invocation experiences are updated in Memory.

### Characteristics

- **Defined capabilities**: Tool definitions for completing specific tasks
- **Relatively static**: Skill definitions don't change at runtime, but usage memories related to tools are updated in memory
- **Callable**: Agent decides when to use which skill

### Storage Location

```
viking://~/skills/{skill-name}/     # Default storage path
├── .abstract.md          # L0: Short description
├── .overview.md          # L1: Directory overview (after generation)
├── SKILL.md              # L2: Skill definition
└── scripts               # L2: Supporting implementation

viking://agent/skills/{skill-name}/    # Override via --uri, public/shared (account global)
├── .abstract.md          # L0: Short description
├── .overview.md          # L1: Directory overview (after generation)
├── SKILL.md              # L2: Skill definition
└── scripts               # L2: Supporting implementation
```

### AgentDefinedContextType Subtypes

AgentDefinedContextType includes the following subtypes, all stored under the `viking://agent/` scope:

| Subtype | Location | Description |
|---------|----------|-------------|
| **Skill** | `agent/skills/` | Traditional workflow definitions, such as search and code generation |
| **Endpoint** | `agent/endpoints/` | Communication endpoint configuration (a2a, anp, etc.) (planned) |
| **Tool** | `agent/tools/` | Tool configuration (mcp, etc.) (planned) |
| **Payment** | `agent/payments/` | Payment capability configuration (ap2, etc.) (planned) |

### Usage

```python
# Add skill (defaults to viking://~/skills/)
await client.add_skill(
    data={
        "name": "search-web",
        "description": "Search the web for information",
        "content": "# search-web\n...",
    },
)

# Write to global agent skills root (public/shared) via -p override
ov skills add search-web -p viking://agent/skills

# Search user skills
results = await client.find(
    query="web search",
    target_uri="viking://~/skills/"
)

# Search global agent skills
results = await client.find(
    query="web search",
    target_uri="viking://agent/skills/",
)
```

## Unified Search

Based on Agent's needs, supports unified search across all three context types, providing comprehensive information:

```python
# Search across all context types
results = await client.find(query="user authentication")

for context in results.get("memories", []):
    print(f"Memory: {context['uri']}")
for context in results.get("resources", []):
    print(f"Resource: {context['uri']}")
for context in results.get("skills", []):
    print(f"Skill: {context['uri']}")
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - URI specification
- [Session Management](./08-session.md) - Memory extraction mechanism
