---
name: ov-capture-insight
description: Automatically capture important insights and learnings from conversations into OpenViking memory. Trigger this skill when 1. the conversation reveals an important insight or learning; 2. the user explicitly asks to "remember this" or "save this for later"; 3. a significant problem was solved or a best practice was discovered.
compatibility: CLI configured at `~/.openviking/ovcli.conf`
---
# OpenViking (OV) Insight Capture

The `ov capture-insight` skill automatically extracts and stores valuable insights from conversations into OpenViking's memory system. This helps agents build long-term knowledge and avoid repeating the same explanations or solutions.

## When to Use

- After solving a complex problem that might occur again
- When the user explicitly asks to "remember this" or "save this insight"
- After discovering a best practice or pattern worth preserving
- When learning something new that could benefit future conversations
- After debugging an issue with a non-obvious solution

## How It Works

The skill analyzes the conversation and:

1. **Identifies Key Insights** - Extracts actionable knowledge from the discussion
2. **Categorizes by Topic** - Tags the insight with relevant categories (debugging, configuration, best-practice, etc.)
3. **Stores in Memory** - Saves to OpenViking with proper context for future retrieval
4. **Links to Resources** - Associates with any referenced files or documentation

## Usage Examples

### Manual Capture

```bash
# Capture a specific insight
ov capture-insight "The API rate limit can be bypassed by using exponential backoff with max 5 retries"

# Capture with category
ov capture-insight --category debugging "The database connection issue was caused by incorrect pool size"

# Capture with tags
ov capture-insight --tags "python,async,performance" "Using uvloop can improve async performance by 2x"
```

### Automatic Capture Patterns

When these patterns appear in conversation, the agent should consider capturing:

| Pattern | Example | Category |
|---------|---------|----------|
| Problem solved | "The bug was caused by..." | debugging |
| Best practice | "Always remember to..." | best-practice |
| Configuration tip | "Set this to optimize..." | configuration |
| Performance finding | "This approach is faster because..." | performance |
| Security note | "Make sure to never expose..." | security |
| Workflow improvement | "A better way to do this is..." | workflow |

### Example Skill Invocation

```
User: "I just discovered that adding `timeout: 30` to the config fixes the hanging issue"

Agent: (Triggering ov-capture-insight skill)
That's a valuable finding! Let me capture this insight for future reference.

[Captured insight: Configuration fix for hanging issue - adding timeout parameter]

Now I'll remember this for similar issues in the future. The insight has been stored in OpenViking memory.
```

## Capture Format

Insights are stored in a structured format:

```markdown
## [Category] Insight Title

**Captured**: YYYY-MM-DD HH:MM
**Tags**: tag1, tag2, tag3

### Context
[What was the situation or problem]

### Insight
[The actual insight or learning]

### Application
[When and how to apply this knowledge]

### Source
[Reference to conversation or documentation]
```

## Retrieving Captured Insights

Later, when facing a similar situation:

```bash
# Search for insights
ov find "hanging issue timeout"

# Browse insights by category
ov ls viking://memories/insights/debugging/

# Read a specific insight
ov read viking://memories/insights/debugging/timeout-fix.md
```

## Best Practices

1. **Be Specific** - Capture concrete details, not vague statements
2. **Include Context** - Why was this insight important?
3. **Add Actionable Steps** - How should this be applied?
4. **Tag Properly** - Use consistent tags for better retrieval
5. **Review Periodically** - Clean up outdated insights

## Example Insights

### Debugging Insight

```bash
ov capture-insight --category debugging --tags "python,import,module" <<EOF
Python Import Error Resolution:
When getting "ModuleNotFoundError" but the module exists, check:
1. PYTHONPATH environment variable
2. __init__.py in package directories
3. Virtual environment activation
4. Case sensitivity on different OS
EOF
```

### Configuration Insight

```bash
ov capture-insight --category configuration --tags "nginx,performance" <<EOF
Nginx Worker Optimization:
For high-traffic servers, set worker_processes to auto and 
worker_connections to 4096 for optimal performance.
Monitor with: nginx -t && systemctl reload nginx
EOF
```

## Integration with OpenClaw

When using with OpenClaw agents:

1. **Auto-trigger** - The skill automatically activates when detecting valuable insights
2. **Context-aware** - Considers the current project and conversation topic
3. **Retrieval-ready** - Insights become searchable in future sessions
4. **Team sharing** - In remote mode, insights can be shared across team members

## Prerequisites

- OpenViking CLI configured: `~/.openviking/ovcli.conf`
- OpenViking server running (local or remote)
- Write permissions to the memory storage

## See Also

- [ov-search-context](../ov-search-context/SKILL.md) - Searching stored context
- [ov-add-data](../ov-add-data/SKILL.md) - Adding resources to OpenViking
- [OpenClaw Plugin README](../../openclaw-plugin/README.md) - Integration guide
