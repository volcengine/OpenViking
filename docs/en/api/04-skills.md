# Skills

Skills are callable capabilities that agents can invoke. This guide covers how to add and manage skills.

## API Reference

### add_skill()

Add a skill to the knowledge base.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| data | Any | Yes | - | Skill data (dict, string, or path) |
| wait | bool | No | False | Wait for vectorization to complete |
| timeout | float | No | None | Timeout in seconds |

**Supported Data Formats**

1. **Dict (Skill format)**:
```python
{
    "name": "skill-name",
    "description": "Skill description",
    "content": "Full markdown content",
    "allowed_tools": ["Tool1", "Tool2"],  # optional
    "tags": ["tag1", "tag2"]  # optional
}
```

2. **Dict (MCP Tool format)** - Auto-detected and converted:
```python
{
    "name": "tool_name",
    "description": "Tool description",
    "inputSchema": {
        "type": "object",
        "properties": {...},
        "required": [...]
    }
}
```

3. **String (SKILL.md content)**:
```python
"""---
name: skill-name
description: Skill description
---

# Skill Content
"""
```

4. **Path (file or directory)**:
   - Single file: Path to `SKILL.md` file
   - Directory: Path to directory containing `SKILL.md` (auxiliary files included)

**Python SDK (Embedded / HTTP)**

```python
skill = {
    "name": "search-web",
    "description": "Search the web for current information",
    "content": """
# search-web

Search the web for current information.

## Parameters
- **query** (string, required): Search query
- **limit** (integer, optional): Max results, default 10
"""
}

result = client.add_skill(skill)
print(f"Added: {result['uri']}")
```

**HTTP API**

```
POST /api/v1/skills
```

```bash
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "search-web",
      "description": "Search the web for current information",
      "content": "# search-web\n\nSearch the web for current information.\n\n## Parameters\n- **query** (string, required): Search query\n- **limit** (integer, optional): Max results, default 10"
    }
  }'
```

**CLI**

```bash
openviking add-skill ./my-skill/ [--wait]
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "uri": "viking://agent/skills/search-web/",
    "name": "search-web",
    "auxiliary_files": 0
  },
  "time": 0.1
}
```

**Example: Add from MCP Tool**

**Python SDK (Embedded / HTTP)**

```python
# MCP tool format is auto-detected and converted
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
```

**HTTP API**

```bash
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
```

**Example: Add from SKILL.md File**

**Python SDK (Embedded / HTTP)**

```python
# Add from file path
result = client.add_skill("./skills/search-web/SKILL.md")
print(f"Added: {result['uri']}")

# Add from directory (includes auxiliary files)
result = client.add_skill("./skills/code-runner/")
print(f"Added: {result['uri']}")
print(f"Auxiliary files: {result['auxiliary_files']}")
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": "./skills/search-web/SKILL.md"
  }'
```

---

## SKILL.md Format

Skills can be defined using SKILL.md files with YAML frontmatter.

**Structure**

```markdown
---
name: skill-name
description: Brief description of the skill
allowed-tools:
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
| allowed-tools | List[str] | Tools this skill can use |
| tags | List[str] | Tags for categorization |

---

## Managing Skills

### List Skills

**Python SDK (Embedded / HTTP)**

```python
# List all skills
skills = client.ls("viking://agent/skills/")
for skill in skills:
    print(f"{skill['name']}")

# Simple list (names only)
names = client.ls("viking://agent/skills/", simple=True)
print(names)
```

**HTTP API**

```bash
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://agent/skills/" \
  -H "X-API-Key: your-key"
```

### Read Skill Content

**Python SDK (Embedded / HTTP)**

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

**Python SDK (Embedded / HTTP)**

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

### Remove Skills

**Python SDK (Embedded / HTTP)**

```python
client.rm("viking://agent/skills/old-skill/", recursive=True)
```

**HTTP API**

```bash
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://agent/skills/old-skill/&recursive=true" \
  -H "X-API-Key: your-key"
```

---

## MCP Conversion

OpenViking automatically detects and converts MCP tool definitions to skill format.

**Detection**

A dict is treated as MCP format if it contains an `inputSchema` field:

```python
if "inputSchema" in data:
    # Convert to skill format
    skill = mcp_to_skill(data)
```

**Conversion Process**

1. Name is converted to kebab-case
2. Description is preserved
3. Parameters are extracted from `inputSchema.properties`
4. Required fields are marked from `inputSchema.required`
5. Markdown content is generated

**Example Conversion**

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

---

## Skill Storage Structure

Skills are stored at `viking://agent/skills/`:

```
viking://agent/skills/
+-- search-web/
|   +-- .abstract.md      # L0: Brief description
|   +-- .overview.md      # L1: Parameters and usage
|   +-- SKILL.md          # L2: Full documentation
|   +-- [auxiliary files]  # Any additional files
+-- calculator/
|   +-- .abstract.md
|   +-- .overview.md
|   +-- SKILL.md
+-- ...
```

---

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
- `search-web` (good)
- `searchWeb` (avoid)
- `search_web` (avoid)

---

## Related Documentation

- [Context Types](../concepts/02-context-types.md) - Skill concept
- [Retrieval](06-retrieval.md) - Finding skills
- [Sessions](05-sessions.md) - Tracking skill usage
