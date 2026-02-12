# Chat vs ChatMem: Feature Comparison

## Overview

Both examples provide multi-turn conversation interfaces using OpenViking's RAG pipeline. The key difference is **memory persistence**.

## Side-by-Side Comparison

| Aspect | examples/chat/ | examples/chatmem/ |
|--------|---------------|-------------------|
| **Core Functionality** |
| Multi-turn conversation | ✅ Yes | ✅ Yes |
| RAG (search + LLM) | ✅ Yes | ✅ Yes |
| Source attribution | ✅ Yes | ✅ Yes |
| Rich TUI | ✅ Yes | ✅ Yes |
| Command history (↑↓) | ✅ Yes | ✅ Yes |
| **Memory & Persistence** |
| In-memory history | ✅ During session | ✅ During session |
| Persist across runs | ❌ No | ✅ Yes |
| Session management | ❌ No | ✅ Yes |
| Memory extraction | ❌ No | ✅ Yes |
| Session archives | ❌ No | ✅ Yes |
| **Storage** |
| Data directory | Symlink to query | Symlink to query |
| Session storage | ❌ None | ✅ data/session/ |
| Memory storage | ❌ None | ✅ data/memory/ |
| **Configuration** |
| Session ID | ❌ N/A | ✅ --session-id flag |
| All other options | ✅ Same | ✅ Same |
| **Use Cases** |
| Quick questions | ✅ Perfect | ⚠️ Overkill |
| One-off conversations | ✅ Perfect | ⚠️ Overkill |
| Long-term projects | ❌ No memory | ✅ Perfect |
| Multi-session work | ❌ No context | ✅ Perfect |
| Knowledge accumulation | ❌ Starts fresh | ✅ Builds over time |

## When to Use Each

### Use `examples/chat/` when:

✅ **Quick testing** - Just want to try a query
✅ **One-off questions** - Don't need to remember context
✅ **Prototyping** - Building something new
✅ **Clean slate** - Want fresh context every time
✅ **Simplicity** - Don't want to manage sessions

### Use `examples/chatmem/` when:

✅ **Long-term projects** - Working on something over days/weeks
✅ **Context matters** - Need to remember previous conversations
✅ **Knowledge building** - Accumulating information over time
✅ **Multiple topics** - Use different session IDs per topic
✅ **Production use** - Real applications with memory needs

## Code Differences

### examples/chat/chat.py

```python
# In-memory history
class ChatSession:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []

    def add_turn(self, question, answer, sources):
        self.history.append({...})

# In ChatREPL
self.session = ChatSession()
```

### examples/chatmem/chat.py

```python
# No ChatSession class - uses OpenViking Session API directly

# In ChatREPL.__init
self.session_id = session_id
self.client = None
self.session = None

# In run()
self.client = SyncOpenViking(path=data_path, config=config)
self.client.initialize()
self.session = self.client.session(session_id=session_id)
self.session.load()

# Recording messages
self.session.add_message("user", [TextPart(question)])
self.session.add_message("assistant", [TextPart(answer)])

# On exit
self.session.commit()  # Extracts memories
```

## Storage Structure

### examples/chat/

```
examples/chat/
├── chat.py                 # All logic in memory
├── recipe.py -> ../query/recipe.py
├── data -> ../query/data   # Only RAG data
└── ov.conf
```

**No persistent storage** - Everything lost on exit.

### examples/chatmem/

```
examples/chatmem/
├── chat.py                 # Session API integration
├── recipe.py -> ../query/recipe.py
├── data/
│   ├── session/           # NEW: Session storage
│   │   ├── chat-interactive/
│   │   │   ├── messages.jsonl
│   │   │   ├── history/
│   │   │   │   └── archive_001/
│   │   │   └── .abstract.md
│   │   └── my-project/
│   │       └── ...
│   └── memory/            # NEW: Extracted memories
│       └── ...
└── ov.conf
```

**Persistent storage** - Everything saved on exit, loaded on startup.

## Performance

### Memory Usage

- **chat**: Lower - only current conversation in RAM
- **chatmem**: Higher - Session API + message history

### Startup Time

- **chat**: Faster - no session loading
- **chatmem**: Slightly slower - loads previous messages

### Exit Time

- **chat**: Instant - no persistence
- **chatmem**: ~1-2 seconds - commits session + extracts memories

## Migration Path

### From chat to chatmem

Already using `examples/chat/`? Easy migration:

```bash
# 1. Copy your config
cp examples/chat/ov.conf examples/chatmem/ov.conf

# 2. Start using chatmem
cd examples/chatmem
uv run chat.py

# 3. Your data/ is symlinked, so RAG data is shared
```

### From chatmem back to chat

Need to go back to stateless?

```bash
cd examples/chat
uv run chat.py
# Starts fresh, no session loading
```

## Real-World Examples

### Example 1: Quick Lookup (use chat/)

```bash
cd examples/chat
uv run chat.py
> What's the syntax for Python list comprehension?
[Gets answer]
> /exit
# Done - don't need to remember this
```

### Example 2: Project Work (use chatmem/)

```bash
cd examples/chatmem

# Day 1
uv run chat.py --session-id my-api-project
> Explain REST API design patterns
> What about authentication?
> /exit

# Day 2 - remembers Day 1!
uv run chat.py --session-id my-api-project
📝 Continuing from previous session: 2 turns, 4 messages
> How do I implement the JWT pattern you mentioned?
[Remembers previous context about authentication!]
```

## Conclusion

**Choose based on your use case:**

- **Ephemeral** → `examples/chat/`
- **Persistent** → `examples/chatmem/`

Both use the same RAG pipeline and UI, so the experience is similar. The difference is what happens when you exit.
