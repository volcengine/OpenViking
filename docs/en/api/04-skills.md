# Skills

Skills are task instructions and supporting resources for agents to read. OpenViking stores, retrieves, and manages them; the consuming Agent/Harness handles activation, tool policies, and execution. This page follows the HTTP API, SDKs, and `ov` CLI in the current repository.

## Core Concepts

### Skill Types

OpenViking supports multiple skill definition formats:

1. **Structured skill data**: Dictionary with name, description, content, etc.
2. **SKILL.md files**: Markdown files with YAML frontmatter
3. **MCP Tool format**: Converted into Skill documentation; does not connect to an MCP server or register executable tools
4. **Skill packages and collections**: Directories or ZIPs containing `SKILL.md` and auxiliary files, or Git repository / GitHub tree URLs

### Skill Storage Structure

Skills support the current user's private root `viking://user/{user_id}/skills/` and the account-shared root `viking://agent/skills/`. The add target resolves in this order: request `target_uri` → user `add_targets.skill_uri` → server `user_config_defaults.add_targets.skill_uri` → current user's private root. Access remains subject to the current identity and permissions.

The home alias `viking://~/skills/` always resolves to the authenticated user's private root, independently of the configured add target. The uid-less `viking://user/skills/` and peer-scoped skills are unsupported. The private package layout below also applies under the shared root:

```
viking://user/{user_id}/skills/
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

### Package Processing and Indexing

New Skills process the entire package by default, without an additional flag. Root L0 still comes from Skill metadata, and root L1 still uses only `SKILL.md`. Subdirectories use resource directory summaries. Directory L0/L1 and supported L2 files are indexed; nested `SKILL.md` files remain ordinary attachments.

Auxiliary files now enter the existing summary and embedding pipeline, subject to the current hidden-file filters, input limits, and media settings. Images use supported image or text inputs; audio and video use text summaries with the existing filename fallback. `wait=true` waits for the whole package. Upgrades do not automatically rebuild existing Skills: use `semantic_and_vectors` to populate missing summaries; rebuilding vectors alone does not create them.

### SKILL.md Format

Skills can be defined using SKILL.md files with YAML frontmatter:

```markdown
---
name: skill-name
description: Brief description of the skill
allowed-tools: Read Bash(python3 *)
tags:
  - tag1
  - tag2
metadata:
  author: example-team
  vikingbot:
    requires:
      bins: [python3]
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
| name | str | Nonempty; at most 64 ASCII letters, digits, underscores, or hyphens; kebab-case recommended |
| description | str | Brief description |

**Optional Fields**

| Field | Type | Description |
|-------|------|-------------|
| allowed-tools | str / List[str] | Space-separated tool declarations or a compatible string list; parentheses can contain spaces; the consumer interprets and enforces policies |
| tags | List[str] | Tags for categorization |
| metadata | object | Preserved extensions such as `metadata.vikingbot.requires`; OpenViking does not install these dependencies |

Use hyphenated **`allowed-tools`** in `SKILL.md`; parsed structured data and API summaries use **`allowed_tools`**. Do not substitute the underscore spelling in frontmatter. An omitted declaration and an explicit empty declaration can have different Harness permissions; a summary with `allowed_tools: []` cannot distinguish them. Read the full definition before execution. See [VikingBot Skills](../../../bot/docs/en/concepts/06-skills.md) for instruction and metadata handling.

### MCP Format Automatic Conversion

OpenViking automatically detects and converts MCP tool definitions to skill format.

**Detection Rule**: A dictionary is treated as MCP format if it contains an `inputSchema` field.

**Conversion Process**:
1. Underscores in the name are replaced with hyphens (camelCase is not converted automatically)
2. Description is preserved
3. Parameters are extracted from `inputSchema.properties`
4. Required fields are marked from `inputSchema.required`
5. Markdown content is generated

**Conversion Example**:

Input (MCP format):
```json
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

Output (Python dictionary):
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
4. Store the package under the selected private or account-shared skills root
5. Return a `task_id` by default for tracking background vectorization

**Code Entry Points**:
- `sdk/python/openviking_sdk/client.py:AsyncHTTPClient.add_skill` - Python SDK entry point
- `openviking_cli/client/http.py` - Compatibility import forwarding to the Python SDK
- `openviking/server/routers/resources.py:add_skill` - HTTP router
- `openviking/service/resource_service.py:ResourceService.add_skill` - Core service implementation
- `openviking/server/routers/skills.py` - List, find, read, validate, update, and delete endpoints
- `crates/ov_cli/src/commands/skills.rs` - CLI Skill command handlers

#### 2. Interface and Parameters

**HTTP body parameters** (`POST /api/v1/skills`)

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| data | Any | No | - | Inline SKILL.md, structured/MCP data, or Git URL; choose this or `temp_file_id` |
| temp_file_id | str | No | - | Temporary upload file ID (from `temp_upload`); choose this or `data` |
| wait | bool | No | False | Wait for skill processing to complete |
| timeout | float | No | None | Timeout in seconds, only effective when `wait=true` |
| telemetry | TelemetryRequest | No | False | Whether to return telemetry data |
| target_uri | str | No | Target resolution order | Skills root, such as `viking://~/skills` or `viking://agent/skills` |
| skills | List[str] | No | `[]` | Select directory names from a directory/Git collection; empty or `["*"]` selects all |
| list_only | bool | No | False | Inspect source Skills without installing; requires a resolvable file, directory, or Git source, not an inline dictionary |
| source_metadata | object | No | Generated automatically | Source tracking saved in `.source.json`; distinct from frontmatter `metadata` |

Supply at least one of `data` or `temp_file_id`. The current server gives uploads precedence if both are present; clients should send only one to avoid ambiguity. Unknown body fields are rejected.

**Additional Notes**:
- **Local file handling**:
  - Python SDK and CLI accept local `SKILL.md` files or directories directly. In HTTP mode they automatically upload before calling the server API.
  - Raw HTTP callers should either:
    - Send structured skill data directly in `data`
    - Send raw `SKILL.md` content in `data`
    - Send a Git repository or GitHub tree URL for server-side source resolution
    - First call `POST /api/v1/resources/temp_upload` to upload a local `SKILL.md` or directory ZIP, then call `POST /api/v1/skills` with `temp_file_id`
  - `temp_upload` defaults to local temporary storage; pass `upload_mode=shared` only when you explicitly need distributed shared temporary uploads. Python HTTP clients can set `upload.mode = "shared"` in `ovcli.conf`; the Rust `ov` CLI instead uses `OPENVIKING_UPLOAD_MODE=shared`.
  - `POST /api/v1/skills` does not accept direct host filesystem paths in `data`.

- **Targeting**:
  - Add requests use `target_uri` for the skills root; `to`, `parent`, and `root_uri` are not HTTP request fields. CLI `-p/--parent-auto-create` maps to `target_uri`.
  - Peer-scoped skill roots are not supported; actor peer filtering only applies to peer memories/resources, not peer skills.
  - Use the home alias `viking://~/skills/...` to address your own skills when listing, reading, deleting, or searching. The uid-less `viking://user/skills/...` spelling returns an error with a corrective hint.

- **Supported data formats**:
  1. **Dict (Skill format)**: Includes `name`, `description`, `content`, etc.
  2. **Dict (MCP Tool format)**: Includes `name`, `description`, `inputSchema`, auto-detected and converted
  3. **String (SKILL.md content)**: Complete SKILL.md content
  4. **Path (automatically uploaded by SDK/CLI)**: SKILL.md file, Skill directory/collection, or ZIP; a single Markdown file does not include sibling resources
  5. **Git URL**: Repository or Skill subdirectory. Collection `skills` selectors use directory names, not frontmatter names. A discovered `SKILL.md` owns its subtree; nested `SKILL.md` files remain attachments rather than separate discovered Skills

Local `.json` files are not automatically decoded into structured Skills. Parse JSON and send the object in `data`, or use a frontmatter-based `SKILL.md` instead.

#### 3. Usage Examples

Single-Skill imports return a `task_id` by default. Query the [Task API](17-tasks.md) and search after the task reaches `completed`. With `wait=true`, the call waits for queue processing and returns `queue_status`. Multiple imports return an `installed` array with independent task information per result; the batch is not atomic. `list_only=true` returns source `skills` and `total` without creating import tasks.

HTTP fields and SDK parameters do not have identical names or nesting. Python `add_skill(data, wait=False, timeout=None, options=None)` / `update_skill(skill_name, data, ...)` take `target_uri` and `telemetry` inside `options`. Put fields such as `skills`, `list_only`, `source_metadata` (add), and `from_source` (update) in `options["extra"]`. TypeScript uses `targetUri` and `extra`; Go uses `TargetURI` and `Extra`. TypeScript local path uploads require Node.js.

```python
# Preview a local collection without writing to OpenViking
listing = client.add_skill(
    "./skills",
    options={"extra": {"list_only": True}},
)
print(listing["skills"])

# Select directory names and install in the shared root (requires permission)
result = client.add_skill(
    "./skills",
    wait=True,
    options={
        "target_uri": "viking://agent/skills",
        "extra": {"skills": ["search-web", "calculator"]},
    },
)
```

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
    }
  }'

# Using inline SKILL.md content
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": "---\nname: my-skill\ndescription: My custom skill\n---\n\n# My Skill\n\nSkill content here."
  }'

# Using MCP Tool format (auto-detected and converted)
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
    -F "file=@./skills/my-skill/SKILL.md" \
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
result = client.add_skill(data=skill)
print(f"Added: {result['root_uri']}")

# Approach 2: Using MCP Tool format (auto-detected and converted)
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
result = client.add_skill(data=mcp_tool)
print(f"Added: {result['uri']}")

# Approach 3: Add from local SKILL.md file
result = client.add_skill(data="./skills/search-web/SKILL.md")
print(f"Added: {result['uri']}")

# Approach 4: Add from directory containing SKILL.md (auxiliary files included)
result = client.add_skill(data="./skills/code-runner/")
print(f"Added: {result['uri']}")
print(f"Auxiliary files: {result['auxiliary_files']}")

# Check the status of the previous import task
print(client.get_task(result["task_id"]))
```

**TypeScript SDK**

```typescript
const result = await client.addSkill("./my-skill");
console.log(result.task_id);
```

**Go SDK**

```go
result, err := client.AddSkill(ctx, "./skills/my-skill/", nil)
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

`ov add-skill` and `ov skills add` share the same options and import flow.
Use `--list` to inspect a collection and `--skill` to select skills. Batch imports
require confirmation unless `--yes` is set.

```bash
# Import one skill from the standalone skills branch; ov skills add also works
ov add-skill https://github.com/volcengine/OpenViking/tree/skills/llm-wiki

# Inspect a local collection, then select skills to import
ov add-skill ./examples/compile/ov-compile-skills --list
ov add-skill ./examples/compile/ov-compile-skills --skill llm-wiki daily-report --yes

# Add a skill from a local file or directory
ov add-skill ./skills/search-web/SKILL.md
ov add-skill ./skills/code-runner/

# Target the shared root and wait for processing
ov skills add ./skills/code-runner/ -p viking://agent/skills --wait

# Check progress using the task_id returned by submission
ov task status TASK_ID

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
    "root_uri": "viking://user/alice/skills/my-skill",
    "uri": "viking://user/alice/skills/my-skill",
    "name": "my-skill",
    "auxiliary_files": 2,
    "task_id": "uuid-xxx"
  }
}
```

**CLI response (default table format)**:
```
Note: Skill processing may continue in the background.
Use 'ov task status <task_id>' to check progress, or 'ov task list' to see all tasks.
status          success
root_uri        viking://user/alice/skills/my-skill
uri             viking://user/alice/skills/my-skill
name            my-skill
auxiliary_files 2
task_id         uuid-xxx
```

**CLI response (JSON format, using -o json)**:
```json
{
  "status": "success",
  "root_uri": "viking://user/alice/skills/my-skill",
  "uri": "viking://user/alice/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2,
  "task_id": "uuid-xxx"
}
```

**Field Description**:

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Successful import results use `success`; synchronous failures use HTTP error responses |
| `root_uri` | string | Canonical final URI of the skill in OpenViking (same as `uri`) |
| `uri` | string | Canonical final URI of the skill in OpenViking (same as `root_uri`) |
| `name` | string | Skill name |
| `auxiliary_files` | number | Number of auxiliary files included with the skill |
| `task_id` | string | Returned in the default asynchronous mode; query the Task API for the background processing task's final status |
| `queue_status` | object | With `wait=true`, queue names such as `Semantic` / `Embedding` map to `processed`, `requeue_count`, `error_count`, and `errors` |

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

Error codes depend on the failure stage: invalid names/targets use `INVALID_ARGUMENT` / `INVALID_URI`, missing Skills use `NOT_FOUND`, oversized integrity manifests use `RESOURCE_EXHAUSTED`, and wait timeouts use `DEADLINE_EXCEEDED`. Success with `wait=false` only confirms the synchronous import stage; inspect task status for background failures. Standard responses may include optional `telemetry` / `profile`, but have no generic `time` field. SDKs normally return the `result` object directly.

## Skill Management Operations

The Python HTTP SDK and Go SDK expose dedicated skill management methods:
`list_skills`, `find_skills`, `validate_skill`, `get_skill`, `update_skill`,
and `delete_skill` in Python; `ListSkills`, `FindSkills`, `ValidateSkill`,
`GetSkill`, `UpdateSkill`, and `DeleteSkill` in Go. The general
filesystem/content/retrieval methods still work for URI-level access.

### List Skills

`GET /api/v1/skills` accepts query parameters `node_limit=1000` and optional `target_uri`. Without a target it merges private and shared roots, retaining same-name Skills distinguished by `root_uri`. The current server fixes directory scanning at `node_limit=1000` per root and does not forward the requested `node_limit` to that scan; do not rely on it as a global result limit or pagination parameter.

**Python SDK**

```python
skills = client.list_skills(node_limit=1000)
for skill in skills["skills"]:
    print(skill["name"])
```

**TypeScript SDK**

```typescript
console.log(await client.listSkills());
```

**Go SDK**

```go
skills, err := client.ListSkills(ctx, nil)
_ = skills
```

**HTTP API**

```bash
curl -X GET "http://localhost:1933/api/v1/skills?node_limit=1000" \
  -H "X-API-Key: your-key"
```

### Read Skill

`GET /api/v1/skills/{skill_name}`:

| Query parameter | Default | Meaning |
|-----------------|---------|---------|
| `target_uri` | Unspecified | Skills root to try first |
| `level` | Unspecified | `0` returns abstract, `1` overview, `2` SKILL.md; omitted returns all three levels |
| `include_content` | Unspecified | `true` adds content; `false` disables content when level is omitted; `level=2` always returns content |
| `include_files` | `true` | File and directory manifest, without hashes by default |
| `include_integrity` | `false` | Read a snapshot under a tree lock; requires `include_files=true` to produce file hashes and revision |
| `include_source` | `false` | Include `.source.json` tracking; absent tracking returns `source.tracked=false` |

Read, update, and delete share name resolution: by default, try the private user Skill, then the shared Skill. An explicit `target_uri` is tried first, but a miss still falls back to private/shared roots. For these operations, `target_uri` currently does not guarantee exclusive resolution within that root. Read and inspect the returned `root_uri` when managing same-name Skills.

**Python SDK**

```python
skill = client.get_skill(
    skill_name="search-web",
    include_content=True,
    include_files=True,
)
print(skill["name"])
print(skill.get("content"))
```

**TypeScript SDK**

```typescript
console.log(await client.getSkill("my-skill"));
```

**Go SDK**

```go
skill, err := client.GetSkill(ctx, "search-web", &openviking.GetSkillOptions{
    IncludeContent: openviking.Bool(true),
    IncludeFiles:   openviking.Bool(true),
})
_ = skill
```

**HTTP API**

```bash
curl -X GET "http://localhost:1933/api/v1/skills/search-web?include_content=true&include_files=true" \
  -H "X-API-Key: your-key"
```

### Integrity Manifests and Remote Use

A remote Harness can request an integrity manifest before downloading scripts or binary resources:

```python
skill = client.get_skill(
    "search-web",
    target_uri="viking://~/skills",
    include_content=True,
    include_files=True,
    include_integrity=True,
)
print(skill["root_uri"], skill["revision"], skill["content_sha256"])
for entry in skill["files"]:
    if not entry["is_dir"]:
        print(entry["path"], entry["uri"], entry["size"], entry["sha256"])
```

The HTTP equivalent is `GET /api/v1/skills/search-web?include_content=true&include_files=true&include_integrity=true`. The current Python SDK exposes `include_integrity`. TypeScript/Go `getSkill` / `GetSkill` options and `ov skills show` do not yet expose it; use HTTP or the Python SDK when needed.

| Response field | Meaning |
|----------------|---------|
| `content_sha256` | SHA-256 of the returned SKILL.md text; present whenever `content` is returned, even without integrity mode |
| `revision` | Version derived from the sorted package manifest: paths, directory flags, sizes, and hashes; not a Git commit or metadata.version |
| `files[].name/path/uri` | Entry name, package-relative path, and canonical URI |
| `files[].is_dir/kind` | Directory flag; kind is `definition`, `summary`, `auxiliary`, or `directory` |
| `files[].size/sha256` | Byte size and SHA-256 for non-directory entries in integrity mode; absent from ordinary manifests |

The manifest includes `SKILL.md`, summaries, and auxiliary files/directories, but excludes `.source.json`. Integrity limits are **512 entries (including directories), 16 MiB per file, and 64 MiB total file bytes**, with read concurrency 8. Exceeding a limit returns `RESOURCE_EXHAUSTED`. Content, manifest, and revision are obtained under one tree lock, but later downloads can encounter updates; consumers should verify file hashes and recheck revision.

Search and `get_skill` return content and manifests without executing scripts or installing files in an Agent sandbox. A Harness can read text remotely and download resources when a tool requires local paths. See [VikingBot Skills](../../../bot/docs/en/concepts/06-skills.md) for the complete consumer workflow.

### Search Skills

`POST /api/v1/skills/find` body:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `query` | Required | Search text |
| `limit` | `10` | Maximum distinct Skills returned after merging private and shared scopes |
| `score_threshold` | `null` | Minimum score; omission uses underlying retrieval defaults |
| `level` | `null` | Levels allowed to match, such as `[0]`; omission allows all levels, unlike the single integer on get |
| `target_uri` | `null` | Search scope; omitted searches private and shared roots separately |
| `telemetry` | `false` | Telemetry configuration |

Package hits are grouped by their full Skill root URI, ranked by their highest final score, then limited to `limit` Skills. Same-named Skills in different scopes remain distinct. `total` is the returned array length, not a count of all possible matches.

Each Skill is represented by its highest-scoring package hit. The existing `uri`, `level`, `score`, and `abstract` fields come directly from that hit, without additional response fields. Package grouping and pagination apply only to dedicated `skills/find`; general `find/search` continues to return individual hits.

| Response field | Meaning |
| --- | --- |
| `uri` / `level` | Actual hit URI and level: L0 points to `.abstract.md`, L1 to `.overview.md`, and L2 to the matching file |
| `score` | The hit's final score, also used to rank its Skill |
| `abstract` | The hit record's existing summary, following the original search rules |
| `name` / `description` / `tags` / `allowed_tools` | Metadata read separately from the Skill root by the dedicated `skills/find` endpoint |
| `root_uri` / `skill_md_uri` | The Skill root and main `SKILL.md` addresses returned by the dedicated `skills/find` endpoint |

The dedicated `skills/find` endpoint now returns the actual hit URI instead of replacing it with the package root. List and get-by-name responses are unchanged. `level=[2]` restricts matching to files and returns `level=2` with the URI of the highest-scoring file in each package.

General search with `read_content=true` continues to read the returned URI. The dedicated `skills/find` endpoint does not accept this option.

The URI rules above apply to semantic search. Filter-only general `find` retains the stored record URI and returns `score=0`, without adding summary-file suffixes for L0 or L1.

Scope, level, and permission filters apply before grouping; the Skill root must also be accessible.

**Python SDK**

```python
results = client.find_skills(query="search the internet", limit=5)

for skill in results["skills"]:
    print(skill["name"], skill["score"])
```

**TypeScript SDK**

```typescript
console.log(await client.findSkills("database migration"));
```

**Go SDK**

```go
results, err := client.FindSkills(ctx, "search the internet", &openviking.FindSkillsOptions{
    Limit: 5,
})
_ = results
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/skills/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "search the internet",
    "limit": 5
  }'
```

### Validate and Update Skills

`POST /api/v1/skills/validate` accepts required `data` (structured data or complete SKILL.md text), `strict=false`, `source_path=null`, `skill_dir_name=null`, and `target_uri=null`. `source_path` is report information, not an instruction to read a server-local file. The current endpoint accepts but does not use `target_uri` for validation. SDK `validate_skill` does not upload paths automatically; read the file text first.

Missing names/descriptions and invalid YAML produce errors. Directory/name mismatch, invalid name length/characters, and descriptions over 1024 characters produce warnings normally and errors with `strict=true`. A body exceeding 500 lines remains only a warning in strict mode. Validation does not install Skills or check registered tools and executable dependencies; it does not replace import or Harness checks.

`PUT /api/v1/skills/{skill_name}` replaces the entire package and accepts:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `data` / `temp_file_id` | Unspecified | New content or uploaded package, as for add |
| `from_source` | `false` | Refresh from recorded Git source; cannot be combined with data/temp_file_id |
| `target_uri` | Unspecified | Skills root to try first; see read resolution fallback |
| `source_metadata` | Generated automatically | Updated source tracking |
| `wait` / `timeout` | `false` / `null` | Wait for background processing and timeout in seconds |
| `telemetry` | `false` | Telemetry configuration |

Supply new content/an upload, or set `from_source=true`. The new name must match `skill_name` in the URL; update is neither rename nor a partial patch. The server parses and checks the new package before backing up and replacing the old one, and attempts restoration on synchronous failure. Include all auxiliary files that should remain in the replacement package.

Background processing starts after the update's files, privacy configuration, and task setup are ready. When an update needs restoration, including a `wait=true` timeout, it cancels that update's summary and indexing work, waits for started writes to exit, and restores the original files, index, and privacy configuration. Restoration failures are reported. The timeout response may arrive after `timeout` while cancellation settles; it does not wait for the entire package to finish processing. Background failures after a successful `wait=false` response do not trigger restoration. A timeout when adding a new Skill still only ends the wait.

**Python SDK**

```python
validated = client.validate_skill(data={"name": "search-web", "description": "..."})
print(validated["valid"], validated["errors"], validated["warnings"])
updated = client.update_skill(
    skill_name="search-web",
    data="./skills/search-web",
)
```

**TypeScript SDK**

```typescript
console.log(await client.validateSkill({
  name: "search-web",
  description: "Search the web for current information",
  content: "# search-web\n\nSearch the web for current information.",
}));
```

**Go SDK**

```go
validated, err := client.ValidateSkill(ctx, map[string]any{
    "name":        "search-web",
    "description": "...",
}, nil)
updated, err := client.UpdateSkill(ctx, "search-web", "./skills/search-web", nil)
_, _ = validated, updated
```

**HTTP API**

```bash
# Validate skill data
curl -X POST http://localhost:1933/api/v1/skills/validate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"data": {"name": "search-web", "description": "..."}}'

# Replace an existing skill with new content
curl -X PUT http://localhost:1933/api/v1/skills/search-web \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "search-web",
      "description": "Search the web for current information",
      "content": "# search-web\n\nUpdated instructions."
    }
  }'
```

Source refresh requires a recorded Git source and fetches the recorded repository, ref, and subdirectory again. A local upload has no Git source to pull automatically:

```python
updated = client.update_skill(
    "search-web",
    data=None,
    wait=True,
    options={"extra": {"from_source": True}},
)
```

```bash
curl -X PUT http://localhost:1933/api/v1/skills/search-web \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"from_source": true, "wait": true}'
```

### Delete Skills

`DELETE /api/v1/skills/{skill_name}` accepts optional query parameter `target_uri`. It recursively removes the resolved package and handles its associated Skill privacy configuration. Target fallback is the same as for read.

**Python SDK**

```python
client.delete_skill(skill_name="old-skill")
```

**TypeScript SDK**

```typescript
await client.deleteSkill("my-skill");
```

**Go SDK**

```go
deleted, err := client.DeleteSkill(ctx, "old-skill")
_ = deleted
```

**HTTP API**

```bash
curl -X DELETE "http://localhost:1933/api/v1/skills/old-skill" \
  -H "X-API-Key: your-key"
```

### CLI Management Commands

```bash
ov skills list -p viking://~/skills
ov skills find "search the internet" --level 0 --limit 5 -p viking://~/skills
ov skills show search-web --level 2 --files --source -p viking://~/skills
ov skills validate ./skills/search-web --strict
ov skills update search-web -p viking://~/skills --wait --yes
ov skills remove old-skill -p viking://~/skills --yes
```

The long form of `-p` is `--uri` for `list/find/show` and `--parent-auto-create` for `add/update/remove`. `show` returns all text levels by default; request files with `--files` and tracking with `--source`. `validate` runs locally without installation. `update` refreshes recorded Git sources; for non-Git sources, interactive use can prompt for a path/URL.

Without names, `ov skills update` attempts all installed Skills and reports unsuccessful entries in `skipped`. `remove` without names opens interactive selection; only `--all` selects everything. Update and remove require confirmation; use `--yes` explicitly in scripts.

### Skill Management Responses

List and search return a `skills` array and `total`. Without `target_uri`, `root_uris` identifies the private user and shared Agent roots; with a target, the response contains a single `root_uri`.

```json
{
  "status": "ok",
  "result": {
    "root_uris": [
      "viking://user/default/skills",
      "viking://agent/skills"
    ],
    "skills": [
      {
        "type": "skill",
        "name": "search-web",
        "uri": "viking://user/default/skills/search-web",
        "root_uri": "viking://user/default/skills/search-web",
        "skill_md_uri": "viking://user/default/skills/search-web/SKILL.md",
        "description": "Search the web for current information",
        "tags": [],
        "allowed_tools": [],
        "score": 0.87,
        "match_reason": "semantic",
        "level": 0
      }
    ],
    "total": 1
  }
}
```

Reading one skill returns the metadata above and conditionally adds `abstract`, `overview`, `content`, `files`, and `source` according to `level` and the `include_*` parameters.

Validation returns `valid`, `strict`, normalized metadata, `body_lines`, `errors`, and `warnings`. Invalid input still uses a successful response envelope with `valid=false`:

```json
{
  "status": "ok",
  "result": {
    "valid": false,
    "strict": false,
    "name": "search-web",
    "description": "",
    "tags": [],
    "allowed_tools": [],
    "body_lines": 0,
    "errors": [
      {
        "rule": "description_required",
        "message": "description is required",
        "field": "description"
      }
    ],
    "warnings": []
  }
}
```

A successful update returns the same processing result as `add_skill` with an additional `"action": "update"`. A successful delete returns:

```json
{
  "status": "ok",
  "result": {
    "name": "old-skill",
    "uri": "viking://user/default/skills/old-skill",
    "root_uri": "viking://user/default/skills/old-skill",
    "estimated_deleted_count": 4,
    "privacy_deleted": false
  }
}
```

`estimated_deleted_count` appears only when the filesystem can estimate the number of deleted entries.

## Best Practices

### Clear Descriptions

```python
# Good - specific and actionable
skill = {
    "name": "search-web",
    "description": "Search the web for current information using Google",
    # Additional skill fields
}

# Less helpful - too vague
skill = {
    "name": "search",
    "description": "Search",
    # Additional skill fields
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
- [VikingBot Skills](../../../bot/docs/en/concepts/06-skills.md) - Local/remote activation, metadata, and execution
