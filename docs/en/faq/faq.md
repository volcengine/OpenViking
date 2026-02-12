# FAQ

## Basic Concepts

### What is OpenViking? What problems does it solve?

OpenViking is an open-source context database designed specifically for AI Agents. It solves core pain points when building AI Agents:

- **Fragmented Context**: Memories, resources, and skills are scattered everywhere, difficult to manage uniformly
- **Poor Retrieval Effectiveness**: Traditional RAG's flat storage lacks global view, making it hard to understand complete context
- **Unobservable Context**: Implicit retrieval chains are like black boxes, difficult to debug when errors occur
- **Limited Memory Iteration**: Lacks Agent-related task memory and self-evolution capabilities

OpenViking unifies all context management through a filesystem paradigm, enabling tiered delivery and self-iteration.

### What's the fundamental difference between OpenViking and traditional vector databases?

| Dimension | Traditional Vector DB | OpenViking |
|-----------|----------------------|------------|
| **Storage Model** | Flat vector storage | Hierarchical filesystem (AGFS) |
| **Retrieval Method** | Single vector similarity search | Directory recursive retrieval + Intent analysis + Rerank |
| **Output Format** | Raw chunks | Structured context (L0 Abstract/L1 Overview/L2 Details) |
| **Memory Capability** | Not supported | Built-in 6 memory categories with auto-extraction and iteration |
| **Observability** | Black box | Fully traceable retrieval trajectory |
| **Context Types** | Documents only | Resource + Memory + Skill three types |

### What is the L0/L1/L2 layered model? Why is it needed?

L0/L1/L2 is OpenViking's progressive content loading mechanism, solving the problem of "stuffing massive context into prompts all at once":

| Layer | Name | Token Limit | Purpose |
|-------|------|-------------|---------|
| **L0** | Abstract | ~100 tokens | Vector search recall, quick filtering, list display |
| **L1** | Overview | ~2000 tokens | Rerank refinement, content navigation, decision reference |
| **L2** | Details | Unlimited | Complete original content, on-demand deep loading |

This design allows Agents to browse abstracts for quick positioning, then load details on demand, significantly saving token consumption.

### What is Viking URI? What's its purpose?

Viking URI is OpenViking's unified resource identifier, formatted as `viking://{scope}/{path}`. It enables precise location of any context:

```
viking://
├── resources/              # Knowledge base: documents, code, web pages, etc.
│   └── my_project/
├── user/                   # User context
│   └── memories/           # User memories (preferences, entities, events)
└── agent/                  # Agent context
    ├── skills/             # Callable skills
    └── memories/           # Agent memories (cases, patterns)
```

## Installation & Configuration

### What are the environment requirements?

- **Python Version**: 3.10 or higher
- **Required Dependencies**: Embedding model (Volcengine Doubao recommended)
- **Optional Dependencies**:
  - VLM (Vision Language Model): For multimodal content processing and semantic extraction
  - Rerank model: For improved retrieval precision

### How do I install OpenViking?

```bash
pip install openviking
```

### How do I configure OpenViking?

Create an `~/.openviking/ov.conf` configuration file in your project directory:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  },
  "storage": {
    "agfs": { "backend": "local", "path": "./data" },
    "vectordb": { "backend": "local", "path": "./data" }
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

Supports Dense, Sparse, and Hybrid embedding modes.

## Usage Guide

### How do I initialize the client?

```python
import openviking as ov

# Async client - embedded mode (recommended)
client = ov.AsyncOpenViking(path="./my_data")
await client.initialize()

# Async client - HTTP client mode
client = ov.AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()
```

The SDK constructor only accepts `url`, `api_key`, and `path` parameters. Other configuration (embedding, vlm, etc.) is managed through the `ov.conf` config file.

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
    "./document.pdf",
    reason="Project technical documentation",  # Describe resource purpose to improve retrieval quality
    target="viking://resources/docs/"  # Specify storage location
)

# Add web page
await client.add_resource(
    "https://example.com/api-docs",
    reason="API reference documentation"
)

# Wait for processing to complete
await client.wait_processed()
```

### What's the difference between `find()` and `search()`? Which should I use?

| Feature | `find()` | `search()` |
|---------|----------|------------|
| **Session Context** | Not required | Required |
| **Intent Analysis** | Not used | Uses LLM to analyze and generate 0-5 queries |
| **Latency** | Low | Higher |
| **Use Case** | Simple semantic search | Complex tasks requiring context understanding |

```python
# find(): Simple direct semantic search
results = await client.find(
    "OAuth authentication flow",
    target_uri="viking://resources/"
)

# search(): Complex tasks requiring intent analysis
results = await client.search(
    "Help me implement user login functionality",
    session_info=session
)
```

**Selection Guide**:
- Know exactly what you're looking for → Use `find()`
- Complex tasks needing multiple context types → Use `search()`

### How do I use session management?

Session management is a core capability of OpenViking, supporting conversation tracking and memory extraction:

```python
# Create session
session = client.session()

# Add conversation messages
await session.add_message("user", [{"type": "text", "text": "Help me analyze performance issues in this code"}])
await session.add_message("assistant", [{"type": "text", "text": "Let me analyze..."}])

# Mark used context (for tracking)
await session.used(["viking://resources/code/main.py"])

# Commit session to trigger memory extraction
await session.commit()
```

### What memory types does OpenViking support?

OpenViking has 6 built-in memory categories, automatically extracted during session commit:

| Category | Belongs To | Description |
|----------|------------|-------------|
| **profile** | user | User basic info (name, role, etc.) |
| **preferences** | user | User preferences (code style, tool choices, etc.) |
| **entities** | user | Entity memories (people, projects, organizations, etc.) |
| **events** | user | Event records (decisions, milestones, etc.) |
| **cases** | agent | Cases learned by Agent |
| **patterns** | agent | Patterns learned by Agent |

### How do I use Unix-like filesystem APIs?

```python
# List directory contents
items = await client.ls("viking://resources/")

# Read full content (L2)
content = await client.read("viking://resources/doc.md")

# Get abstract (L0)
abstract = await client.abstract("viking://resources")

# Get overview (L1)
overview = await client.overview("viking://resources")
```

## Retrieval Optimization

### How do I improve retrieval quality?

1. **Use Rerank model**: Configuring Rerank significantly improves ranking effectiveness
2. **Provide meaningful `reason`**: Describe purpose when adding resources to help system understand resource value
3. **Organize directory structure properly**: Use `target` parameter to group related resources together
4. **Use session context**: `search()` leverages session history for intent analysis
5. **Choose appropriate Embedding mode**: Use `multimodal` input for multimodal content

### How is the retrieval result score calculated?

OpenViking uses a score propagation mechanism:

```
Final Score = 0.5 × Embedding Similarity + 0.5 × Parent Directory Score
```

This design gives content under high-scoring directories a boost, reflecting the importance of "contextual environment".

### What is directory recursive retrieval?

Directory recursive retrieval is OpenViking's innovative retrieval strategy:

1. **Intent Analysis**: Analyze query to generate multiple retrieval conditions
2. **Initial Positioning**: Vector retrieval to locate high-scoring directories
3. **Refined Exploration**: Secondary retrieval within high-scoring directories
4. **Recursive Drill-down**: Layer-by-layer recursion until convergence
5. **Result Aggregation**: Return the most relevant context

This strategy finds semantically matching fragments while understanding the complete context of the information.

## Troubleshooting

### Resources not being indexed after adding

**Possible causes and solutions**:

1. **Didn't wait for processing to complete**
   ```python
   await client.add_resource("./doc.pdf")
   await client.wait_processed()  # Must wait
   ```

2. **Embedding model configuration error**
   - Check if `api_key` in `~/.openviking/ov.conf` is correct
   - Confirm model name and endpoint are configured correctly

3. **Unsupported file format**
   - Check if file extension is in the supported list
   - Confirm file content is valid and not corrupted

4. **View processing logs**
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

### Search not returning expected results

**Troubleshooting steps**:

1. **Confirm resources have been processed**
   ```python
   # Check if resources exist
   items = await client.ls("viking://resources/")
   ```

2. **Check `target_uri` filter condition**
   - Ensure search scope includes target resources
   - Try expanding search scope

3. **Try different query approaches**
   - Use more specific or broader keywords
   - Compare effects of `find()` and `search()`

4. **Check L0 abstract quality**
   ```python
   abstract = await client.abstract("viking://resources/your-doc")
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

4. **View extracted memories**
   ```python
   memories = await client.find("", target_uri="viking://user/memories/")
   ```

### Performance issues

**Optimization suggestions**:

1. **Batch processing**: Adding multiple resources at once is more efficient than one by one
2. **Set appropriate `batch_size`**: Adjust batch processing size in Embedding configuration
3. **Use local storage**: Use `local` backend during development to reduce network latency
4. **Async operations**: Fully utilize `AsyncOpenViking` / `AsyncHTTPClient`'s async capabilities

## Deployment

### What's the difference between embedded mode and service mode?

| Mode | Use Case | Characteristics |
|------|----------|-----------------|
| **Embedded** | Local development, single-process apps | Auto-starts AGFS subprocess, uses local vector index |
| **Service Mode** | Production, distributed deployment | Connects to remote services, supports multi-instance concurrency, independently scalable |

```python
# Embedded mode
client = ov.AsyncOpenViking(path="./data")

# HTTP client mode (connects to a remote server)
client = ov.AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
```

### Is OpenViking open source?

Yes, OpenViking is fully open source under the Apache 2.0 license.

## Related Documentation

- [Introduction](../getting-started/01-introduction.md) - Understand OpenViking's design philosophy
- [Quick Start](../getting-started/02-quickstart.md) - 5-minute tutorial
- [Architecture Overview](../concepts/01-architecture.md) - Deep dive into system design
- [Retrieval Mechanism](../concepts/07-retrieval.md) - Detailed retrieval process
- [Configuration Guide](../guides/01-configuration.md) - Complete configuration reference
