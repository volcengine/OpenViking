# Architecture Overview

OpenViking is a context database designed for AI Agents, unifying all context types (Memory, Resource, Skill) into a directory structure with semantic retrieval and progressive content loading.

## System Overview

<ArchitectureDiagram />

<details>
<summary>Text version</summary>

```text
CLI / SDK / HTTP client
          |
      HTTP Server
          |
      Service Layer
          |
   +------+------+----------------+
   |             |                |
Retrieval     Sessions       Resource / Skill import
   |             |                |
   |       Memory extraction  Parse / Semantic queues
   |             |                |
   +-------------+----------------+
                 |
             VikingFS
           /          \
      RAGFS         Vector index
```

</details>

## Core Modules

<div class="module-table">

| Module | Responsibility | Key Capabilities |
|--------|----------------|------------------|
| **Client** | Unified entry | Sends supported SDK/CLI operations to the HTTP API |
| **Service** | Business logic | FSService, SearchService, SessionService, ResourceService, PackService, DebugService |
| **Retrieve** | Context retrieval | Intent analysis (IntentAnalyzer), hierarchical retrieval (HierarchicalRetriever), Rerank |
| **Session** | Session management | Message recording, usage tracking, session archiving, triggering memory commit |
| **Parse** | Context extraction | Document parsing (PDF/MD/HTML), tree building (TreeBuilder), async semantic generation |
| **Memory** | Memory extraction | Schema-driven extraction by MemoryType (ExtractLoop), LLM merge and deduplication written back as patches (MemoryUpdater); orchestrated by `SessionCompressorV3` |
| **Storage** | Storage layer | VikingFS virtual filesystem, vector index, RAGFS integration |

</div>

## Service Layer

The Service layer decouples business logic from the transport layer. The CLI and SDKs reach it through the HTTP Server:

| Service | Responsibility | Key Methods |
|---------|----------------|-------------|
| **FSService** | File system operations | ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob |
| **SearchService** | Semantic search | search, find |
| **SessionService** | Session management | session, sessions, commit, delete |
| **ResourceService** | Resource import | add_resource, add_skill, wait_processed |
| **PackService** | Import/export and backup/restore | export_ovpack, import_ovpack, backup_ovpack, restore_ovpack |
| **DebugService** | Debug service | observer (ObserverService) |

## Dual-Layer Storage

OpenViking uses a dual-layer storage architecture separating content from index (see [Storage Architecture](./05-storage.md)):

| Layer | Responsibility | Content |
|-------|----------------|---------|
| **RAGFS** | Content storage | L0/L1/L2 full content, multimedia files |
| **Vector Index** | Index storage | URIs, vectors, metadata, and text used for retrieval, including abstracts |

## Data Flow Overview

### Adding Context

```
Input → Parser → TreeBuilder → RAGFS → SemanticQueue → Vector Index
```

1. **Parser**: Parse source documents into files and directories; model use depends on the selected parser
2. **TreeBuilder**: Move temp directory to RAGFS, enqueue for semantic processing
3. **SemanticQueue**: Async bottom-up L0/L1 generation
4. **Vector Index**: Build index for semantic search

### Retrieving Context

```
Query → Query Preparation (optional intent analysis) → Vector Retrieval (optional directory traversal and rerank) → Results
```

1. **Query Preparation**: `find()` uses the query directly; `search()` generates typed queries when intent analysis is enabled and session content exists
2. **QUICK retrieval**: `find`, image queries, and `search` without a reranker use vector retrieval without directory expansion
3. **THINKING retrieval**: Text `search` with a configured reranker traverses directories and reranks candidates
4. **Results**: Return contexts sorted by relevance

### Session Commit

```
Messages → Archive Boundary → Archive → Memory Extraction → Storage
```

1. **Messages**: Accumulate conversation messages and usage records
2. **Archive Boundary**: Split archived and retained messages according to the commit parameters; by default, archive all current messages
3. **Archive**: Generate L0/L1 for history segments
4. **Memory Extraction**: Extract memories from messages according to the memory policy and MemoryType schemas
5. **Storage**: Write to RAGFS + vector index

## Deployment Mode

### HTTP Mode

For team sharing, production deployment, and cross-language integration:

```python
# Python SDK connects to OpenViking Server
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
```

```bash
# Or use curl / any HTTP client
curl http://localhost:1933/api/v1/search/find \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "how to use openviking"}'
```

- Server runs as standalone process (`openviking-server`)
- Clients connect via HTTP API
- Supports any language that can make HTTP requests
- See [Server Deployment](../guides/03-deployment.md) for setup

## Design Principles

| Principle | Description |
|-----------|-------------|
| **Pure Storage Layer** | Storage only handles RAGFS operations and basic vector search; Rerank is in retrieval layer |
| **Three-Layer Information** | L0/L1/L2 enables progressive detail loading, saving token consumption |
| **Two-Stage Retrieval** | Vector retrieval supplies candidates; text search can traverse directories and rerank when configured |
| **Single Data Source** | RAGFS holds source files; vector records retain the text and metadata needed for retrieval |

## Related Documents

- [Context Types](./02-context-types.md) - Resource/Memory/Skill types
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Viking URI](./04-viking-uri.md) - Unified resource identifier
- [Storage Architecture](./05-storage.md) - Dual-layer storage details
- [Retrieval Mechanism](./07-retrieval.md) - Retrieval process details
- [Context Extraction](./06-extraction.md) - Parsing and extraction process
- [Session Management](./08-session.md) - Session and memory management
- [Transaction Model](./09-transaction.md) - Write and consistency model
- [Data Encryption](./10-encryption.md) - At-rest encryption and key architecture
- [Multi-Tenant](./11-multi-tenant.md) - Account, user, and peer isolation model
- [Metrics](./12-metrics.md) - `/metrics` usage and key metric explanations
- [Privacy Configs and Skill Privacy Extraction/Restore](./13-privacy.md) - Versioning, placeholder extraction, and read-time restore
