# Architecture Overview

OpenViking is a context database designed for AI Agents, unifying all context types (Memory, Resource, Skill) into a directory structure with semantic retrieval and progressive content loading.

## System Overview

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        OpenViking System Architecture                       │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│                              ┌─────────────┐                               │
│                              │   Client    │                               │
│                              │ (OpenViking)│                               │
│                              └──────┬──────┘                               │
│                                     │ delegates                            │
│                              ┌──────▼──────┐                               │
│                              │   Service   │                               │
│                              │    Layer    │                               │
│                              └──────┬──────┘                               │
│                                     │                                      │
│           ┌─────────────────────────┼─────────────────────────┐            │
│           │                         │                         │            │
│           ▼                         ▼                         ▼            │
│    ┌─────────────┐          ┌─────────────┐          ┌─────────────┐      │
│    │  Retrieve   │          │   Session   │          │    Parse    │      │
│    │  (Context   │          │  (Session   │          │  (Context   │      │
│    │  Retrieval) │          │  Management)│          │  Extraction)│      │
│    │ search/find │          │ add/used    │          │ Doc parsing │      │
│    │ Intent      │          │ compress    │          │ L0/L1/L2    │      │
│    │ Rerank      │          │ commit      │          │ Tree build  │      │
│    └──────┬──────┘          └──────┬──────┘          └──────┬──────┘      │
│           │                        │                        │             │
│           │                        │ Memory extraction      │             │
│           │                        ▼                        │             │
│           │                 ┌─────────────┐                 │             │
│           │                 │ Compressor  │                 │             │
│           │                 │ Compress/   │                 │             │
│           │                 │ Deduplicate │                 │             │
│           │                 └──────┬──────┘                 │             │
│           │                        │                        │             │
│           └────────────────────────┼────────────────────────┘             │
│                                    ▼                                      │
│    ┌─────────────────────────────────────────────────────────────────┐    │
│    │                         Storage Layer                            │    │
│    │               AGFS (File Content)  +  Vector Index               │    │
│    └─────────────────────────────────────────────────────────────────┘    │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## Core Modules

| Module | Responsibility | Key Capabilities |
|--------|----------------|------------------|
| **Client** | Unified entry | Provides all operation interfaces, delegates to Service layer |
| **Service** | Business logic | FSService, SearchService, SessionService, ResourceService, RelationService, PackService, DebugService |
| **Retrieve** | Context retrieval | Intent analysis (IntentAnalyzer), hierarchical retrieval (HierarchicalRetriever), Rerank |
| **Session** | Session management | Message recording, usage tracking, session compression, memory commit |
| **Parse** | Context extraction | Document parsing (PDF/MD/HTML), tree building (TreeBuilder), async semantic generation |
| **Compressor** | Memory compression | 6-category memory extraction, LLM deduplication decisions |
| **Storage** | Storage layer | VikingFS virtual filesystem, vector index, AGFS integration |

## Service Layer

The Service layer decouples business logic from the transport layer, enabling reuse across HTTP Server and CLI:

| Service | Responsibility | Key Methods |
|---------|----------------|-------------|
| **FSService** | File system operations | ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob |
| **SearchService** | Semantic search | search, find |
| **SessionService** | Session management | session, sessions, compress, extract, delete |
| **ResourceService** | Resource import | add_resource, add_skill, wait_processed |
| **RelationService** | Relation management | relations, link, unlink |
| **PackService** | Import/export | export_ovpack, import_ovpack |
| **DebugService** | Debug service | get_system_status, get_queue_status, get_vikingdb_status, get_vlm_status, is_healthy |

## Dual-Layer Storage

OpenViking uses a dual-layer storage architecture separating content from index (see [Storage Architecture](./05-storage.md)):

| Layer | Responsibility | Content |
|-------|----------------|---------|
| **AGFS** | Content storage | L0/L1/L2 full content, multimedia files, relations |
| **Vector Index** | Index storage | URIs, vectors, metadata (no file content) |

## Data Flow Overview

### Adding Context

```
Input → Parser → TreeBuilder → AGFS → SemanticQueue → Vector Index
```

1. **Parser**: Parse documents, create file and directory structure (no LLM calls)
2. **TreeBuilder**: Move temp directory to AGFS, enqueue for semantic processing
3. **SemanticQueue**: Async bottom-up L0/L1 generation
4. **Vector Index**: Build index for semantic search

### Retrieving Context

```
Query → Intent Analysis → Hierarchical Retrieval → Rerank → Results
```

1. **Intent Analysis**: Analyze query intent, generate 0-5 typed queries
2. **Hierarchical Retrieval**: Directory-level recursive search using priority queue
3. **Rerank**: Scalar filtering + model reranking
4. **Results**: Return contexts sorted by relevance

### Session Commit

```
Messages → Compress → Archive → Memory Extraction → Storage
```

1. **Messages**: Accumulate conversation messages and usage records
2. **Compress**: Keep recent N rounds, archive older messages
3. **Archive**: Generate L0/L1 for history segments
4. **Memory Extraction**: Extract 6-category memories from messages
5. **Storage**: Write to AGFS + vector index

## Deployment Modes

### Embedded Mode

For local development and single-process applications:

```python
client = OpenViking(path="./data")
```

- Auto-starts AGFS subprocess
- Uses local vector index
- Singleton pattern

### Service Mode

For production distributed deployment:

```python
client = OpenViking(
    vectordb_url="http://vectordb:5000",
    agfs_url="http://agfs:8080"
)
```

- Connects to remote services
- Supports multiple concurrent instances
- Independently scalable

## Design Principles

| Principle | Description |
|-----------|-------------|
| **Pure Storage Layer** | Storage only handles AGFS operations and basic vector search; Rerank is in retrieval layer |
| **Three-Layer Information** | L0/L1/L2 enables progressive detail loading, saving token consumption |
| **Two-Stage Retrieval** | Vector search recalls candidates + Rerank improves accuracy |
| **Single Data Source** | All content read from AGFS; vector index only stores references |

## Related Documents

- [Context Types](./02-context-types.md) - Resource/Memory/Skill types
- [Context Layers](./04-context-layers.md) - L0/L1/L2 model
- [Viking URI](./03-viking-uri.md) - Unified resource identifier
- [Storage Architecture](./05-storage.md) - Dual-layer storage details
- [Retrieval Mechanism](./06-retrieval.md) - Retrieval process details
- [Context Extraction](./07-extraction.md) - Parsing and extraction process
- [Session Management](./08-session.md) - Session and memory management
