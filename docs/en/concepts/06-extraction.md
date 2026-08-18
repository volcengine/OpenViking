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

### Processing Limits

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_concurrent_llm` | 10 | Concurrent LLM calls |
| `max_images_per_call` | 10 | Max images per VLM call |
| `max_sections_per_call` | 20 | Max sections per VLM call |

## Code Skeleton Extraction

For code files, OpenViking uses a fixed skeleton extraction route. This route is built into the code summary pipeline and is not selected or tuned by per-language parser settings.

### What Skeleton Extraction Includes

The skeleton can include imports, classes, methods, functions, and other language-level symbols. Exact output depends on the maintained query or generic parser result for that language, but the route itself is fixed.

### Extraction Route

Code skeleton extraction follows this fixed order:

1. Use a maintained `tags.scm` query when one exists for the language.
2. If no corresponding `tags.scm` exists, use `tree-sitter-language-pack.process()`.
3. Invoke `semantic.code_summary` only as fallback when the extraction route produces no useful skeleton.

This routing applies to short and long code files alike.

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

# Flow: SessionCompressorV3 → ExtractLoop → MemoryUpdater → SemanticQueue
```

V3 has one extraction entry. It first extracts enabled user-memory schemas,
including `cases`. Trajectory, experience, and optional executable session-skill
training runs only when that extraction produces at least one case. A session
with no case therefore produces none of those execution-derived artifacts.

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Storage Architecture](./05-storage.md) - AGFS and vector index
- [Session Management](./08-session.md) - Memory extraction details
