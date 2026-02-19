# Viking URI

Viking URI is the unified resource identifier for all content in OpenViking.

## Format

```
viking://{scope}/{path}
```

- **scheme**: Always `viking`
- **scope**: Top-level namespace (resources, user, agent, session, queue)
- **path**: Resource path within the scope

## Scopes

| Scope | Description | Lifecycle | Visibility |
|-------|-------------|-----------|------------|
| **resources** | Independent resources | Long-term | Global |
| **user** | User-level data | Long-term | Global |
| **agent** | Agent-level data | Long-term | Global |
| **session** | Session-level data | Session lifetime | Current session |
| **queue** | Processing queue | Temporary | Internal |
| **temp** | Temporary files | During parsing | Internal |

## Initial Directory Structure

Moving away from traditional flat database thinking, all context is organized as a filesystem. Agents no longer just find data through vector search, but can locate and browse data through deterministic paths and standard filesystem commands. Each context or directory is assigned a unique URI identifier string in the format viking://{scope}/{path}, allowing the system to precisely locate and access resources stored in different locations.

```
viking://
├── session/{session_id}/
│   ├── .abstract.md          # L0: One-line session summary
│   ├── .overview.md          # L1: Session overview
│   ├── .meta.json            # Session metadata
│   ├── messages.json         # Structured message storage
│   ├── checkpoints/          # Version snapshots
│   ├── summaries/            # Compression summary history
│   └── .relations.json       # Relations table
│
├── user/
│   ├── .abstract.md          # L0: Content summary
│   ├── .overview.md          # User profile
│   └── memories/             # User memory storage
│       ├── .overview.md      # Memory overview
│       ├── preferences/      # User preferences
│       ├── entities/         # Entity memories
│       └── events/           # Event records
│
├── agent/
│   ├── .abstract.md          # L0: Content summary
│   ├── .overview.md          # Agent overview
│   ├── memories/             # Agent learning memories
│   │   ├── .overview.md
│   │   ├── cases/            # Cases
│   │   └── patterns/         # Patterns
│   ├── instructions/         # Agent instructions
│   └── skills/               # Skills directory
│
└── resources/{project}/      # Resource workspace
```

## URI Examples

### Resources

```
viking://resources/                           # All resources
viking://resources/my-project/                # Project root
viking://resources/my-project/docs/           # Docs directory
viking://resources/my-project/docs/api.md     # Specific file
```

### User Data

```
viking://user/                                # User root
viking://user/memories/                       # All user memories
viking://user/memories/preferences/           # User preferences
viking://user/memories/preferences/coding     # Specific preference
viking://user/memories/entities/              # Entity memories
viking://user/memories/events/                # Event memories
```

### Agent Data

```
viking://agent/                               # Agent root
viking://agent/skills/                        # All skills
viking://agent/skills/search-web              # Specific skill
viking://agent/memories/                      # Agent memories
viking://agent/memories/cases/                # Learned cases
viking://agent/memories/patterns/             # Learned patterns
viking://agent/instructions/                  # Agent instructions
```

### Session Data

```
viking://session/{session_id}/                # Session root
viking://session/{session_id}/messages/       # Session messages
viking://session/{session_id}/tools/          # Tool executions
viking://session/{session_id}/history/        # Archived history
```

## Directory Structure

```
viking://
├── resources/       # Independent resources
│   └── {project}/
│       ├── .abstract.md
│       ├── .overview.md
│       └── {files...}
│
├── user/{user_id}/
│   ├── profile.md                # User basic info
│   └── memories/
│       ├── preferences/          # By topic
│       ├── entities/             # Each independent
│       └── events/               # Each independent
│
├── agent/{unique_space_name}/    # unique_space_name see UserIdentifier
│   ├── skills/                   # Skill definitions
│   ├── memories/
│   │   ├── cases/
│   │   └── patterns/
│   ├── workspaces/
│   └── instructions/
│
└── session/{unique_space_name}/{session_id}/
    ├── messages/
    ├── tools/
    └── history/
```

## URI Operations

### Parsing

```python
from openviking_cli.utils.uri import VikingURI

uri = VikingURI("viking://resources/docs/api")
print(uri.scope)      # "resources"
print(uri.full_path)  # "resources/docs/api"
```

### Building

```python
# Join paths
base = "viking://resources/docs/"
full = VikingURI(base).join("api.md").uri  # viking://resources/docs/api.md

# Parent directory
uri = "viking://resources/docs/api.md"
parent = VikingURI(uri).parent.uri  # viking://resources/docs
```

## API Usage

### Targeting Specific Scopes

```python
# Search only in resources
results = client.find(
    "authentication",
    target_uri="viking://resources/"
)

# Search only in user memories
results = client.find(
    "coding preferences",
    target_uri="viking://user/memories/"
)

# Search only in skills
results = client.find(
    "web search",
    target_uri="viking://agent/skills/"
)
```

### File System Operations

```python
# List directory
entries = await client.ls("viking://resources/")

# Read file
content = await client.read("viking://resources/docs/api.md")

# Get abstract
abstract = await client.abstract("viking://resources/docs/")

# Get overview
overview = await client.overview("viking://resources/docs/")
```

## Special Files

Each directory may contain special files:

| File | Purpose |
|------|---------|
| `.abstract.md` | L0 abstract (~100 tokens) |
| `.overview.md` | L1 overview (~2k tokens) |
| `.relations.json` | Related resources |
| `.meta.json` | Metadata |

## Best Practices

### Use Trailing Slash for Directories

```python
# Directory
"viking://resources/docs/"

# File
"viking://resources/docs/api.md"
```

### Scope-Specific Operations

```python
# Add resources only to resources scope
await client.add_resource(url, target="viking://resources/project/")

# Skills go to agent scope
await client.add_skill(skill)  # Automatically to viking://agent/skills/
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Three types of context
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Storage Architecture](./05-storage.md) - VikingFS and AGFS
- [Session Management](./08-session.md) - Session storage structure
