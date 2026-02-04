# OpenViking Chat with Persistent Memory

Interactive chat interface with memory that persists across sessions using OpenViking's Session API.

## Features

- 🔄 **Multi-turn conversations** - Natural follow-up questions
- 💾 **Persistent memory** - Conversations saved and resumed
- ✨ **Memory extraction** - Automatic long-term memory creation
- 📚 **Source attribution** - See which documents informed answers
- ⌨️ **Command history** - Use ↑/↓ arrows to navigate
- 🎨 **Rich UI** - Beautiful terminal interface
- 🛡️ **Graceful exit** - Ctrl-C or /exit saves session

## Quick Start

```bash
# 0. Setup
cd examples/chatmem
uv sync

# 1. Configure (copy from query example or create new)
cp ../query/ov.conf ./ov.conf
# Edit ov.conf with your API keys

# 2. Start chatting
uv run chat.py
```

## How Memory Works

### Session Storage

Every conversation is saved with a session ID:
- **Default:** `chat-interactive`
- **Custom:** Use `--session-id my-project`

Sessions are stored in `data/session/{session-id}/`:
```
data/session/chat-interactive/
├── messages.jsonl          # All conversation messages
├── history/                # Archived message history
│   └── archive_001/        # Compressed archives
│       ├── messages.jsonl
│       ├── .abstract.md
│       └── .overview.md
└── .abstract.md            # Session summary
```

### Memory Extraction

When you exit (Ctrl-C or /exit), the session:
1. **Commits** current messages to storage
2. **Extracts** long-term memories from conversation
3. **Archives** older messages for compression
4. **Persists** everything to disk

### Resuming Sessions

Next time you run with the same session ID:
```bash
uv run chat.py --session-id my-project
```

You'll see:
```
📝 Continuing from previous session: 5 turns, 10 messages
```

The AI remembers your previous conversation context!

## Usage

### Basic Chat

```bash
uv run chat.py
```

**First run:**
```
🚀 OpenViking Chat with Memory

You: What is prompt engineering?
[Answer with sources]

You: /exit
💾 Saving session...
👋 Goodbye!
```

**Second run:**
```
📝 Continuing from previous session: 1 turns, 2 messages

You: Can you give me more examples?
[Remembers previous context!]
```

### Commands

- `/help` - Show available commands
- `/clear` - Clear screen (keeps memory)
- `/exit` or `/quit` - Save and exit
- `Ctrl-C` - Save and exit gracefully
- `Ctrl-D` - Exit

### Session Management

```bash
# Use default session
uv run chat.py

# Use project-specific session
uv run chat.py --session-id my-project

# Use date-based session
uv run chat.py --session-id $(date +%Y-%m-%d)
```

### Options

```bash
# Adjust creativity
uv run chat.py --temperature 0.9

# Use more context
uv run chat.py --top-k 10

# Stricter relevance
uv run chat.py --score-threshold 0.3

# All options
uv run chat.py --help
```

### Debug Mode

```bash
OV_DEBUG=1 uv run chat.py
```

## Configuration

Edit `ov.conf`:

```json
{
  "embedding": {
    "provider": "volcengine",
    "model": "doubao-embedding",
    "api_key": "your-key"
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-pro-32k",
    "api_key": "your-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

## Architecture

### Components

- **ChatREPL** - Interactive interface with command handling
- **OpenViking Session** - Persistent conversation memory
- **Recipe** - RAG pipeline (from query example)
- **TextPart** - Message content wrapper

### Memory Flow

```
User Input
    ↓
session.add_message("user", [TextPart(question)])
    ↓
Recipe.query() → LLM Response
    ↓
session.add_message("assistant", [TextPart(answer)])
    ↓
Display Answer + Sources
    ↓
On Exit: session.commit()
    ↓
Memories Extracted & Persisted
```

## Comparison with examples/chat/

| Feature | examples/chat/ | examples/chatmem/ |
|---------|---------------|-------------------|
| Multi-turn | ✅ | ✅ |
| Persistent memory | ❌ | ✅ |
| Memory extraction | {❌ | ✅ |
| Session management | ❌ | ✅ |
| Cross-run memory | ❌ | ✅ |

Use `examples/chat/` for:
- Quick one-off conversations
- Testing without persistence
- Simple prototyping

Use `examples/chatmem/` for:
- Long-term projects
- Conversations spanning multiple sessions
- Building up knowledge base over time

## Tips

- **Organize by project:** Use `--session-id project-name` for different contexts
- **Date-based sessions:** `--session-id $(date +%Y-%m-%d)` for daily logs
- **Clear screen, keep memory:** Use `/clear` to clean display without losing history
- **Check session files:** Look in `data/session/` to see what's stored

## Troubleshooting

**"Error initializing"**
- Check `ov.conf` has valid API keys
- Ensure `data/` directory is writable

**"No relevant sources found"**
- Add documents using `../query/add.py`
- Lower `--score-threshold` value
- Try rephrasing your question

**Session not loading**
- Verify session ID matches previous run
- Check `data/session/{session-id}/` exists
- Look for `messages.jsonl` in session directory

**High memory usage**
- Sessions accumulate messages - use different session IDs for different topics
- Check `data/session/` directory size
- Old sessions can be deleted if not needed

## Advanced

### List All Sessions

```bash
ls data/session/
```

### View Session Messages

```bash
cat data/session/chat-interactive/messages.jsonl
```

### Check Extracted Memories

```bash
# Look in memory storage
ls data/memory/
```

### Backup Sessions

```bash
tar -czf sessions-backup-$(date +%Y%m%d).tar.gz data/session/
```

## Next Steps

- Build on this for domain-specific assistants
- Add session search to find relevant past conversations
- Implement session export/import for sharing
- Create session analytics dashboards
