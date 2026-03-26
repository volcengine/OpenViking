# Basic Usage Example: Getting Started with OpenViking Python SDK

This example demonstrates the core features of OpenViking using the Python SDK. It covers the essential operations for building AI Agent applications with persistent context management.

## What You'll Learn

- **Initializing OpenViking**: Embedded mode vs HTTP client mode
- **Adding Resources**: URLs, files, and directories
- **Browsing the Virtual Filesystem**: Using `ls`, `tree`, `glob`
- **Semantic Search**: Finding relevant context with `find` and `search`
- **Tiered Context Loading**: Using L0 (abstract), L1 (overview), L2 (full content)
- **Session Management**: Storing and recalling conversation memories

## Prerequisites

1. **Python 3.10+**
2. **OpenViking installed**:
   ```bash
   pip install openviking --upgrade
   ```
3. **Configuration file** at `~/.openviking/ov.conf` (see [Configuration](#configuration) below)

## Quick Start

### 1. Run the Example Script

```bash
# Clone the repository
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/examples/basic-usage

# Run the basic usage example
python basic_usage.py
```

### 2. Expected Output

```
=== OpenViking Basic Usage Example ===

1. Initializing OpenViking...
   Status: healthy

2. Adding a resource (URL)...
   Root URI: viking://resources/raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md
   Files indexed: 1

3. Browsing the virtual filesystem...
   viking://resources/raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/
   └── README.md

4. Waiting for semantic processing...

5. Tiered Context Loading:
   L0 (Abstract): OpenViking is an open-source Context Database designed specifically for AI Agents...

   L1 (Overview):
   [Contains key points and usage scenarios]

6. Semantic Search:
   Query: "what is openviking"
   Results:
   - viking://resources/.../README.md (score: 0.8523)

7. Content search (grep):
   Pattern: "Agent"
   Found 15 matches

8. Closing OpenViking...
   Done!
```

## Code Walkthrough

### Initialization

OpenViking supports two modes:

**Embedded Mode** (default for local development):
```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

**HTTP Client Mode** (connect to remote server):
```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
```

### Adding Resources

Add URLs, local files, or directories:

```python
# Add a URL
result = client.add_resource(
    path="https://example.com/documentation",
    wait=True  # Wait for semantic processing
)

# Add a local file
result = client.add_resource(
    path="/path/to/your/document.pdf"
)

# Add a directory (repository)
result = client.add_resource(
    path="/path/to/your/project",
    instruction="This is a Python web application"
)
```

### Browsing the Filesystem

OpenViking uses a virtual filesystem paradigm with `viking://` URIs:

```python
# List directory contents
files = client.ls("viking://resources/")

# Show directory tree
tree = client.tree("viking://resources/my-project", level_limit=3)

# Find files by pattern
matches = client.glob("**/*.md", uri="viking://resources/my-project")
```

### Semantic Search

Find context using natural language queries:

```python
# Quick semantic search
results = client.find(
    query="how to handle API authentication",
    target_uri="viking://resources/my-project",
    limit=5
)

# Advanced search with intent analysis
results = client.search(
    query="database connection configuration and error handling",
    target_uri="viking://resources/",
    limit=10
)
```

### Tiered Context Loading

OpenViking processes content into three layers for efficient retrieval:

```python
uri = "viking://resources/my-project/docs/api.md"

# L0: Quick abstract (~100 tokens)
abstract = client.abstract(uri)

# L1: Overview with key points (~2k tokens)
overview = client.overview(uri)

# L2: Full content
content = client.read(uri)
```

### Session Management

Store conversation memories for long-term recall:

```python
# Create a session
session_info = client.create_session()
session_id = session_info["session_id"]

# Add conversation messages
client.add_message(session_id, "user", "I prefer TypeScript over JavaScript")
client.add_message(session_id, "assistant", "Noted! I'll use TypeScript in your projects.")

# Commit session to extract long-term memories
client.commit_session(session_id)

# Later, recall relevant memories
memories = client.find(
    query="user programming preferences",
    target_uri="viking://user/memories/"
)
```

## Configuration

Create `~/.openviking/ov.conf` with your model provider settings:

```json
{
  "storage": {
    "workspace": "~/.openviking/data"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "your-api-key",
      "model": "text-embedding-3-large",
      "dimension": 3072
    }
  },
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o"
  }
}
```

> **Tip**: You can also use Volcengine (Doubao), Azure OpenAI, or LiteLLM (supports Anthropic, DeepSeek, Gemini, etc.). See the main README for provider-specific configuration.

## Use Cases

### Building a Documentation-Aware Agent

```python
import openviking as ov

class DocumentationAgent:
    def __init__(self):
        self.ov = ov.OpenViking(path="./data")
        self.ov.initialize()
    
    def ingest_docs(self, doc_path: str):
        """Add documentation to the knowledge base."""
        self.ov.add_resource(doc_path, wait=True)
    
    def answer(self, question: str) -> str:
        """Find relevant documentation and answer."""
        results = self.ov.find(question, limit=3)
        
        context = []
        for r in results.resources:
            content = self.ov.read(r.uri)
            context.append(content)
        
        # Use context with your LLM to generate answer
        return context

# Usage
agent = DocumentationAgent()
agent.ingest_docs("https://docs.python.org/3/")
agent.ingest_docs("/path/to/your/project/docs")
```

### Creating a Memory-Aware Assistant

```python
import openviking as ov

class MemoryAssistant:
    def __init__(self, user_id: str):
        self.ov = ov.SyncHTTPClient(url="http://localhost:1933")
        self.user_id = user_id
        self.session_id = None
    
    def start_conversation(self):
        """Start a new conversation session."""
        session = self.ov.create_session()
        self.session_id = session["session_id"]
    
    def remember(self, user_input: str, assistant_response: str):
        """Store conversation turn."""
        self.ov.add_message(self.session_id, "user", user_input)
        self.ov.add_message(self.session_id, "assistant", assistant_response)
    
    def recall(self, query: str) -> list:
        """Recall relevant memories."""
        return self.ov.find(query, target_uri="viking://user/memories/")
    
    def end_conversation(self):
        """Extract long-term memories from session."""
        self.ov.commit_session(self.session_id)
```

## Next Steps

- **[OpenClaw Plugin](../openclaw-plugin/)**: Integrate with OpenClaw AI assistant
- **[Claude Memory Plugin](../claude-memory-plugin/)**: Use with Claude Code
- **[OpenCode Memory Plugin](../opencode-memory-plugin/)**: Integrate with OpenCode
- **[Skills Examples](../skills/)**: CLI-based search and management skills

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ImportError: pyagfs not found` | Run: `pip install -e third_party/agfs/agfs-sdk/python` from source |
| `Connection refused` | Ensure OpenViking server is running: `openviking-server` |
| `API key error` | Check your `~/.openviking/ov.conf` configuration |
| `Slow semantic processing` | Wait for `wait_processed()` or use `add_resource(..., wait=True)` |

## License

Apache License 2.0