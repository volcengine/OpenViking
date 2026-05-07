# Roadmap

This document outlines the development roadmap for OpenViking.

## Completed Features

### Core Infrastructure
- Three-layer information model (L0/L1/L2)
- Viking URI addressing system
- Dual-layer storage (AGFS + Vector Index)
- Async/Sync client support
- QueueFS with SQLite backend

### Resource Management
- Text resource management (Markdown, HTML, PDF)
- Automatic L0/L1 generation
- Semantic search with vector indexing
- Resource relations and linking
- Content write API
- Agent namespace management

### Multi-modal Parsing
- Image OCR and parsing
- Audio transcription (Whisper ASR)
- Video parsing
- PDF with bookmark extraction
- Word, PowerPoint, Excel, EPub, ZIP parsers
- Code file parsing
- Feishu/Lark document parser

### Retrieval
- Basic semantic search (`find`)
- Context-aware search with intent analysis (`search`)
- Session-based query expansion
- Reranking pipeline with multiple providers (OpenAI, LiteLLM, Cohere, Volcengine)

### Session & Memory
- Conversation state tracking
- Context and skill usage tracking
- Automatic memory extraction
- Memory deduplication with LLM
- Session archiving and compression
- Working Memory V2 with cold-storage archival

### Skills
- Skill definition and storage
- MCP tool auto-conversion
- Skill search and retrieval

### Multi-tenant & Security
- Multi-tenant support with account isolation
- File and document encryption
- User-level privacy configs API
- API Key authentication

### Configuration & Providers
- Pluggable embedding providers (OpenAI, Gemini, Volcengine, MiniMax, LiteLLM, Jina, Cohere, DashScope, Voyage, local)
- Pluggable LLM providers
- Pluggable rerank providers
- YAML-based configuration
- Setup wizard (`openviking-server init`)

### Server & Client Architecture
- HTTP Server (FastAPI)
- Native MCP endpoint built into openviking-server
- Python HTTP Client
- Client abstraction layer (LocalClient / HTTPClient)
- Web Console

### CLI
- Rust CLI (`ov` command)
- TUI filesystem navigator
- Privacy, search, session, resource, and admin commands

### Bot Integration
- VikingBot framework
- Feishu/Lark channel
- Telegram channel

### Ecosystem & Plugins
- OpenClaw plugin (context engine for coding agents)
- Claude Code memory plugin
- Codex memory plugin

### Observability
- Prometheus metrics
- OpenTelemetry tracing
- HTTP observability middleware

### Deployment
- Docker image and Docker Compose
- Helm Chart for Kubernetes
- Cloud VikingDB support

---

## Future Plans

### Context Management
- Propagation updates when context is modified
- Version management and rollback for context (git-like)

### Distributed Storage
- Distributed storage backend

### Ecosystem
- Additional Agent framework adapters

We welcome suggestions and feedback in issues.

---

## Contributing

We welcome contributions to help achieve these goals. See [Contributing](contributing.md) for guidelines.
