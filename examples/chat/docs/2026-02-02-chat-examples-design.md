# Chat Examples Design

**Date:** 2026-02-02
**Status:** Approved

## Overview

Create two chat examples building on the existing `query` example:
1. **Phase 1:** `examples/chat/` - Multi-turn chat interface (no persistence)
2. **Phase 2:** `examples/chatmem/` - Chat with session memory using OpenViking Session API

## Architecture

### Phase 1: Multi-turn Chat (`examples/chat/`)

**Purpose:** Interactive REPL for multi-turn conversations within a single run.

**Core Components:**
- `ChatSession` - In-memory conversation history
- `ChatREPL` - Interactive interface using Rich TUI
- `Recipe` - Reused from query example (symlink)

**Directory Structure:**
```
examples/chat/
├── chat.py                      # Main REPL interface
├── recipe.py -> ../query/recipe.py
├── boring_logging_config.py -> ../query/boring_logging_config.py
├── ov.conf                      # Config file
├── data -> ../query/data        # Symlink to query data
├── pyproject.toml              # Dependencies
└── README.md                    # Usage instructions
```

### Phase 2: Chat with Memory (`examples/chatmem/`)

**Purpose:** Multi-turn chat with persistent memory using OpenViking Session API.

**Additional Features:**
- Session creation and loading
- Message recording (user + assistant)
- Commit on exit (normal or Ctrl-C)
- Memory verification on restart

**To be designed in detail after Phase 1 completion.**

## Phase 1: Detailed Design

### 1. ChatSession Class

**Responsibilities:**
- Store conversation history in memory
- Manage Q&A turns
- Display conversation history

**Interface:**
```python
class ChatSession:
    def __init__(self):
        self.history: List[Dict] = []

    def add_turn(self, question: str, answer: str, sources: List[Dict]):
        """Add a Q&A turn to history"""

    def clear(self):
        """Clear conversation history"""

    def display_history(self):
        """Display conversation history using Rich"""
```

### 2. ChatREPL Class

**Responsibilities:**
- Main REPL loop
- Command handling
- User input processing
- Question/answer display

**Interface:**
```python
class ChatREPL:
    def __init__(self, config_path: str, data_path: str, **kwargs):
        self.recipe = Recipe(config_path, data_path)
        self.session = ChatSession()

    def run(self):
        """Main REPL loop"""

    def handle_command(self, cmd: str) -> bool:
        """Handle commands, return True if should exit"""

    def ask_question(self, question: str):
        """Query and display answer"""
```

### 3. REPL Flow

```
1. Display welcome banner
2. Initialize ChatSession (empty)
3. Loop:
   - Show prompt: "You: "
   - Get user input using readline
   - If empty: continue
   - If command (/exit, /quit, /clear, /help): handle_command()
   - If question: ask_question()
     - Call recipe.query()
     - Display answer with sources
     - Add to session.history
   - Continue loop
4. On exit:
   - Display goodbye message
   - Clean up resources
```

### 4. User Interface

**Display Layout:**
```
┌─ OpenViking Chat ─────────────────────────────┐
│ Type your question or /help for commands      │
└───────────────────────────────────────────────┘

[Conversation history shown above]

You: What is prompt engineering?

[Spinner: "Wait a sec..."]

┌─ Answer ──────────────────────────────────────┐
│ Prompt engineering is...                      │
└───────────────────────────────────────────────┘

┌─ Sources (3 documents) ───────────────────────┐
│ # │ File          │ Relevance │               │
│ 1 │ prompts.md    │ 0.8234    │               │
└───────────────────────────────────────────────┘

You: [cursor]
```

**Commands:**
- `/exit` or `/quit` - Exit chat
- `/clear` - Clear screen (but keep history)
- `/help` - Show available commands
- `Ctrl-C` - Graceful exit with goodbye message
- `Ctrl-D` - Exit

### 5. Implementation Notes

**Dependencies:**
- `rich` - TUI components (already in query example)
- `readline` - Input history (arrow keys)
- Built-in `signal` - Ctrl-C handling

**Key Features:**
- Reuse Recipe class from query (symlink)
- In-memory history only (no persistence)
- Readline for command history (up/down arrows)
- Signal handling for graceful Ctrl-C
- Rich console for beautiful output
- Simple and clean - focus on multi-turn UX

**Symlinks:**
```bash
cd examples/chat
ln -s ../query/recipe.py recipe.py
ln -s ../query/boring_logging_config.py boring_logging_config.py
ln -s ../query/data data
```

**Configuration:**
- Copy `ov.conf` from query example
- Same LLM and embedding settings
- Reuse existing data directory

## Testing Plan

### Phase 1 Testing:
1. **Basic REPL:**
   - Start chat
   - Ask single question
   - Verify answer displayed
   - Exit with /exit

2. **Multi-turn:**
   - Ask multiple questions
   - Verify history accumulates
   - Check context still works

3. **Commands:**
   - Test /help, /clear, /exit, /quit
   - Test Ctrl-C (graceful exit)
   - Test Ctrl-D

4. **Edge cases:**
   - Empty input
   - Very long questions
   - No search results

### Phase 2 Testing (Future):
1. Session creation and loading
2. Message persistence
3. Commit on exit
4. Memory verification on restart

## Success Criteria

### Phase 1:
- [x] Design approved
- [ ] Chat example created
- [ ] Multi-turn conversation works
- [ ] Commands functional
- [ ] Graceful exit handling
- [ ] README with usage examples

### Phase 2:
- [ ] Session integration designed
- [ ] Memory persistence works
- [ ] Commit on exit implemented
- [ ] Memory verification tested

## Next Steps

1. Create `examples/chat/` directory structure
2. Implement ChatSession and ChatREPL
3. Test multi-turn functionality
4. Document usage
5. Verify and handoff to next agent for Phase 2

## Notes

- Keep Phase 1 simple - no persistence
- Focus on UX for multi-turn chat
- Reuse existing components where possible
- Session API integration deferred to Phase 2
