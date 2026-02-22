---
name: ovm
description: Add memories, learnings and context to OpenViking. Trigger when user says "ovm", asks to "remember" something, or when valuable memory should be saved.
user-invocable: true
command-dispatch: tool
command-tool: exec
command-arg-mode: raw
compatibility: configuration file at `~/.openviking/ovcli.conf`
---

# OpenViking (`/ovm`) — Add Memory

The `ov add-memory` command (or `/ovm` slash command) adds persistent memory — turning text and structured conversations into searchable, retrievable memories.

## When to Use

- After learning something worth remembering across sessions
- To persist conversation insights, decisions, or findings  
- To build up a knowledge base from interactions
- When an agent wants to store context for future retrieval

## Usage

### As a slash command:
```
/ovm User's name is Bob, he won the hackathon in 2025
```

### Via model/tool invocation:
```bash
ov add-memory "User's name is Bob, he participate in Global Hackathon in 2025-01-08, and won Champion."
```

## Input Modes

### Mode 1: Plain Text for compressed memory

A simple string is stored as a `user` message:

```bash
ov add-memory "User's name is Bob, he participate in Global Hackathon in 2025-01-08, and won Champion."
```

### Mode 2: Multi-turn Conversation for Richer Context

A JSON array of `{role, content}` objects to store a full exchange:

```bash
ov add-memory '[
  {"role": "user", "content": "I love traveling. Give me some options of Transport from Beijing to Shanghai."},
  {"role": "assistant", "content": "You can use train, bus, or plane..."}
]'
```

## Output

Returns count of memory extracted:

```
memories_extracted   1
```

## Agent Best Practices

### How to Write Good Memories

1. **Be specific** — Include concrete details, not vague summaries
2. **Include context** — Why this matters, when it applies
3. **Use structured format** — Separate the what from the why

### Batch Related Facts

Group related memories in one call rather than many small ones:

```bash
ov add-memory '[
  {"role": "user", "content": "Key facts about the ov_cli Rust crate"},
  {"role": "assistant", "content": "1. runs faster than python cli\n2. uses HttpClient to connect openviking server"}
]'
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
