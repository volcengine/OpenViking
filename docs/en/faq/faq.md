# FAQ

## Basic Concepts

### What is OpenViking? What problems does it solve?

OpenViking organizes an agent's resources, memories, and skills as files. Applications can browse paths, search semantically, and read content on demand. They can also retain preferences and experience extracted from sessions for later tasks.

For example, an agent revising a deployment plan can search project documentation and read the relevant configuration. Constraints agreed in an earlier session can also be retrieved and reused if they were extracted as memories. The application must connect retrieval and session submission; installing the database alone does not give an agent this context. Start by [importing and searching a resource](../getting-started/02-quickstart.md), then connect an [agent tool](../agent-integrations/01-overview.md) if needed.

### What's the fundamental difference between OpenViking and traditional vector databases?

A vector database primarily stores vectors and supports similarity search. OpenViking adds context directories, layered summaries, resource ingestion, sessions, and memory management. Vector database capabilities vary by product; compare them against your requirements.

| Need | OpenViking capability |
| --- | --- |
| Organize context | Manage resources, memories, and skills through `viking://` paths |
| Retrieve content | Combine vector search and directory traversal with configurable intent analysis and reranking |
| Control how much to read | Read directory abstracts or overviews before loading details |
| Reuse session experience | Extract and update memories after a session is committed, following the memory policy |
| Diagnose retrieval problems | Inspect processing and retrieval through logs and telemetry |

If your application only needs similarity queries over existing vectors, evaluate whether its current database is sufficient. Consider OpenViking when you also need directory browsing, layered reading of summaries and details, or memory across sessions. Compare retrieved content, reading volume, latency, and processing cost using your own data and representative questions; benefits depend on the data, models, and configuration.

### What is the L0/L1/L2 layered model? Why is it needed?

An agent can use summaries to locate relevant material before reading full content, reducing unrelated content in its context.

| Layer | Content | Default body limit | Purpose |
| --- | --- | --- | --- |
| L0 | Directory abstract, `.abstract.md` | 256 characters | Retrieval and quick filtering |
| L1 | Directory overview, `.overview.md` | 4,000 characters | Navigation and reranking |
| L2 | Original or parsed content | No shared limit | Reading details on demand |

L0/L1 are directory sidecars. Their availability depends on processing state and configuration. Limits are measured in characters and configured through `semantic.abstract_max_chars` and `semantic.overview_max_chars`. See [Context Layers](../concepts/03-context-layers.md).

### What is Viking URI? What's its purpose?

Viking URI is OpenViking's unified resource identifier, formatted as `viking://{scope}/{path}`. It enables precise location of any context:

```
viking://
├── resources/              # Knowledge base: documents, code, web pages, etc.
│   └── my_project/
├── user/
│   └── {user_id}/          # Private context for a user
│       ├── memories/       # User memories
│       ├── resources/      # Private user resources
│       ├── skills/         # Private user skills (default)
│       └── peers/{peer_id}/
│           ├── memories/   # Memories scoped to a peer
│           └── resources/  # Resources scoped to a peer
└── agent/                  # Optional account-wide Agent capabilities
    └── skills/             # Shared skills
```

## Installation & Configuration

### What are the environment requirements?

- **Python Version**: 3.10 or higher
- **Build Tools** (if installing from source or on unsupported platforms): Rust/Cargo, GCC 9+ or Clang 11+
- **Required Dependencies**: Embedding model (Volcengine Doubao recommended)
- **Optional Dependencies**:
  - VLM (Vision Language Model): For multimodal content processing and semantic extraction
  - Rerank model: For improved retrieval precision

### How does OpenViking access the AGFS filesystem?

OpenViking runs the RAGFS filesystem in-process through the Rust binding
(`ragfs_python` / `RAGFSBindingClient`). The binding executes filesystem logic
directly within the Python process; remote storage backends still make network requests. The RAGFS shared library ships in prebuilt wheels and can also be built from source.

> [!WARNING]
> OpenViking no longer supports the AGFS HTTP client mode. AGFS / RAGFS filesystem access now happens only through the in-process Rust binding (`RAGFSBindingClient`). This does not affect the OpenViking server HTTP API, the `ov` CLI, or `AsyncHTTPClient` / `SyncHTTPClient` when they connect to an OpenViking server.

### What should I do if I encounter "AGFS binding library not found"?

This usually means the RAGFS shared library is missing or cannot be loaded. Check whether a prebuilt wheel exists for your Python version and platform, then try reinstalling. [Build from source](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING.md) only if no wheel is available or you need to change the source; that path requires the native build toolchain.

### How do I install/upgrade OpenViking?

```bash
pip install openviking --upgrade --force-reinstall

```

### How do I configure OpenViking?

Create `~/.openviking/ov.conf` under your home directory. Replace the example models and credentials with your own:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-lite-260428",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  },
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
  }
}
```

Config files at the default path `~/.openviking/ov.conf` are loaded automatically; you can also specify a different path via the `OPENVIKING_CONFIG_FILE` environment variable or `--config` flag. See [Configuration Guide](../guides/01-configuration.md) for details.

### What Embedding providers are supported?

| Provider | Description |
|---------|-------------|
| `volcengine` | Volcengine Embedding API (Recommended) |
| `openai` | OpenAI Embedding API |
| `vikingdb` | VikingDB Embedding API |
| `jina` | Jina AI Embedding API |
| `ollama` | Ollama (local OpenAI-compatible server, no API key required) |

Supports Dense, Sparse, and Hybrid embedding modes.

## Usage Guide

### How do I initialize the client?

```python
from openviking_sdk import AsyncHTTPClient

client = AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()
```

Embedding, VLM, storage, and other service configuration is managed by the OpenViking Server through `ov.conf`.

### What file formats are supported?

| Type | Supported Formats |
|------|-------------------|
| **Text** | `.txt`, `.md`, `.json`, `.yaml` |
| **Code** | `.py`, `.js`, `.ts`, `.go`, `.java`, `.cpp`, etc. |
| **Documents** | `.pdf`, `.docx` |
| **Images** | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` |
| **Video** | `.mp4`, `.mov`, `.avi` |
| **Audio** | `.mp3`, `.wav`, `.m4a` |

### How do I add resources?

```python
# Add single file
await client.add_resource(
    path="./document.pdf",
    parent="viking://resources",  # Store under this directory; the name comes from the source
    options={"reason": "Project technical documentation"},  # Used for L0/L1 summaries when no instruction is given, and for resource-linked memory extraction
)

# Add web page
await client.add_resource(
    path="https://example.com/api-docs",
    options={"reason": "API reference documentation"},
)

# Wait for processing to complete
await client.wait_processed()
```

### What is the difference between `to` and `parent`? Which should I use?

|  | `to` | `parent` |
|---|---|---|
| What you pass | The exact final URI, **including the leaf name** | An **existing directory**; the leaf name comes from the source |
| On a name collision | No renaming. An existing target is synced to the new source, so visible entries the source does not contain are deleted | Never overwrites. Falls back to `name_1`, `name_2`, … and returns a warning |
| When to use it | The final name is known and must be honored verbatim, or you want to update an existing resource in place | The leaf name is derived server-side (URL / repository imports, split documents), or nothing already at the destination may be touched |

Leaving both empty derives the directory and the leaf name from the source, with the same collision handling as `parent`.

`to` and `parent` cannot be combined; passing both is an error.

### What happens when `to` points at an existing directory?

The content is synced to match the new source and the metadata is kept:

- **Dot-prefixed entries survive untouched** — `.abstract.md`, `.overview.md`, `.search_tags.json`, `.image_mappings.json` and friends. The sync enumerates neither side's dotfiles, so they are neither deleted nor overwritten.
- **Everything else visible is aligned with the source** — entries the source does not contain are deleted, changed ones are overwritten, unchanged ones stay in place and keep their URI, vectors and tags.

So this preserves metadata and replaces the content itself; it is not a delete-and-recreate. Use `parent` when nothing already at the destination may be touched.

Note: `processing_mode="vectors_only"` skips semantic processing, so the surviving `.abstract.md` / `.overview.md` are **not** regenerated and keep describing the content that was just replaced. Use the default `semantic_and_vectors` when summaries must follow the content.

### What's the difference between `find()` and `search()`? Which should I use?

| Feature | `find()` | `search()` |
|---------|----------|------------|
| **Session Context** | Not used | Optional; used when `session_id` is supplied |
| **Intent Analysis** | Not used | Uses an LLM when session content exists and intent analysis is enabled |
| **Latency** | Low | Higher |
| **Use Case** | Simple semantic search | Complex tasks requiring context understanding |

```python
# find(): Simple direct semantic search
results = await client.find(
    query="OAuth authentication flow",
    target_uri="viking://resources/",
)

# search(): Complex tasks requiring intent analysis
results = await client.search(
    query="Help me implement user login functionality",
    session_id=session.session_id,
)
```

**Selection Guide**:
- Know exactly what you're looking for → Use `find()`
- Complex tasks needing multiple context types → Use `search()`

### How do I use session management?

Session management is a core capability of OpenViking, supporting conversation tracking and memory extraction:

```python
from openviking_sdk import TextPart

# Create session
session_info = await client.create_session()
session = client.session(session_id=session_info["session_id"])

# Add conversation messages
await session.add_message(
    role="user",
    parts=[TextPart(text="Help me analyze performance issues in this code")],
)
await session.add_message(
    role="assistant",
    parts=[TextPart(text="Let me analyze...")],
)

# Commit session to trigger memory extraction
await session.commit()
```

### What memory types does OpenViking support?

OpenViking includes memory types such as `profile`, `preferences`, `entities`, `events`, `identity`, `soul`, `cases`, `trajectories`, and `experiences`. After a session is committed, the active memory policy determines which useful information to extract. Applications can also extend or adjust the memory types for their own needs.

Memories are stored in the current User or Peer namespace; there is no current writable `viking://agent/memories` directory. See [Context Types](../concepts/02-context-types.md) for the complete type and path mapping.

### How do I use Unix-like filesystem APIs?

```python
# List directory contents
items = await client.ls(uri="viking://resources/")

# Read full content (L2)
content = await client.read(uri="viking://resources/doc.md")

# Get abstract (L0)
abstract = await client.abstract(uri="viking://resources")

# Get overview (L1)
overview = await client.overview(uri="viking://resources")
```

## Retrieval Optimization

### How do I improve retrieval quality?

1. **Evaluate reranking**: Compare ranking results on representative queries before enabling it
2. **Check summaries**: Verify that L0/L1 represent the source accurately; adjust the import `instruction` or summary templates when needed
3. **Organize directories**: Import with `parent` for an existing parent directory or `to` for an exact target URI
4. **Use session context**: Keep `retrieval.enable_intent` on (default) and pass a session with content to `search()`
5. **Choose appropriate Embedding mode**: Use `multimodal` input for multimodal content

### How is the retrieval result score calculated?

Recursive retrieval propagates parent-directory scores. The weight is set by `retrieval.score_propagation_alpha`, which defaults to `1.0`, using only the current candidate score:

```
Propagated score = α × Current candidate score + (1 − α) × Parent directory score
```

The current candidate score can come from vector search or reranking, depending on retrieval mode and configuration. Final ranking may also use factors such as hotness, so this formula alone does not describe every returned score. See [Retrieval](../concepts/07-retrieval.md).

### What is directory recursive retrieval?

Recursive retrieval locates relevant directories, then searches their contents:

1. **Prepare the query**: `find()` uses it directly; `search()` can analyze intent first
2. **Initial Positioning**: Vector retrieval to locate high-scoring directories
3. **Refined Exploration**: Secondary retrieval within high-scoring directories
4. **Recursive Drill-down**: Layer-by-layer recursion until convergence
5. **Result Aggregation**: Return the most relevant context

Directory scores influence the ranking of descendants. Retrieval quality depends on source organization, summaries, models, and the query.

## Troubleshooting

### Resources not being indexed after adding

**Possible causes and solutions**:

1. **Didn't wait for processing to complete**
   ```python
   result = await client.add_resource(path="./doc.pdf", wait=True)
   print(result)
   ```
   Use `wait=True` for a new import. To inspect an import already submitted, query its returned `task_id` instead of importing it again. Keep waiting while its status is `pending` or `running`; inspect the task details if it is `failed` or `cancelled`. A request timeout alone does not mean the background task failed. See [Async Tasks](../api/17-tasks.md).

2. **Embedding model configuration error**
   - Check if `api_key` in `~/.openviking/ov.conf` is correct
   - Confirm model name and endpoint are configured correctly

3. **Unsupported file format**
   - Check if file extension is in the supported list
   - Confirm file content is valid and not corrupted

4. **View server-side processing logs**
   Inspect the task error in the server terminal or logging system. Python logging settings in a client do not enable logs on a remote server.

### Search not returning expected results

**Troubleshooting steps**:

1. **Confirm resources exist, then check the import task**
   Use the `task_id` returned by the import. `completed` means processing has finished; inspect the cause if the task is `failed` or `cancelled`.
   ```python
   # Check if resources exist
   items = await client.ls(uri="viking://resources/")
   task = await client.get_task("<task_id returned by the import>")
   print(task["status"])
   ```

2. **Check `target_uri` filter condition**
   - Ensure search scope includes target resources
   - Try expanding search scope

3. **Try different query approaches**
   - Use more specific or broader keywords
   - Compare effects of `find()` and `search()`

4. **Check L0 abstract quality**
   ```python
   abstract = await client.abstract(uri="viking://resources/your-doc")
   print(abstract)  # Confirm abstract accurately reflects content
   ```

### Memory extraction not working

**Troubleshooting steps**:

1. **Ensure `commit()` was called**
   ```python
   await session.commit()  # Triggers memory extraction
   ```

2. **Check VLM configuration**
   - Memory extraction requires VLM model
   - Confirm `vlm` configuration is correct

3. **Confirm conversation content is meaningful**
   - Casual chat may not produce memories
   - Needs to contain extractable information (preferences, entities, events, etc.)

4. **List the memory directory**
   ```python
   memories = await client.ls(uri="viking://~/memories/")
   ```

### Performance issues

**Optimization suggestions**:

1. **Locate the bottleneck**: Inspect queues, model latency, and storage time before changing concurrency
2. **Set appropriate `batch_size`**: Adjust batch processing size in Embedding configuration
3. **Use local storage**: Use `local` backend during development to reduce network latency
4. **Async operations**: Fully utilize `AsyncHTTPClient`'s async capabilities

## Deployment

### Is OpenViking open source?

The main project uses AGPLv3; the CLI and most examples use Apache 2.0. See the repository [license summary](https://github.com/volcengine/OpenViking#license) for component licenses and exceptions.

## Related Documentation

- [Introduction](../getting-started/01-introduction.md) - Understand OpenViking's design philosophy
- [Quick Start](../getting-started/02-quickstart.md) - Your first import and search
- [Architecture Overview](../concepts/01-architecture.md) - Deep dive into system design
- [Retrieval Mechanism](../concepts/07-retrieval.md) - Detailed retrieval process
- [Configuration Guide](../guides/01-configuration.md) - Complete configuration reference
