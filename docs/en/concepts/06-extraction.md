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
| Code | CodeRepositoryParser | .py, .js, .go, etc. | Respects `.gitignore` and ignores common non-code directories |
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
    scope="resources",  # resources/user
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

## Code Skeleton Extraction

For code files, OpenViking uses tree-sitter-based skeleton extraction as a lightweight alternative to LLM summarization.

### Modes

Controlled by `code_summary_mode` in `ov.conf` (see [Configuration](../guides/01-configuration.md#code)):

| Mode | Description |
|------|-------------|
| `"ast"` | Extract a compact structural skeleton (**default**) |
| `"llm"` | Always use LLM summarization |
| `"ast_llm"` | Extract a more detailed skeleton; it does not add a second LLM call |

### Fallback Behavior

The default `auto` provider follows this order:

1. Use a maintained `tags.scm` query when one exists for the language.
2. Otherwise, or when that query produces no useful result, use `tree-sitter-language-pack.process()`.
3. Fall back to `semantic.code_summary` only when neither extractor produces a useful skeleton.

This routing applies to short and long code files alike. Set `code_skeleton_provider` only when a specific provider is needed for compatibility or evaluation.

## Three Context Types Extraction

### Flow Comparison

| Phase | Resource | Memory | Skill |
|-------|----------|--------|-------|
| **Parser** | Common flow | Common flow | Common flow |
| **Base URI** | `viking://resources` | `viking://user/memories` | `viking://user/skills` |
| **TreeBuilder scope** | resources | user | user |
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

# Flow: Direct write to viking://user/skills/{name}/ → SemanticQueue
```

### Memory Extraction

```python
# Memory auto-extracted from session
await session.commit()

# Flow: SessionCompressorV2 → ExtractLoop → MemoryUpdater → SemanticQueue
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Storage Architecture](./05-storage.md) - AGFS and vector index
- [Session Management](./08-session.md) - Memory extraction details
