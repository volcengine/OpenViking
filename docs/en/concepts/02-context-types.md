# Context Types

Based on a simplified mapping of human cognitive patterns and engineering considerations, OpenViking abstracts context into **three basic types: Resource, Memory, and Skill**, each serving different purposes in Agent applications.

## Overview

| Type | Purpose | Lifecycle | Initiative |
|------|---------|-----------|------------|
| **Resource** | Knowledge and rules | Long-term, relatively static | User adds |
| **Memory** | Agent's cognition | Long-term, dynamically updated | Agent records |
| **Skill** | Callable capabilities | Long-term, static | Agent invokes |

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

Memories are divided into user memories and Agent memories, representing learned knowledge about users and the world.

### Characteristics

- **Agent-driven**: Memory information actively extracted and recorded by Agent
- **Dynamic updates**: Continuously updated from interactions by Agent
- **Personalized**: Learned for specific users or specific Agents

### 6 Categories

| Category | Location | Description | Update Strategy |
|----------|----------|-------------|-----------------|
| **profile** | `user/memories/.overview.md` | User basic info | ✅ Appendable |
| **preferences** | `user/memories/preferences/` | User preferences by topic | ✅ Appendable |
| **entities** | `user/memories/entities/` | Entity memories (people, projects) | ✅ Appendable |
| **events** | `user/memories/events/` | Event records (decisions, milestones) | ❌ No update |
| **cases** | `agent/memories/cases/` | Learned cases | ❌ No update |
| **patterns** | `agent/memories/patterns/` | Learned patterns | ❌ No update |

### Usage

```python
# Memories are auto-extracted from sessions
session = client.session()
await session.add_message("user", [{"type": "text", "text": "I prefer dark mode"}])
await session.commit()  # Extracts preference memory

# Search memories
results = await client.find(
    "UI preferences",
    target_uri="viking://user/memories/"
)
```

## Skill

Skills are capabilities that Agents can invoke, such as current Skills, MCP, etc.

### Characteristics

- **Defined capabilities**: Tool definitions for completing specific tasks
- **Relatively static**: Skill definitions don't change at runtime, but usage memories related to tools are updated in memory
- **Callable**: Agent decides when to use which skill

### Storage Location

```
viking://agent/skills/{skill-name}/
├── .abstract.md          # L0: Short description
├── SKILL.md              # L1: Detailed overview
└── scripts               # L2: Full definition
```

### Usage

```python
# Add skill
await client.add_skill({
    "name": "search-web",
    "description": "Search the web for information",
    "content": "# search-web\n..."
})

# Search skills
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
