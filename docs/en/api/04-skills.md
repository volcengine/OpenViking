# Skills

Skills are callable capabilities that agents can invoke. This module provides skill addition and management functionality.

## Core Concepts

### Skill Types

OpenViking supports multiple skill definition formats:

1. **Structured skill data**: Dictionary with name, description, content, etc.
2. **SKILL.md files**: Markdown files with YAML frontmatter
3. **MCP Tool format**: Automatically detected and converted to OpenViking skill format

### Skill Storage Structure

Skills are stored at `viking://agent/skills/`:

```
viking://agent/skills/
+-- search-web/
|   +-- .abstract.md      # L0: Brief description
|   +-- .overview.md      # L1: Parameters and usage overview
|   +-- SKILL.md          # L2: Full documentation
|   +-- [auxiliary files]  # Any additional files
+-- calculator/
|   +-- .abstract.md
|   +-- .overview.md
|   +-- SKILL.md
+-- ...
```

### SKILL.md Format

Skills can be defined using SKILL.md files with YAML frontmatter:

```markdown
---
name: skill-name
description: Brief description of the skill
allowed_tools:
  - Tool1
  - Tool2
tags:
  - tag1
  - tag2
---

# Skill Name

Full skill documentation in Markdown format.

## Parameters
- **param1** (type, required): Description
- **param2** (type, optional): Description

## Usage
When and how to use this skill.

## Examples
Concrete examples of skill invocation.
```

**Required Fields**

| Field | Type | Description |
|-------|------|-------------|
| name | str | Skill name (kebab-case recommended) |
| description | str | Brief description |

**Optional Fields**

| Field | Type | Description |
|-------|------|-------------|
| allowed_tools | List[str] | Tools this skill can use |
| tags | List[str] | Tags for categorization |

### MCP Format Automatic Conversion

OpenViking automatically detects and converts MCP tool definitions to skill format.

**Detection Rule**: A dictionary is treated as MCP format if it contains an `inputSchema` field.

**Conversion Process**:
1. Name is converted to kebab-case
2. Description is preserved
3. Parameters are extracted from `inputSchema.properties`
4. Required fields are marked from `inputSchema.required`
5. Markdown content is generated

**Conversion Example**:

Input (MCP format):
```python
{
    "name": "search_web",
    "description": "Search the web",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query"
            },
            "limit": {
                "type": "integer",
                "description": "Max results"
            }
        },
        "required": ["query"]
    }
}
```

Output (Skill format):
```python
{
    "name": "search-web",
    "description": "Search the web",
    "content": """---
name: search-web
description: Search the web
---

# search-web

Search the web

## Parameters

- **query** (string) (required): Search query
- **limit** (integer) (optional): Max results

## Usage

This tool wraps the MCP tool `search-web`. Call this when the user needs functionality matching the description above.
"""
}
```

## API Reference

### add_skill

Add a skill to the knowledge base.

#### 1. API Implementation Overview

Skills are a special type of resource that define actions or tools agents can perform.

**Processing Flow**:
1. Receive skill data or uploaded temporary file
2. Detect data format (structured data, SKILL.md content, MCP format)
3. Parse skill definition
4. Store to `viking://agent/skills/` path
5. If `wait=true`, wait for vectorization to complete

**Code Entry Points**:
- `openviking/client/local.py:LocalClient.add_skill` - SDK entry point (embedded)
- `openviking_cli/client/http.py:AsyncHTTPClient.add_skill` - SDK entry point (HTTP)
- `openviking/server/routers/resources.py:add_skill` - HTTP router
- `openviking/service/resource_service.py:ResourceService.add_skill` - Core service implementation
- `crates/ov_cli/src/handlers.rs:handle_add_skill` - CLI handler

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| data | Any | No | - | Inline skill content or structured data. Mutually exclusive with `temp_file_id` |
| temp_file_id | str | No | - | Temporary upload file ID (from `temp_upload`). Mutually exclusive with `data` |
| wait | bool | No | False | Wait for skill processing to complete |
| timeout | float | No | None | Timeout in seconds, only effective when `wait=true` |
| telemetry | TelemetryRequest | No | False | Whether to return telemetry data |

**Additional Notes**:
- **Local file handling**:
  - Python SDK and CLI accept local `SKILL.md` files or directories directly. In HTTP mode they automatically upload before calling the server API.
  - Raw HTTP callers should either:
    - Send structured skill data directly in `data`
    - Send raw `SKILL.md` content in `data`
    - First call `POST /api/v1/resources/temp_upload` to upload a local `SKILL.md` file/zip directory, then call `POST /api/v1/skills` with `temp_file_id`
  - `POST /api/v1/skills` does not accept direct host filesystem paths in `data`.

- **Supported data formats**:
  1. **Dict (Skill format)**: Includes `name`, `description`, `content`, etc.
  2. **Dict (MCP Tool format)**: Includes `name`, `description`, `inputSchema`, auto-detected and converted
  3. **String (SKILL.md content)**: Complete SKILL.md content
  4. **Path (file or directory)**: Path to `SKILL.md` file, or directory containing `SKILL.md` (auxiliary files included)

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/skills
Content-Type: application/json
```

```bash
# Using inline structured data
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "search-web",
      "description": "Search the web for current information",
      "content": "# search-web\n\nSearch the web for current information.\n\n## Parameters\n- **query** (string, required): Search query\n- **limit** (integer, optional): Max results, default 10"
    },
    "wait": true
  }'

# Using inline SKILL.md content
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": "---\nname: my-skill\ndescription: My custom skill\n---\n\n# My Skill\n\nSkill content here."
  }'

# Using MCP Tool format (auto-detected and converted
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "calculator",
      "description": "Perform mathematical calculations",
      "inputSchema": {
        "type": "object",
        "properties": {
          "expression": {
            "type": "string",
            "description": "Mathematical expression to evaluate"
          }
        },
        "required": ["expression"]
      }
    }
  }'

# Using local file (first use temp_upload)
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F "file=@./skills/my-skill.json" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\"
  }"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Approach 1: Using structured skill data
skill = {
    "name": "search-web",
    "description": "Search the web for current information",
    "content": """# search-web

Search the web for current information.

## Parameters
- **query** (string, required): Search query
- **limit** (integer, optional): Max results, default 10
"""
}
result = client.add_skill(skill)
print(f"Added: {result['root_uri']}")

# Approach 2: Using MCP Tool format (auto-detected and converted
mcp_tool = {
    "name": "calculator",
    "description": "Perform mathematical calculations",
    "inputSchema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate"
            }
        },
        "required": ["expression"]
    }
}
result = client.add_skill(mcp_tool)
print(f"Added: {result['uri']}")

# Approach 3: Add from local SKILL.md file
result = client.add_skill("./skills/search-web/SKILL.md")
print(f"Added: {result['uri']}")

# Approach 4: Add from directory containing SKILL.md (auxiliary files included
result = client.add_skill("./skills/code-runner/")
print(f"Added: {result['uri']}")
print(f"Auxiliary files: {result['auxiliary_files']}")

# Wait for processing completion
result = client.add_skill("./skills/my-skill/", wait=True)
client.wait_processed()
```

**CLI**

```bash
# Add skill (from file or directory
ov add-skill ./skills/my-skill.json
ov add-skill ./skills/search-web/SKILL.md
ov add-skill ./skills/code-runner/

# Wait for processing completion
ov add-skill ./skills/my-skill/ --wait

# Use JSON output format
ov add-skill ./skills/my-skill/ -o json
```

**Response Examples**

**HTTP API response (JSON)**:
```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://agent/skills/my-skill/",
    "uri": "viking://agent/skills/my-skill/",
    "name": "my-skill",
    "auxiliary_files": 2,
    "queue_status": {
      "pending": 0,
      "processing": 0,
      "completed": 1
    }
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "time": 0.1
}
```

**CLI response (default table format)**:
```
Note: Skill is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status          success
root_uri        viking://agent/skills/my-skill
uri             viking://agent/skills/my-skill
name            my-skill
auxiliary_files 2
```

**CLI response (JSON format, using -o json)**:
```json
{
  "status": "success",
  "root_uri": "viking://agent/skills/my-skill",
  "uri": "viking://agent/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2
}
```

**Field Description**:

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Processing status: `success` or `error` |
| `root_uri` | string | Final URI of the skill in OpenViking (same as `uri`) |
| `uri` | string | Final URI of the skill in OpenViking (same as `root_uri`) |
| `name` | string | Skill name |
| `auxiliary_files` | number | Number of auxiliary files included with the skill |
| `queue_status` | object | (Optional, only when `wait=true`) Queue processing status with `pending`, `processing`, `completed` counts |

#### 4. Error Handling

**Synchronous Processing Errors**:

If skill parsing or processing fails synchronously, raw HTTP returns the standard error envelope with a non-2xx HTTP status code:

```json
{
  "status": "error",
  "error": {
    "code": "PROCESSING_ERROR",
    "message": "Skill parse error: invalid skill metadata"
  }
}
```

The Python HTTP SDK raises the corresponding mapped exception for this response.

## Skill Management Operations

### List Skills

**Python SDK**

```python
# List all skills
skills = client.ls("viking://agent/skills/")
for skill in skills:
    print(f"{skill['name']}")

# Simple list (names only
names = client.ls("viking://agent/skills/", simple=True)
print(names)
```

**HTTP API**

```bash
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://agent/skills/" \
  -H "X-API-Key: your-key"
```

### Read Skill Content

**Python SDK**

```python
uri = "viking://agent/skills/search-web/"

# L0: Brief description
abstract = client.abstract(uri)
print(f"Abstract: {abstract}")

# L1: Parameters and usage overview
overview = client.overview(uri)
print(f"Overview: {overview}")

# L2: Full skill documentation
content = client.read(uri)
print(f"Content: {content}")
```

**HTTP API**

```bash
# L0: Brief description
curl -X GET "http://localhost:1933/api/v1/content/abstract?uri=viking://agent/skills/search-web/" \
  -H "X-API-Key: your-key"

# L1: Parameters and usage overview
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://agent/skills/search-web/" \
  -H "X-API-Key: your-key"

# L2: Full skill documentation
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://agent/skills/search-web/" \
  -H "X-API-Key: your-key"
```

### Search Skills

**Python SDK**

```python
# Semantic search for skills
results = client.find(
    "search the internet",
    target_uri="viking://agent/skills/",
    limit=5
)

for ctx in results.skills:
    print(f"Skill: {ctx.uri}")
    print(f"Score: {ctx.score:.3f}")
    print(f"Description: {ctx.abstract}")
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "search the internet",
    "target_uri": "viking://agent/skills/",
    "limit": 5
  }'
```

### Delete Skills

**Python SDK**

```python
client.rm("viking://agent/skills/old-skill/", recursive=True)
```

**HTTP API**

```bash
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://agent/skills/old-skill/&recursive=true" \
  -H "X-API-Key: your-key"
```

## Best Practices

### Clear Descriptions

```python
# Good - specific and actionable
skill = {
    "name": "search-web",
    "description": "Search the web for current information using Google",
    ...
}

# Less helpful - too vague
skill = {
    "name": "search",
    "description": "Search",
    ...
}
```

### Comprehensive Content

Include in your skill content:
- Clear parameter descriptions with types
- When to use the skill
- Concrete examples
- Edge cases and limitations

### Consistent Naming

Use kebab-case for skill names:
- `search-web` (recommended)
- `searchWeb` (avoid)
- `search_web` (avoid)

## Related Documentation

- [Resource Management](02-resources.md) - Resource addition and management
- [File System](03-filesystem.md) - File and directory operations
- [Context Types](../concepts/02-context-types.md) - Skill concept
- [Retrieval](06-retrieval.md) - Finding skills
- [Sessions](05-sessions.md) - Tracking skill usage
