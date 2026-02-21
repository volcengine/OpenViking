---
name: searching-context
description: Search memories and resource context from OpenViking, aka. ov. Trigger this tool when 1. sees keyword "ovs"; 2. is explicitly requested searching files or knowledge; 3. sees `search context` request
compatibility: CLI configured at `~/.openviking/ovcli.conf`
---
# OpenViking (OV) context searching
The `ov search` command performs context-aware retrieval across all memories and resources in OpenViking — combining semantic understanding with directory recursive retrieval to find the most relevant context for any query.

## Table of Content
- When to Use
- Search Modes
  - Context-Aware Search (`ov search`)
  - Content Pattern Search (`ov grep`)
  - File Glob Search (`ov glob`)
- Search Options
  - Result Control
  - Scope Control
  - Session-Aware Search
- Output
- Other Utilities
  - List Contents (`ov ls`)
  - Tree View (`ov tree`)
  - Read File Content (`ov read`)
- Agent Best Practices
  - Choosing Search Methods
  - Query Formulation
  - Combining with Other Commands
- Prerequisite

## When to Use

- Finding specific information within imported resources or saved memories
- Retrieving context about topics, APIs, or patterns previously added
- Searching across project documentation, code, and learnings
- When an agent needs to reference previously stored knowledge

> note: cli command can be outdated, when sees error, use `--help` to get latest usage

## Search Modes

### Context-Aware Search (`ov search`)

Primary search method with session context awareness:

```bash
# Basic search across all context
ov search "how to handle API rate limits"

# Search within specific URI scope
ov search "authentication flow" --uri "viking://resources/my-project"

# Limit results and set threshold
ov search "error handling" --limit 5 --threshold 0.7
```

### Content Pattern Search (`ov grep`)

Literal pattern matching:

```bash
# Search for exact text pattern
ov grep "viking://resources" "TODO:"

# Case-insensitive search
ov grep "viking://resources" "API_KEY" --ignore-case
```

### File Glob Search (`ov glob`)

File path pattern matching:

```bash
# Find all markdown files
ov glob "**/*.md"

# Find Python files in specific directory
ov glob --uri="viking://user/" "**/*.md"
```

## Search Options

### Result Control

```bash
# Limit number of results
ov search "authentication" --limit 5

# Set minimum relevance threshold (0.0-1.0)
ov search "deployment" --threshold 0.8

# Combine limit and threshold
ov search "monitoring" --limit 10 --threshold 0.7
```

### Scope Control

```bash
# Search specific directory
ov search "API design" --uri "viking://resources/xxx"

# Search within memories
ov search "user habits" --uri "viking://user/memories"
```

### Session-Aware Search

```bash
# Search with session context (uses conversation history for better retrieval)
ov search "previous decision" --session-id "session-abc123"
```

## Output

Returns a ranked list of matching resources with relevance scores:

```
URI: viking://resources/docs/api.md
Score: 0.8523
Abstract: API endpoints for user authentication...

URI: viking://user/memories/preferences
Score: 0.7891
Abstract: User prefers dark mode and compact layout...
```

## Other Utilities

### List Contents (`ov ls`)

Browse directory structure:

```bash
# List root directory
ov ls

# List specific directory
ov ls viking://resources/my-project

# Simple path output (for scripts)
ov ls viking://resources --simple

# Recursive listing
ov ls viking://resources --recursive

# Show hidden files
ov ls viking://resources --all

# Control output limits
ov ls viking://resources --node-limit 50 --abs-limit 128
```

### Tree View (`ov tree`)

Visualize directory hierarchy:

```bash
# Show tree structure
ov tree viking://resources

# Control depth and limits
ov tree viking://resources --node-limit 100 --abs-limit 128

# Show all files including hidden
ov tree viking://resources --all
```

### Read File Content (`ov read`)

Retrieve full content (L2 layer):

```bash
# Read full content
ov read viking://resources/docs/api.md

# Read abstract (L0 - quick summary)
ov abstract viking://resources/docs/api.md

# Read overview (L1 - key points)
ov overview viking://resources/docs/api.md
```

## Agent Best Practices

### Choosing Search Methods

- **`ov search`** — Default choice. Context-aware, combines semantic + directory recursive retrieval
- **`ov grep`** — Exact text pattern matching (like `grep` command)
- **`ov glob`** — File path pattern matching (like shell glob)

### Query Formulation

Write specific, contextual queries:

```bash
# Too vague
ov search "API"

# Better
ov search "REST API authentication with JWT tokens"

# Even better with scope
ov search "JWT token refresh flow" --uri "viking://resources/backend"
```

### Combining with Other Commands

Use search results to guide further actions:

```bash
ov ls viking://resources/

# Search for relevant files
ov search "authentication" --uri "viking://resources/xxx"

# Then read specific content
ov read viking://resources/backend/auth.md

# Or get overview for context
ov overview viking://resources/backend
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
- Resources or memories previously added to OpenViking
