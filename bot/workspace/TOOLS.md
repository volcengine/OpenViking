# Available Tools

**IMPORTANT: Always use OpenViking first for knowledge queries and memory storage**

## OpenViking Knowledge Base (Use First)

When querying information or files, **always use OpenViking tools first** before web search or other methods.

### Search Resources
```
openviking_search(query: str, target_uri: str = None) -> str
```
Search for knowledge, documents, code, and resources in OpenViking. Use this as the first step for any information query.

### Read Content
```
openviking_read(uri: str, level: str = "abstract") -> str
```
Read resource content from OpenViking. Levels: abstract (summary), overview, read (full content).

### List Resources
```
openviking_list(uri: str, recursive: bool = False) -> str
```
List all resources at a specified path.

### Search User Memories
```
user_memory_search(query: str) -> str
```
Search for user-related memories and events.

### ⚠️ CRITICAL: Commit Memories and Events
```
openviking_memory_commit(session_id: str, messages: list) -> str
```
**All important conversations, events, and memories MUST be committed to OpenViking** for future retrieval and context understanding.

---

## Shell Execution

### exec
Execute a shell command and return output.
```
exec(command: str, working_dir: str = None) -> str
```

**Safety Notes:**
- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- Optional `restrictToWorkspace` config to limit paths

## Web Access

### web_search
Search the web using configurable backend (Brave Search, DuckDuckGo, or Exa).
```
web_search(query: str, count: int = 5, type: str = None, livecrawl: str = None) -> str
```

Returns search results with titles, URLs, and snippets. Requires API key configuration.
- `count`: Number of results (1-20, default 5)
- `type` (Exa only): Search type - "auto", "fast", or "deep"
- `livecrawl` (Exa only): Live crawl mode - "fallback" or "preferred"

### web_fetch
Fetch and extract main content from a URL.
```
web_fetch(url: str, extractMode: str = "markdown", maxChars: int = 50000) -> str
```

**Notes:**
- Content is extracted using readability
- Supports markdown or plain text extraction
- Output is truncated at 50,000 characters by default

## Image Generation

### generate_image
Generate images from scratch, edit existing images, or create variations.
```
generate_image(
    mode: str = "generate",
    prompt: str = None,
    base_image: str = None,
    mask: str = None,
    size: str = "1920x1920",
    quality: str = "standard",
    style: str = "vivid",
    n: int = 1
) -> str
```

**Modes:**
- `generate`: Generate from scratch (requires `prompt`)
- `edit`: Edit existing image (requires `prompt` and `base_image`)
- `variation`: Create variations (requires `base_image`)

**Parameters:**
- `base_image`: Base image for edit/variation: base64 data URI, URL, or file path
- `mask`: Mask image for edit mode (optional, transparent areas indicate where to edit
- `size`: Image size (only "1920x1920" supported)
- `quality`: "standard" or "hd"
- `style`: "vivid" or "natural" (DALL-E 3 only)
- `n`: Number of images (1-4)

## Communication

### message
Send a message to the user (used internally).
```
message(content: str) -> str
```

## Background Tasks

### spawn
Spawn a subagent to handle a task in the background.
```
spawn(task: str, label: str = None) -> str
```

Use for complex or time-consuming tasks that can run independently. The subagent will complete the task and report back when done.

## Scheduled Reminders (Cron)

Use the `cron` tool to create scheduled reminders:

### Set a recurring reminder
```
# Every day at 9am
cron(
    action="add",
    name="morning",
    message="Good morning! ☀️",
    cron_expr="0 9 * * *"
)

# Every 2 hours
cron(
    action="add",
    name="water",
    message="Drink water! 💧",
    every_seconds=7200
)
```

### Set a one-time reminder
```
# At a specific time (ISO format)
cron(
    action="add",
    name="meeting",
    message="Meeting starts now!",
    at="2025-01-31T15:00:00"
)
```

### Manage reminders
```
# List all jobs
cron(
    action="list"
)

# Remove a job
cron(
    action="remove",
    job_id="<job_id>"
)
```

## OpenViking Tools

### openviking_read
Read content from OpenViking resources at different levels.
```
openviking_read(uri: str, level: str = "abstract") -> str
```

**Levels:**
- `abstract`: L0 - Brief summary
- `overview`: L1 - Medium overview
- `read`: L2 - Full content

### openviking_list
List resources in an OpenViking path.
```
openviking_list(uri: str = "", recursive: bool = False) -> str
```

### openviking_search
Search for resources in OpenViking using semantic search.
```
openviking_search(query: str, target_uri: str = None) -> str
```

### openviking_grep
Search OpenViking resources using regex patterns.
```
openviking_grep(uri: str, pattern: str, case_insensitive: bool = False) -> str
```

### openviking_glob
Find OpenViking resources using glob patterns.
```
openviking_glob(pattern: str, uri: str = "") -> str
```

### user_memory_search
Search for user memories in OpenViking.
```
user_memory_search(query: str) -> str
```

## Heartbeat Task Management

The `HEARTBEAT.md` file in the workspace is checked at regular intervals.
Use file operations to manage periodic tasks:

### Add a heartbeat task
```
# Append a new task
edit_file(
    path="HEARTBEAT.md",
    old_text="## Example Tasks",
    new_text="- [ ] New periodic task here\n\n## Example Tasks"
)
```

### Remove a heartbeat task
```
# Remove a specific task
edit_file(
    path="HEARTBEAT.md",
    old_text="- [ ] Task to remove\n",
    new_text=""
)
```

### Rewrite all tasks
```
# Replace the entire file
write_file(
    path="HEARTBEAT.md",
    content="# Heartbeat Tasks\n\n- [ ] Task 1\n- [ ] Task 2\n"
)
```
