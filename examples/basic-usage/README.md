# Basic Usage Example: OpenViking Python SDK

This example is the shortest path to understanding OpenViking's core Python SDK workflow:
initialize a client, ingest a resource, browse the `viking://` filesystem, retrieve context,
and create a session that can later be committed into long-term memory.

It is intentionally SDK-first. If you want production deployment, shared access, or MCP client
integration, use this example as the foundation and then move to the server and MCP guides linked below.

## What This Example Covers

- Embedded SDK usage for local exploration
- HTTP client usage for server mode
- Resource ingestion from a remote URL
- Filesystem-style access with `ls`, `tree`, and `read`
- Retrieval with `find`, `abstract`, `overview`, and `grep`
- Session creation and message appending for memory workflows

## Choose the Right Mode

OpenViking currently has three common integration paths:

| Mode | Best for | Recommended? |
|------|----------|--------------|
| Embedded SDK | Single-process local experimentation | Yes, for first contact |
| HTTP server + SDK/CLI | Shared service, multi-session, multi-agent workloads | Yes, preferred for real deployments |
| MCP | Claude Code, Cursor, Claude Desktop, OpenClaw, and other MCP hosts | Yes, for tool-based client integration |

If you are building anything beyond a one-process local demo, prefer HTTP server mode over spawning isolated local processes repeatedly. For MCP specifically, follow the dedicated [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md).

## Prerequisites

1. Python 3.10+
2. OpenViking installed:

```bash
pip install openviking --upgrade --force-reinstall
```

3. A valid config file at `~/.openviking/ov.conf`

## Quick Start

### 1. Run the Example Script

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/examples/basic-usage
python basic_usage.py
```

The script uses embedded mode by default:

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

To point the same flow at a running server instead, switch to:

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()
```

See the dedicated [Server Mode Quick Start](../../docs/en/getting-started/03-quickstart-server.md) for the recommended shared-service setup.

### 2. What the Script Demonstrates

`basic_usage.py` walks through the same sequence most applications need:

1. Initialize a client and verify health.
2. Add a resource from a URL.
3. Inspect the resulting `viking://resources/...` tree.
4. Wait for semantic processing.
5. Load L0/L1/L2 context with `abstract`, `overview`, and `read`.
6. Run retrieval with `find`.
7. Run literal content search with `grep`.
8. Create a session and append messages for later memory extraction.

## Code Walkthrough

### Initialization

Use embedded mode for a local first run:

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

Use HTTP client mode when OpenViking runs as a separate service:

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()
```

If server authentication is enabled, use a `user_key` for normal data access:

```python
client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
    agent_id="my-agent",
)
```

`root_key` is for administrative access. It does not directly work with tenant-scoped APIs such as
`add_resource`, `find`, or `ls` unless you also pass `account` and `user`.
See [Authentication](../../docs/en/guides/04-authentication.md) and [Server Mode Quick Start](../../docs/en/getting-started/03-quickstart-server.md).

### Resource Ingestion

Add a URL, local file, or directory:

```python
result = client.add_resource(
    path="https://example.com/docs",
    wait=False,
)

result = client.add_resource(path="/path/to/manual.pdf")

result = client.add_resource(
    path="/path/to/repo",
    instruction="This is a Python web application",
)
```

For scripts and demos, `wait=True` is fine. In long-running applications, it is often better to ingest
asynchronously and call `wait_processed()` when you actually need the indexed result.

### Filesystem Access

OpenViking organizes context as a virtual filesystem:

```python
files = client.ls("viking://resources/")
tree = client.tree("viking://resources/my-project", level_limit=3)
content = client.read("viking://resources/my-project/README.md")
```

This same URI model applies to memories and skills as well:

- `viking://resources/`
- `viking://user/memories/`
- `viking://agent/memories/`
- `viking://agent/skills/`

### Retrieval

Use `find` for fast semantic search and `search` for more advanced retrieval:

```python
results = client.find(
    query="how does authentication work",
    target_uri="viking://resources/my-project",
    limit=5,
)

results = client.search(
    query="database configuration and failure handling",
    target_uri="viking://resources/",
    limit=10,
)
```

Use tiered loading after retrieval:

```python
uri = "viking://resources/my-project/docs/api.md"

abstract = client.abstract(uri)
overview = client.overview(uri)
content = client.read(uri)
```

Use `grep` when you need literal text matching instead of semantic retrieval:

```python
result = client.grep("viking://resources/my-project", "Agent", case_insensitive=True)
matches = result.get("matches", [])
```

### Sessions and Long-Term Memory

The example script creates a session and appends messages:

```python
session_info = client.create_session()
session_id = session_info["session_id"]

client.add_message(session_id, "user", "I prefer TypeScript over JavaScript")
client.add_message(session_id, "assistant", "Understood. I will use TypeScript where appropriate.")
```

To extract durable memories from that conversation, commit the session:

```python
client.commit_session(session_id)
```

After commit, you can retrieve those memories through normal search APIs:

```python
memories = client.find(
    query="user programming preferences",
    target_uri="viking://user/memories/",
)
```

## Configuration

Create `~/.openviking/ov.conf` with storage, embedding, and VLM settings. A minimal local setup looks like this:

```json
{
  "server": { "host": "127.0.0.1", "port": 1933 },
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

You can also use Volcengine, Azure OpenAI, or LiteLLM-backed providers. For current provider-specific examples, check the main [README](../../README.md) and the [Configuration Guide](../../docs/en/guides/01-configuration.md).

## Recommended Next Steps

- [Configuration Guide](../../docs/en/guides/01-configuration.md): review the current config model before moving to shared deployments.
- [Server Mode Quick Start](../../docs/en/getting-started/03-quickstart-server.md): set up `openviking-server` properly.
- [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md): connect OpenViking to Claude Code, Cursor, Claude Desktop, or OpenClaw.
- [Claude Code Memory Plugin](../claude-code-memory-plugin/README.md): use OpenViking as long-term memory inside Claude Code.
- [OpenCode Memory Plugin](../opencode-memory-plugin/README.md): use OpenViking memory tools inside OpenCode.
- [OpenClaw Plugin](../openclaw-plugin/README.md): integrate OpenViking with OpenClaw.

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| `ImportError` or local extension issues | Reinstall `openviking`; if developing from source, ensure local build dependencies are available. |
| `Connection refused` in HTTP mode | Start `openviking-server` and verify `http://localhost:1933/health`. |
| Tenant/auth errors | Prefer `user_key` for normal data APIs; use `root_key` only with explicit tenant headers. |
| Slow or empty search results right after ingestion | Wait for `wait_processed()` or ingest with `wait=True`. |
| Multiple clients or sessions competing for local storage | Use HTTP server mode instead of spinning up separate local processes. |

## License

Apache License 2.0
