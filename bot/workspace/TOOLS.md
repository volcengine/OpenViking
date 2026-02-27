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
Search the web using Brave Search API.
```
web_search(query: str, count: int = 5) -> str
```

Returns search results with titles, URLs, and snippets. Requires `tools.web.search.apiKey` in config.

### web_fetch
Fetch and extract main content from a URL.
```
web_fetch(url: str, extractMode: str = "markdown", maxChars: int = 50000) -> str
```

**Notes:**
- Content is extracted using readability
- Supports markdown or plain text extraction
- Output is truncated at 50,000 characters by default

## Communication

### message
Send a message to the user (used internally).
```
message(content: str, channel: str = None, chat_id: str = None) -> str
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
```bash
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
```bash
# At a specific time (ISO format)
cron(
    action="add",
    name="meeting",
    message="Meeting starts now!",
    at="2025-01-31T15:00:00"
) 
```

### Manage reminders
```bash
# List all jobs
cron(
    action="list"
) 
vikingbot cron list              
# Remove a job
cron(
    action="remove",
    job_id=<job_id>
) 
```

## Heartbeat Task Management

The `HEARTBEAT.md` file in the workspace is checked at regular intervals
Use file operations to manage periodic tasks:

### Add a heartbeat task
```python
# Append a new task
edit_file(
    path="HEARTBEAT.md",
    old_text="## Example Tasks",
    new_text="- [ ] New periodic task here\n\n## Example Tasks"
)
```

### Remove a heartbeat task
```python
# Remove a specific task
edit_file(
    path="HEARTBEAT.md",
    old_text="- [ ] Task to remove\n",
    new_text=""
)
```

### Rewrite all tasks
```python
# Replace the entire file
write_file(
    path="HEARTBEAT.md",
    content="# Heartbeat Tasks\n\n- [ ] Task 1\n- [ ] Task 2\n"
)
```