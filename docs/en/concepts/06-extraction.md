# Context Extraction

Resource ingestion proceeds through parsing, target-path resolution, content persistence, semantic generation, and embedding. Memories and skills have their own entry points and also use semantic processing and vector indexes.

## Overview

```text
Input → Parser → TreeBuilder → Content persistence → SemanticQueue → EmbeddingQueue → Vector index
        Artifacts  Target URI                        L0/L1 generation
```

Parsing and directory-summary generation are separate stages. Individual parsers may call models or external parsing services, so parsing is not universally LLM-free. Import APIs normally return a task ID before processing finishes; wait for that task before dependent searches. See [Task Management](../api/17-tasks.md).

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
| Image | ImageParser | .png, .jpg, etc. | Supported; depends on media configuration |
| Video | VideoParser | .mp4, .avi, .mov, .mkv, .webm, .flv, .wmv, .ts (MPEG-TS content only) | Supported; depends on media configuration |
| Audio | AudioParser | .mp3, .wav, .ogg, .flac, .aac, .m4a, .opus, .ac3 | Supported; depends on media configuration |

### Parse Artifacts

The internal `registry.parse()` interface is asynchronous and returns a `ParseResult`. Artifacts can live in a local temporary directory or AGFS; `temp_dir_path` is not necessarily a Viking URI, and `artifact_ref` identifies the storage backend. These are server internals; clients should use the resource import API.

| Field | Meaning |
| --- | --- |
| `root` | Root of the parsed resource tree |
| `temp_dir_path` | Temporary artifact location |
| `artifact_ref` | Artifact backend and root path |
| `source_format` / `parser_name` | Source format and parser name |
| `parse_time` | Parsing duration in seconds |
| `meta` / `warnings` | Metadata and parsing warnings |

### Document Splitting

The Markdown parser organizes sections by headings and size, merging short sections and splitting oversized content. Defaults are `max_section_size=2048` tokens, `max_section_chars=6000` characters, and `section_size_flexibility=0.3`, which allows some token overflow to preserve coherence. A single oversized table row may remain intact. These are splitting targets, not absolute limits on every output file.

Behavior varies by parser and `parse_mode`. Use `parse_mode="no_split"` for supported imports when a single file is needed; see [Resource Management](../api/02-resources.md).

## TreeBuilder

`TreeBuilder.finalize_from_temp()` inspects parse artifacts and resolves the final URI, returning a `BuildingTree` with root and temporary-location metadata. It does not copy files, clean up artifacts, or enqueue semantic work itself.

### Subsequent Ingestion Steps

1. Resolve and validate the target URI from the artifact and `to` or `parent`.
2. The ingestion processor persists content, handles resource locks, and updates existing content.
3. It schedules semantic processing and embedding, and cleans up temporary data through the artifact's backend.

The default resource root is `viking://resources`. For personal resources, explicitly target `viking://~/resources/...`; `viking://user` is a container of user spaces, not your resource root.

## SemanticQueue

SemanticQueue handles async L0/L1 generation and vectorization.

### Message Structure

Selected internal message fields follow; this is not a client request format:

| Field | Meaning |
| --- | --- |
| `id` | Message UUID |
| `uri` | Directory to process |
| `context_type` | resource, memory, skill, or session |
| `status` | Queue processing status |
| `recursive` | Whether to process child directories |
| `propagate_to_parent` | Whether completion may schedule a parent refresh |

### Processing Flow (Bottom-up)

```
Leaf directories → Parent directories → Root
```

### Single Directory Processing Steps

1. **Concurrent file summary generation**: Concurrency is limited by `vlm.max_concurrent`
2. **Collect child directory abstracts**: Read generated .abstract.md
3. **Generate .overview.md**: LLM generates L1 overview
4. **Extract .abstract.md**: Extract L0 from overview
5. **Write files**: Store the body and protected metadata as OKF Markdown
6. **Vectorize**: Create Context and queue to EmbeddingQueue

L0/L1 are directory sidecars, not per-file sidecars. Parent-summary generation consumes only child L0 bodies; OKF frontmatter is excluded from prompts. Embedding input contains the body and the whitelisted `directory`; `source`, `generated_by`, and `freshness` are excluded.

### Freshness, Sampling, and Parent Refresh

Each generation records direct-child coverage and uses stable sampling above `semantic.overview_sample_limit` (default 32). Resource/skill parent refreshes depend on child L0 body changes and freshness thresholds; unchanged L0 bodies do not propagate. `pending_child_changes` counts change events awaiting refresh, including repeated changes to the same child. See [Context Layers](03-context-layers.md#freshness-and-stable-sampling) for thresholds, manual refresh behavior, and deferred updates.

### Processing Limits

| Parameter | Default | Description |
|-----------|---------|-------------|
| `vlm.max_concurrent` | 32 | Semantic-processing concurrency, passed to `SemanticProcessor.max_concurrent_llm` |
| `max_images_per_call` | 10 | `VLMProcessor` constructor parameter: images per call; not an `ov.conf` field |
| `max_sections_per_call` | 20 | `VLMProcessor` constructor parameter: sections per call; not an `ov.conf` field |
| `semantic.overview_sample_limit` | 32 | Maximum direct-child sample used for one directory summary |

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
| **Entry point** | Resource parser | Session extraction and memory updates | Skill import |
| **Base URI** | `viking://resources` | `viking://~/memories` | `viking://~/skills` |
| **SemanticMsg type** | resource | memory | skill |

The examples below use a configured synchronous Python SDK client named `client`.

### Resource Extraction

```python
# Add resource
client.add_resource(
    path="/path/to/doc.pdf",
    options={"reason": "API documentation"},
)

# Flow: Parser → TreeBuilder(scope=resources) → SemanticQueue
```

### Skill Extraction

```python
# Add skill
client.add_skill(
    data={
        "name": "search-web",
        "content": "# search-web\n...",
    },
)

# Flow: Direct write to viking://~/skills/{name}/ → SemanticQueue
```

### Memory Extraction

```python
# Memory auto-extracted from session
client.commit_session(session_id)

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
