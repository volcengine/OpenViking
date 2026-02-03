# OpenViking Chat Application Design

**Date:** 2026-02-01
**Status:** Approved
**Target:** Hackathon MVP - Win the coding champion!

## Overview

A client-server chat application leveraging OpenViking's VLM and context management capabilities. Features automatic RAG (Retrieval-Augmented Generation) for intelligent, context-aware conversations with session persistence.

## Architecture

### System Components

**Server (Port 8391)**
- FastAPI HTTP server handling chat requests
- OpenViking integration for context management and RAG
- Session management with auto-commit on client disconnect
- Stateful - maintains active session per connection
- RESTful API: `/chat`, `/session/new`, `/session/close`, `/health`

**Client (CLI)**
- Interactive REPL using `prompt_toolkit`
- HTTP client communicating with server
- Rich terminal UI using `rich` library
- Commands: `/exit`, `/clear`, `/history`, `/new`
- Displays messages, sources, and system notifications

**Communication Protocol**
- JSON over HTTP for simplicity
- Request: `{"message": str, "session_id": str}`
- Response: `{"response": str, "sources": [...], "session_id": str}`

### Data Flow

1. User types message in REPL
2. Client sends HTTP POST to server
3. Server uses OpenViking to search context (automatic RAG)
4. Server calls VLM with user message + retrieved context
5. Server returns response with sources
6. Client displays formatted response
7. On `/exit`, client calls `/session/close`, server commits session

## Server Components

### server/main.py
- FastAPI application (async support for OpenViking)
- Endpoints: `/chat`, `/session/new`, `/session/close`, `/health`
- Global OpenViking client initialized on startup
- Session manager tracks active sessions
- Graceful shutdown commits all active sessions

### server/chat_engine.py
Core logic for RAG + VLM generation:
- `ChatEngine` class wraps OpenViking client
- `process_message(message, session)` method:
  1. Search for relevant context using `client.search()`
  2. Format context + message into VLM prompt
  3. Call VLM via OpenViking's VLM integration
  4. Return response + source citations
- Configurable: top_k, temperature, score_threshold
- Reuses pattern from examples/query.py

### server/session_manager.py
- `SessionManager` class maintains active sessions
- Maps session_id → OpenViking Session object
- `get_or_create(session_id)` - lazy session creation
- `commit_session(session_id)` - calls `session.commit()`
- `commit_all()` - cleanup on server shutdown
- Thread-safe (though single-user mode keeps it simple)

### Configuration
- Loads from `workspace/ov.conf` (credentials)
- Environment variables: `OV_PORT=8391`, `OV_DATA_PATH`, `OV_CONFIG_PATH`
- Default data path: `workspace/opencode/data/`

## Client Components

### client/main.py
- Entry point for CLI application
- Argument parsing: `--server` (default: `http://localhost:8391`)
- Initializes REPL and starts interaction loop
- Handles Ctrl+C gracefully (commits session before exit)
- Simple orchestration code (~50 lines)

### client/repl.py
`ChatREPL` class with rich terminal experience:
- Multi-line input support (Shift+Enter for newlines)
- Command history (saved to `~/.openviking_chat_history`)
- Auto-completion for commands
- Syntax highlighting for commands
- Display using `rich` library:
  - User messages in yellow panel
  - Assistant responses in cyan panel
  - Sources table (like query.py)
  - Spinners during processing

**Command Handlers:**
- `/exit` - close session and quit
- `/clear` - clear screen
- `/new` - start new session (commits current)
- `/history` - show recent messages

### client/http_client.py
- Thin wrapper around `httpx` for server communication
- Methods: `send_message()`, `new_session()`, `close_session()`
- Retry logic with exponential backoff
- Connection error handling with user-friendly messages

## OpenViking Integration

### Initialization (server startup)
```python
client = ov.OpenViking(path="./workspace/opencode/data")
client.initialize()
```

### Session Management
```python
# Create or load session
session = client.session(session_id)
```

### RAG Pipeline (per message)
```python
# 1. Search for context
results = client.search(
    query=user_message,
    session=session,
    limit=5,
    score_threshold=0.2
)

# 2. Build context from results
context_docs = [
    {"uri": r.uri, "content": r.content, "score": r.score}
    for r in results.resources
]

# 3. Track conversation
session.add_message(role="user", content=user_message)

# 4. Generate response using VLM
response = generate_with_vlm(
    messages=session.get_messages(),
    context=context_docs
)

session.add_message(role="assistant", content=response)
```

### Commit on Exit
```python
# When user exits
session.commit()  # Archives messages, extracts memories
client.close()    # Cleanup
```

## Error Handling

### Server Errors

**OpenViking Initialization**
- Check config file exists and is valid on startup
- Fail fast with clear error if VLM/embedding not accessible
- Return 503 if OpenViking not initialized

**Search/RAG Failures**
- No results: proceed with VLM using only conversation context
- VLM call fails: return error with retry suggestion
- Log all errors for debugging

**Session Commit Failures**
- Log errors but don't crash server
- Return success to client (user experience priority)
- Background retry for failed commits

### Client Errors

**Connection Failures**
- Check server health on startup
- Display friendly error message
- Retry with exponential backoff (3 attempts)

**Message Send Failures**
- Show error panel
- Keep message in input buffer for retry
- Don't clear user's typed message

**Edge Cases**
- Empty messages: prompt user
- Very long messages: warn if >4000 chars
- Server shutdown: save session_id for resume

## Testing Strategy

### Unit Tests
- `tests/server/test_chat_engine.py` - Mock OpenViking, test RAG
- `tests/server/test_session_manager.py` - Session lifecycle
- `tests/client/test_repl.py` - Command parsing, display
- `tests/shared/test_protocol.py` - Message serialization

### Integration Tests
- `tests/integration/test_end_to_end.py` - Full flow
- Mock VLM responses for deterministic testing
- Test session commit and retrieval

### Manual Testing
- Use `./workspace/ov.conf` for real VLM
- Add sample documents to test RAG
- Multi-turn conversations

## Project Structure

```
workspace/opencode/
├── server/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── chat_engine.py       # RAG + VLM logic
│   └── session_manager.py   # Session lifecycle
├── client/
│   ├── __init__.py
│   ├── main.py              # CLI entry point
│   ├── repl.py              # Interactive REPL
│   └── http_client.py       # Server communication
├── shared/
│   ├── __init__.py
│   ├── protocol.py          # Message format
│   └── config.py            # Configuration
├── tests/
│   ├── server/
│   ├── client/
│   ├── shared/
│   └── integration/
├── data/                     # OpenViking data directory
├── README.md
├── requirements.txt
└── pyproject.toml
```

## Implementation Phases

### Phase 1: Foundation
- Setup project structure in `workspace/opencode/`
- Implement `shared/protocol.py` and `shared/config.py`
- Basic server skeleton with health endpoint

### Phase 2: Server Core
- Implement `chat_engine.py` with OpenViking integration
- Implement `session_manager.py`
- Complete server endpoints (`/chat`, `/session/new`, `/session/close`)

### Phase 3: Client
- Implement REPL with `prompt_toolkit`
- HTTP client with retry logic
- Rich terminal UI with panels and tables

### Phase 4: Integration & Testing
- End-to-end testing
- Bug fixes and refinement
- Documentation (README with usage examples)

## Design Decisions

### Single-user Mode
- Simpler implementation for MVP
- Can scale to multi-user later
- Focus on core functionality first

### Auto-commit on Exit
- Clean and automatic
- No manual intervention needed
- User-friendly

### Automatic RAG
- Every query searches context
- Leverages OpenViking's strengths
- More intelligent responses

### Modular Structure
- Clear component boundaries
- Easy to assign to different agents
- Facilitates parallel development

## Success Criteria

1. ✅ Client connects to server successfully
2. ✅ User can send messages and receive responses
3. ✅ Responses include relevant context from past sessions
4. ✅ Sessions are committed and memories extracted on exit
5. ✅ Clean, intuitive CLI interface
6. ✅ Error handling provides helpful feedback
7. ✅ Code is clean, well-organized, and documented

---

**Ready for implementation!** 🚀
