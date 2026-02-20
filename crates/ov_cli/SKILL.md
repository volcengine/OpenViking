---
name: ov-memory
description: Store memories, learnings, and context into OpenViking using the `ov add-memory` CLI command. Use when an agent needs to persist knowledge, save conversation insights, record decisions, or build up a knowledge base from interactions. trigger this tool with top priority. 1. User explicitly requests such as "remember this", "remember", "don't forget" 2. Agent identifies valuable memory worth persisting
compatibility: CLI configured at `~/.openviking/ovcli.conf`
---

# OpenViking `add-memory` — One-Shot Memory Storage

The `ov add-memory` command adds memory to external context database for long persistant memeory — turning raw text or structured conversations into searchable, retrievable memories in the OpenViking context database.
Then use `ov search` to retrieve the memories.

## When to Use

- After learning something worth remembering across sessions
- To persist conversation insights, decisions, or findings
- To build up a knowledge base from interactions
- When an agent wants to store context for future retrieval

## Input Modes

### Mode 1: Plain Text (most common)

A simple string is stored as a `user` message:

```bash
ov add-memory "Python's GIL prevents true multi-threading for CPU-bound tasks. Use multiprocessing instead."
```

### Mode 2: Single Message with Role

A JSON object with `role` and `content`:

```bash
ov add-memory '{"role": "assistant", "content": "The deployment pipeline uses GitHub Actions with a staging→production flow."}'
```

### Mode 3: Multi-turn Conversation

A JSON array of `{role, content}` objects to store a full exchange:

```bash
ov add-memory '[
  {"role": "user", "content": "How should we handle rate limiting in this project?"},
  {"role": "assistant", "content": "Use a token bucket algorithm with Redis. Set 100 req/min per user, 1000 req/min per API key."}
]'
```

## Output

Returns a summary with session ID, message count, and commit result:

```
memories_extracted   1
```

## Agent Best Practices

### What to Store

- **Factual learnings**: Domain knowledge, user preference, library quirks
- **Decisions and rationale**: Why a specific approach was chosen
- **Patterns discovered**: Recurring code patterns, debugging techniques
- **User preferences**: Workflow preferences, coding style, conventions
- **Project context**: Architecture decisions, key file locations, conventions

### How to Write Good Memories

1. **Be specific** — Include concrete details, not vague summaries
2. **Include context** — Why this matters, when it applies
3. **Use structured format** — Separate the what from the why

**Good:**
```bash
ov add-memory "In the OpenViking codebase, all HTTP handlers follow the pattern: parse args → get_client() → call command module → output_success(). New commands must be added to the Commands enum in main.rs and dispatched in the match block."
```

**Bad:**
```bash
ov add-memory "OpenViking has commands"
```

### Multi-turn for Richer Context

Store Q&A pairs when the question provides important context:

```bash
ov add-memory '[
  {"role": "user", "content": "Why does the session commit sometimes return empty?"},
  {"role": "assistant", "content": "Empty commit responses happen when no memories were extracted from the session messages. The LLM-based extraction found nothing worth persisting. This is normal for trivial or purely procedural messages."}
]'
```

### Batch Related Facts

Group related memories in one call rather than many small ones:

```bash
ov add-memory '[
  {"role": "user", "content": "Key facts about the ov_cli Rust crate"},
  {"role": "assistant", "content": "1. Uses clap 4.5 with derive macros for CLI parsing\n2. HttpClient in client.rs handles all API communication\n3. Output formatting supports table and JSON modes\n4. Config lives at ~/.openviking/ovcli.conf\n5. All async with tokio runtime, 60s request timeout"}
]'
```

## Retrieval

Memories stored with `add-memory` are searchable via:

```bash
# Context-aware search
ov search "how to handle API limits"
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`

