---
name: ovm
description: Add memories, learnings and context to OpenViking. Trigger when user says "ovm", asks to "remember" something, or when valuable memory should be saved.
user-invocable: true
command-dispatch: tool
command-tool: exec
command-arg-mode: raw
compatibility: configuration file at `~/.openviking/ovcli.conf`
---

# OpenViking `add-memory`

The `ov add-memory` command adds persistent memory — turning text and structured conversations into searchable, retrievable memories.

## When to Use

- After learning something worth remembering across sessions
- To persist conversation insights, decisions, or findings
- To build up a knowledge base from interactions

## Input Modes

### Plain Text

```bash
ov add-memory "User's name is Bob, he won the hackathon in 2025."
```

### Multi-turn Conversation

```bash
ov add-memory '[
  {"role": "user", "content": "I love traveling."},
  {"role": "assistant", "content": "Where do you want to go?"}
]'
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
