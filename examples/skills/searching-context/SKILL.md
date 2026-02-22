---
name: ovs
description: Search OpenViking context. Trigger when user says "ovs", asks to search files/knowledge, or when context retrieval is needed.
user-invocable: true
command-dispatch: tool
command-tool: exec
command-arg-mode: raw
compatibility: CLI configured at `~/.openviking/ovcli.conf`
---

# OpenViking `search`

The `ov search` command performs context-aware retrieval across all memories and resources.

## When to Use

- Finding specific information within imported resources or saved memories
- Retrieving context about topics, APIs, or patterns previously added

## Usage

```bash
# Basic search
ov search "how to handle API rate limits"

# With scope
ov search "authentication" --uri "viking://resources/my-project"

# Limit results
ov search "error handling" --limit 5
```

## Other Commands

- `ov grep` — literal pattern matching
- `ov glob` — file path pattern matching
- `ov ls` — browse directory structure
- `ov tree` — visualize hierarchy

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
