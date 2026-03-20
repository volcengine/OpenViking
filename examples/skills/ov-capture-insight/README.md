# ov-capture-insight

> Automatically capture important insights and learnings from conversations

## Overview

This skill enables OpenClaw agents to automatically identify and store valuable insights from conversations into OpenViking's memory system. It helps build long-term knowledge and avoid repeating explanations or solutions.

## Features

- **Automatic Detection** - Identifies valuable insights in conversation
- **Categorization** - Tags insights by topic (debugging, configuration, performance, etc.)
- **Structured Storage** - Saves in a format optimized for future retrieval
- **Context Preservation** - Maintains the context around why an insight was valuable

## Installation

Copy this directory to your OpenClaw skills folder:

```bash
cp -r ov-capture-insight ~/.openclaw/skills/
```

## Usage

The skill triggers automatically when:
1. A significant problem is solved
2. The user asks to "remember this"
3. A best practice or pattern is discovered
4. A non-obvious debugging solution is found

Example trigger:

```
User: "I found that setting pool_size=10 fixes the connection timeout issue"

Agent: That's valuable! Let me capture this insight... [skill activates]
```

## Categories

| Category | Use Case |
|----------|----------|
| debugging | Problem solutions, bug fixes |
| configuration | Setup tips, optimization settings |
| performance | Speed improvements, resource usage |
| security | Security considerations, vulnerabilities |
| workflow | Process improvements, best practices |

## Related Skills

- [ov-search-context](../ov-search-context) - Search stored memories
- [ov-add-data](../ov-add-data) - Add resources to OpenViking

---

Part of [OpenViking](https://github.com/volcengine/OpenViking) project.
