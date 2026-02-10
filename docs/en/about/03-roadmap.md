# Roadmap

This document outlines the development roadmap for OpenViking.

## Completed Features

### Core Infrastructure
- Three-layer information model (L0/L1/L2)
- Viking URI addressing system
- Dual-layer storage (AGFS + Vector Index)
- Async/Sync client support

### Resource Management
- Text resource management (Markdown, HTML, PDF)
- Automatic L0/L1 generation
- Semantic search with vector indexing
- Resource relations and linking

### Retrieval
- Basic semantic search (`find`)
- Context-aware search with intent analysis (`search`)
- Session-based query expansion
- Reranking pipeline

### Session Management
- Conversation state tracking
- Context and skill usage tracking
- Automatic memory extraction
- Memory deduplication with LLM
- Session archiving and compression

### Skills
- Skill definition and storage
- MCP tool auto-conversion
- Skill search and retrieval

### Configuration
- Pluggable embedding providers
- Pluggable LLM providers
- YAML-based configuration

### Server & Client Architecture
- HTTP Server (FastAPI)
- Python HTTP Client
- API Key authentication
- Client abstraction layer (LocalClient / HTTPClient)

---

## Future Plans

### CLI
- Complete command-line interface for all operations
- Distributed storage backend

### Multi-modal Support
- Intelligent parsing and access for multi-modal resources (images, video, audio, etc.)
- Directory storage structure for multi-modal resources

### Context Management
- Propagation updates when context is modified
- Version management and rollback for context (git-like)

### Resource Node Access Control
- Multi-Agent / Multi-User support
- Role-based isolation design
- Access control and permission design for resource directory nodes

### Ecosystem Integration
- Popular Agent framework adapters
- Plugin system for custom components

We welcome suggestions and feedback in issues.

---

## Contributing

We welcome contributions to help achieve these goals. See [Contributing](contributing.md) for guidelines.
