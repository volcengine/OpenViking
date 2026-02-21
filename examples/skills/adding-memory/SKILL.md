---
name: adding-memory
description: Add memories, learnings and context to OpenViking, aka. ov. Use when saving insights in chat. Trigger this tool when 1. sees keyword "ovm"; 2. is explicitly requested memorizing e.g. "remember ..." 3. identifies valuable memory worth adding
compatibility: configuration file at `~/.openviking/ovcli.conf`
---

# OpenViking (OV) `add-memory`

The `ov add-memory` command adds long persistant memory — turning text and structured conversations into searchable, retrievable memories in the OpenViking context database.

## When to Use

- After learning something worth remembering across sessions
- To persist conversation insights, decisions, or findings
- To build up a knowledge base from interactions
- When an agent wants to store context for future retrieval

## Input Modes
choose wisely between plain text and multi-turn mode. Multi-turn mode can contain more complex insights, let openviking handle the memory extraction.

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
  {"role": "assistant", "content": "You can use train, bus, or plane. Train is the fastest, but you need to book in advance. Bus is cheaper, but you need to wait. Plane is the most expensive, but you can get there any time of day."},
  {"role": "user", "content": "I prefer train. I like sightseeing on the train. Can you give me the train schedule?"},
  < ... more possible conversation about schedule and tickest need to be memorized ... >
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
  {"role": "assistant", "content": "1. runs faster than python cli\n2. uses HttpClient to connect openviking server\n3. Output formatting supports table and JSON modes\n4. Config lives at ~/.openviking/ovcli.conf"}
]'
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
