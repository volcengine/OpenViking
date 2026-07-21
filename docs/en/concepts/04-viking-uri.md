# Viking URI

Viking URI is the unified resource identifier for all content in OpenViking.

## Format

```
viking://{scope}/{path}
```

- **scheme**: Always `viking`
- **scope**: Top-level namespace (`resources`, `user`, `agent`; `temp`, `queue`, and `upload` are internal)
- **path**: Resource path within the scope

## Scopes

| Scope | Description | Lifecycle | Visibility |
|-------|-------------|-----------|------------|
| **resources** | Independent resources / objective knowledge | Long-term | Account global |
| **user** | User-level data, including sessions | Long-term / session lifetime | Current user |
| **agent** | Agent capabilities and configuration (skills, endpoints, tools, payments, etc.) | Long-term | Account global |
| **queue** | Processing queue | Temporary | Internal |
| **temp** | Temporary files | During parsing | Internal |
| **upload** | Temporary upload files | Temporary | Internal |

Public API and CLI filesystem/content operations accept the public scopes
`resources`, `user`, and `agent`, plus the root URI `viking://`. `session` is retained
as a backward-compatible alias for user session paths; new session data lives
under `viking://user/{user_id}/sessions`.
`temp`, `queue`, and `upload` are internal implementation
scopes and cannot be addressed directly through public API URI parameters.

## Initial Directory Structure

Moving away from traditional flat database thinking, all context is organized as a filesystem. Agents no longer just find data through vector search, but can locate and browse data through deterministic paths and standard filesystem commands. Each context or directory is assigned a unique URI identifier string in the format viking://{scope}/{path}, allowing the system to precisely locate and access resources stored in different locations.

```
viking://
├── user/
│   └── {user_id}/
│       ├── profile.md        # User profile
│       ├── memories/         # User memory storage
│       ├── resources/        # User-owned private resources
│       ├── skills/           # User skills
│       ├── peers/
│       │   └── {peer_id}/
│       │       ├── memories/  # Memory about a specific interaction peer
│       │       └── resources/ # Resources scoped to that peer
│       └── sessions/         # User session storage
│           └── {session_id}/
│               ├── .abstract.md
│               ├── .overview.md
│               ├── .meta.json
│               ├── messages.jsonl
│               ├── tools/
│               └── history/
│
├── agent/                     # Agent capabilities and configuration (global)
│   ├── skills/                # Skill definitions
│   ├── endpoints/             # Communication endpoints (a2a, anp, etc.) (planned)
│   ├── tools/                 # Tool configuration (mcp, etc.) (planned)
│   └── payments/              # Payment configuration (ap2, etc.) (planned)
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
viking://user/resources/                      # Current user's resources
viking://user/resources/docs/                 # Current user's resource directory
```

### User Skills and Peer Content

```
viking://user/skills/                         # Current user's skills
viking://user/skills/search-web               # Specific skill
viking://user/memories/                       # Current user's memories
viking://user/memories/cases/                 # Task cases used for training and evaluation
viking://user/memories/trajectories/          # Reusable task-execution trajectories
viking://user/memories/experiences/           # Experience distilled from execution outcomes
viking://user/{user_id}/peers/{peer_id}/memories/
viking://user/{user_id}/peers/{peer_id}/resources/
```

### Agent Capabilities and Configuration

```
viking://agent/skills/search-web                    # A specific skill definition
viking://agent/skills/                              # All skill definitions
viking://agent/endpoints/                           # Communication endpoints (a2a, anp, etc.) (planned)
viking://agent/tools/mcp/                           # MCP tool configuration (planned)
viking://agent/payments/ap2/                        # Payment configuration (planned)
```

`viking://agent/...` is a global shared scope, accessible to all users under the account,
without agent_id isolation. Legacy (0.3.x) data under `viking://agent/...` remains accessible
via a read-only compatibility entry, but new data should be written according to the new directory semantics.

The short `viking://user/...` form is relative to the current request identity.
OpenViking expands it internally to explicit namespace paths such as
`viking://user/{user_id}/...` before storage and retrieval.
Identity path segments such as `{user_id}` and `{peer_id}` must be safe single
segments, for example `alice` or `web-visitor-alice`.

### Session Data

```
viking://user/{user_id}/sessions/{session_id}/          # Session root
viking://user/{user_id}/sessions/{session_id}/messages  # Session messages
viking://user/{user_id}/sessions/{session_id}/tools     # Tool executions
viking://user/{user_id}/sessions/{session_id}/history   # Archived history
viking://user/sessions/{session_id}/                    # Current-user short form
```

`viking://session/{session_id}` is accepted as a backward-compatible alias for
the current user's session path. It is not a separate storage root for new
session data.

## Path Variables

Viking URI supports path variables for dynamic path generation. This is especially useful for organizing time-series data like emails, logs, daily reports, etc.

### Variable Syntax

```
{namespace:key}
```

- **namespace**: Variable provider namespace (e.g., `calendar`, `env`, `user`)
- **key**: Variable name within the namespace

### Calendar Variables

The `calendar` namespace provides date-related variables:

| Variable | Description | Example (2026-05-07) |
|----------|-------------|----------------------|
| `{calendar:today}` | Full date path | `2026/05/07` |
| `{calendar:yesterday}` | Yesterday's date path | `2026/05/06` |
| `{calendar:tomorrow}` | Tomorrow's date path | `2026/05/08` |
| `{calendar:year}` | Year | `2026` |
| `{calendar:month}` | Month with leading zero | `05` |
| `{calendar:day}` | Day with leading zero | `07` |
| `{calendar:ym}` | Year/month | `2026/05` |
| `{calendar:quarter}` | Quarter (Q1-Q4) | `Q2` |
| `{calendar:yq}` | Year/quarter | `2026/Q2` |
| `{calendar:week}` | ISO week number with leading zero | `18` |
| `{calendar:yw}` | Year/ISO week | `2026/w18` |

### Usage Examples

```python
# Organize emails by date
viking://resources/emails/{calendar:today}/inbox
# Renders to: viking://resources/emails/2026/05/07/inbox

# View yesterday's logs
viking://resources/logs/{calendar:yesterday}/app.log
# Renders to: viking://resources/logs/2026/05/06/app.log

# Pre-upload tomorrow's tasks
viking://resources/tasks/{calendar:tomorrow}/todo.md
# Renders to: viking://resources/tasks/2026/05/08/todo.md

# Monthly logs
viking://resources/logs/{calendar:year}/{calendar:month}/app.log
# Renders to: viking://resources/logs/2026/05/app.log

# Daily snapshots
viking://resources/snapshots/{calendar:today}/
# Renders to: viking://resources/snapshots/2026/05/07/
```

### Resolution

Path variables are resolved **server-side** at the time of API execution. The CLI/SDK passes the URI template as-is, and the server renders it to a concrete path based on the current context (time, authenticated user, etc.).

### Use with CLI

```bash
# Add today's emails, --parent-auto-create can be shortened to -p
ov add-resource --parent-auto-create "viking://resources/emails/{calendar:today}/inbox" ./emails/*.eml

# Read yesterday's log
ov read "viking://resources/logs/{calendar:yesterday}/app.log"

# Prep tomorrow's tasks
ov write --uri "viking://resources/tasks/{calendar:tomorrow}/todo.md" --content "Plan the day"

# Upload monthly report, --parent-auto-create can be shortened to -p
ov add-resource --parent-auto-create "viking://resources/reports/{calendar:ym}" ./report.pdf
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
│   ├── memories/
│   │   ├── preferences/          # By topic
│   │   ├── entities/             # Each independent
│   │   └── events/               # Each independent
│   ├── resources/
│   │   └── {project}/
│   ├── skills/
│   └── peers/{peer_id}/
│       ├── memories/
│       └── resources/
│
├── agent/                        # Agent capabilities and configuration (account global)
│   ├── skills/                   # Skill definitions
│   ├── endpoints/                # Communication endpoints (a2a, anp, etc.) (planned)
│   ├── tools/                    # Tool configuration (mcp, etc.) (planned)
│   └── payments/                 # Payment configuration (ap2, etc.) (planned)
│
└── user/{user_id}/sessions/{session_id}/
    ├── messages.jsonl
    ├── tools/
    └── history/
```

`viking://agent/...` is a global shared scope for agent capabilities, accessible to all users under the account,
without agent_id isolation. Legacy (0.3.x) data under `viking://agent/...` remains accessible
via a read-only compatibility entry, but new data should be written according to the new directory semantics.

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

# Search only in current-user resources
results = client.find(
    "private project notes",
    target_uri="viking://user/resources/"
)

# Search only in user memories
results = client.find(
    "coding preferences",
    target_uri="viking://user/memories/"
)

# Search only in user skills
results = client.find(
    "web search",
    target_uri="viking://user/skills/"
)

# Search only in global agent skills
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
# Add resources to the shared account resource scope
await client.add_resource(url, to="viking://resources/project/")

# Add private resources to the current user's resource root
await client.add_resource(path, parent="viking://user/resources/project/")

# Skills are added to the current user's skills root by default
await client.add_skill(skill)  # canonical root: viking://user/skills/

# Write to the global agent skills root (public/shared) via -p override
ov skills add xxx -p viking://agent/skills/
```

### Resources Scope Constraint

The `resources` scope is for objective knowledge only (documents, code, specifications, papers, etc.).
Storing non-knowledge data in `viking://resources/` is prohibited, including but not limited to:
tool configurations, communication endpoint definitions, payment configurations, skill definitions, etc.
Such data should use the `viking://agent/` scope.

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Three types of context
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Storage Architecture](./05-storage.md) - VikingFS and AGFS
- [Session Management](./08-session.md) - Session storage structure
