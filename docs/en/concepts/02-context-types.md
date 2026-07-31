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
    "https://docs.example.com/api.pdf",
    reason="API documentation"
)

# Search resources
results = client.find(
    "authentication methods",
    target_uri="viking://resources/"
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
| **profile** | `user/memories/profile.md` | Basic user information |
| **preferences** | `user/memories/preferences/` | User preferences organized by topic |
| **entities** | `user/memories/entities/` | Knowledge about people, projects, organizations, and other entities |
| **events** | `user/memories/events/` | Decisions, milestones, and other event records |
| **identity** | `user/memories/identity.md` | Assistant name, persona, temperament, and self-introduction |
| **soul** | `user/memories/soul.md` | Assistant principles, boundaries, style, and continuity |
| **cases** | `user/memories/cases/` | Task cases used for training and evaluation |
| **trajectories** | `user/memories/trajectories/` | Reusable task-execution trajectories |
| **experiences** | `user/memories/experiences/` | Reusable experience distilled from execution outcomes |
| **tools** | `user/memories/tools/` | Tool usage knowledge and best practices |
| **skills** | `user/memories/skills/` | Skill-execution knowledge and workflow strategies |

The `user/...` entries above are current-user short paths. The server resolves them to `viking://user/{user_id}/...`. When the memory policy permits Peer memory, supported types may instead be written under `viking://user/{user_id}/peers/{peer_id}/memories/...`. Applications can extend or adjust memory types with custom templates.

### Usage

```python
# Memories are auto-extracted from sessions
session = client.session()
await session.add_message("user", [{"type": "text", "text": "I prefer dark mode"}])
commit = await session.commit()  # Starts background memory extraction
task = await client.get_task(commit["task_id"])  # Poll until task["status"] == "completed"

# Search memories
results = await client.find(
    "UI preferences",
    target_uri="viking://user/memories/"
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
viking://user/skills/{skill-name}/     # Default storage path
├── .abstract.md          # L0: Short description
├── SKILL.md              # L1: Detailed overview
└── scripts               # L2: Full definition

viking://agent/skills/{skill-name}/    # Override via --uri, public/shared (account global)
├── .abstract.md          # L0: Short description
├── SKILL.md              # L1: Detailed overview
└── scripts               # L2: Full definition
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
# Add skill (defaults to viking://user/skills/)
await client.add_skill({
    "name": "search-web",
    "description": "Search the web for information",
    "content": "# search-web\n..."
})

# Write to global agent skills root (public/shared) via -p override
ov skills add search-web -p viking://agent/skills

# Search user skills
results = await client.find(
    "web search",
    target_uri="viking://user/skills/"
)

# Search global agent skills
results = await client.find(
    "web search",
    target_uri="viking://agent/skills/"
)
```

## Unified Search

Based on Agent's needs, supports unified search across all three context types, providing comprehensive information:

```python
# Search across all context types
results = await client.find("user authentication")

for ctx in results.memories:
    print(f"Memory: {ctx.uri}")
for ctx in results.resources:
    print(f"Resource: {ctx.uri}")
for ctx in results.skills:
    print(f"Skill: {ctx.uri}")
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - URI specification
- [Session Management](./08-session.md) - Memory extraction mechanism
