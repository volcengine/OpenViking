# Context Extraction

OpenViking uses a three-layer async architecture for document parsing and context extraction.

## Overview

```
Input File → Parser → TreeBuilder → SemanticQueue → Vector Index
              ↓           ↓              ↓
          Parse &     Move Files     L0/L1 Generation
          Convert     Queue Semantic  (LLM Async)
          (No LLM)
```

**Design Principle**: Parsing and semantics are separated. Parser doesn't call LLM; semantic generation is async.

## Parser

Parser handles document format conversion and structuring, creating file structure in temp directory.

### Supported Formats

| Format | Parser | Extensions | Status |
|--------|--------|------------|--------|
| Markdown | MarkdownParser | .md, .markdown | Supported |
| Plain text | TextParser | .txt | Supported |
| PDF | PDFParser | .pdf | Supported |
| HTML | HTMLParser | .html, .htm | Supported |
| Code | CodeRepositoryParser | .py, .js, .go, etc. |  |
| Image | ImageParser | .png, .jpg, etc. |  |
| Video | VideoParser | .mp4, .avi, etc. |  |
| Audio | AudioParser | .mp3, .wav, etc. |  |

### Core Flow (Document Example)

```python
# 1. Parse file
parse_result = registry.parse("/path/to/doc.md")

# 2. Returns temp directory URI
parse_result.temp_dir_path  # viking://temp/abc123
```

### Smart Splitting

```
If document_tokens <= 1024:
    → Save as single file
Else:
    → Split by headers
    → Section < 512 tokens → Merge
    → Section > 1024 tokens → Create subdirectory
```

### Return Result

```python
ParseResult(
    temp_dir_path: str,    # Temp directory URI
    source_format: str,    # pdf/markdown/html
    parser_name: str,      # Parser name
    parse_time: float,     # Duration (seconds)
    meta: Dict,            # Metadata
)
```

## TreeBuilder

TreeBuilder moves temp directory to AGFS and queues semantic processing.

### Core Flow

```python
building_tree = tree_builder.finalize_from_temp(
    temp_dir_path="viking://temp/abc123",
    scope="resources",  # resources/user/agent
)
```

### 5-Phase Processing

1. **Find document root**: Ensure exactly 1 subdirectory in temp
2. **Determine target URI**: Map base URI by scope
3. **Recursively move directory tree**: Copy all files to AGFS
4. **Clean up temp directory**: Delete temp files
5. **Queue semantic generation**: Submit SemanticMsg to queue

### URI Mapping

| scope | Base URI |
|-------|----------|
| resources | `viking://resources` |
| user | `viking://user` |
| agent | `viking://agent` |

## SemanticQueue

SemanticQueue handles async L0/L1 generation and vectorization.

### Message Structure

```python
SemanticMsg(
    id: str,           # UUID
    uri: str,          # Directory URI
    context_type: str, # resource/memory/skill
    status: str,       # pending/processing/completed
)
```

### Processing Flow (Bottom-up)

```
Leaf directories → Parent directories → Root
```

### Single Directory Processing Steps

1. **Concurrent file summary generation**: Limited to 10 concurrent
2. **Collect child directory abstracts**: Read generated .abstract.md
3. **Generate .overview.md**: LLM generates L1 overview
4. **Extract .abstract.md**: Extract L0 from overview
5. **Write files**: Save to AGFS
6. **Vectorize**: Create Context and queue to EmbeddingQueue

### Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_concurrent_llm` | 10 | Concurrent LLM calls |
| `max_images_per_call` | 10 | Max images per VLM call |
| `max_sections_per_call` | 20 | Max sections per VLM call |

## Code Skeleton Extraction (AST Mode)

For code files, OpenViking supports AST-based skeleton extraction via tree-sitter as a lightweight alternative to LLM summarization, significantly reducing processing cost.

### Modes

Controlled by `code_summary_mode` in `ov.conf` (see [Configuration](../guides/01-configuration.md#code)):

| Mode | Description |
|------|-------------|
| `"ast"` | Extract structural skeleton for files ≥100 lines, skip LLM calls (**default**) |
| `"llm"` | Always use LLM for summarization (original behavior) |
| `"ast_llm"` | Extract AST skeleton first, then pass it as context to LLM for summarization |

### What AST Extracts

The skeleton includes:

- Module-level docstring (first line)
- Import statement list
- Class names, base classes, and method signatures (`ast` mode: first-line docstrings only; `ast_llm` mode: full docstrings)
- Top-level function signatures

### Supported Languages

The following languages have dedicated extractors built on tree-sitter:

| Language | Status |
|----------|--------|
| Python | Supported |
| JavaScript / TypeScript | Supported |
| Rust | Supported |
| Go | Supported |
| Java | Supported |
| C / C++ | Supported |

Other languages automatically fall back to LLM.

### Fallback Behavior

The following conditions trigger automatic fallback to LLM, with the reason logged. The overall pipeline is unaffected:

- Language not in the supported list
- File has fewer than 100 lines
- AST parse error
- Extraction produces an empty skeleton

### File Structure

```
openviking/parse/parsers/code/ast/
├── extractor.py      # Language detection and dispatch
├── skeleton.py       # CodeSkeleton / FunctionSig / ClassSkeleton data structures
└── languages/        # Per-language extractors
```

## Three Context Types Extraction

### Flow Comparison

| Phase | Resource | Memory | Skill |
|-------|----------|--------|-------|
| **Parser** | Common flow | Common flow | Common flow |
| **Base URI** | `viking://resources` | `viking://user/memories` | `viking://agent/skills` |
| **TreeBuilder scope** | resources | user/agent | agent |
| **SemanticMsg type** | resource | memory | skill |

### Resource Extraction

```python
# Add resource
await client.add_resource(
    "/path/to/doc.pdf",
    reason="API documentation"
)

# Flow: Parser → TreeBuilder(scope=resources) → SemanticQueue
```

### Skill Extraction

```python
# Add skill
await client.add_skill({
    "name": "search-web",
    "content": "# search-web\\n..."
})

# Flow: Direct write to viking://agent/skills/{name}/ → SemanticQueue
```

### Memory Extraction

```python
# Memory auto-extracted from session
await session.commit()

# Flow: MemoryExtractor → TreeBuilder(scope=user) → SemanticQueue
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Storage Architecture](./05-storage.md) - AGFS and vector index
- [Session Management](./08-session.md) - Memory extraction details
