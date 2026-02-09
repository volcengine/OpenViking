---
name: openviking
description: RAG and semantic search via OpenViking Context Database MCP server. Query documents, search knowledge base, add files/URLs to vector memory. Use for document Q&A, knowledge management, AI agent memory, file search, semantic retrieval. Triggers on "openviking", "search documents", "semantic search", "knowledge base", "vector database", "RAG", "query pdf", "document query", "add resource".
---

# OpenViking - Context Database for AI Agents

OpenViking is ByteDance's open-source **Context Database** designed for AI Agents — a next-generation RAG system that replaces flat vector storage with a filesystem paradigm for managing memories, resources, and skills.

**Key Features:**
- **Filesystem paradigm**: Organize context like files with URIs (`viking://resources/...`)
- **Tiered context (L0/L1/L2)**: Abstract → Overview → Full content, loaded on demand
- **Directory recursive retrieval**: Better accuracy than flat vector search
- **MCP server included**: Full RAG pipeline via Model Context Protocol

---

## Quick Check: Is It Set Up?

```bash
test -f ~/code/openviking/examples/mcp-query/ov.conf && echo "Ready" || echo "Needs setup"
curl -s http://localhost:8000/mcp && echo "Running" || echo "Not running"
```

## If Not Set Up → Initialize

Run the init script (one-time):

```bash
bash ~/.openclaw/skills/openviking-mcp/scripts/init.sh
```

This will:
1. Clone OpenViking from `https://github.com/volcengine/OpenViking`
2. Install dependencies with `uv sync`
3. Create `ov.conf` template
4. **Pause for you to add API keys** (embedding.dense.api_key, vlm.api_key)

**Required: Volcengine/Ark API Keys**

| Config Key | Purpose |
|------------|---------|
| `embedding.dense.api_key` | Semantic search embeddings |
| `vlm.api_key` | LLM for answer generation |

Get keys from: https://console.volcengine.com/ark

## Start the Server

```bash
cd ~/code/openviking/examples/mcp-query
uv run server.py
```

Options:
- `--port 8000` - Listen port
- `--host 127.0.0.1` - Bind address
- `--data ./data` - Data directory

Server will be at: `http://127.0.0.1:8000/mcp`

## Connect to Claude

```bash
claude mcp add --transport http openviking http://localhost:8000/mcp
```

Or add to `~/.mcp.json`:
```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Tools Available

| Tool | Description |
|------|-------------|
| `query` | Full RAG pipeline — search + LLM answer |
| `search` | Semantic search only, returns docs |
| `add_resource` | Add files, directories, or URLs |

## Example Usage

Once connected via MCP:

```
"Query: What is OpenViking?"
"Search: machine learning papers"
"Add https://example.com/article to knowledge base"
"Add ~/documents/report.pdf"
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port in use | `uv run server.py --port 9000` |
| Auth errors | Check API keys in ov.conf |
| Server not found | Ensure it's running: `curl localhost:8000/mcp` |

## Files

- `ov.conf` - Configuration (API keys, models)
- `data/` - Vector database storage
- `server.py` - MCP server implementation
