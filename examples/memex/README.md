# Memex - Personal Knowledge Assistant

A CLI-based personal knowledge assistant powered by OpenViking.

## Features

- **Knowledge Management**: Add files, directories, URLs to your knowledge base
- **Intelligent Q&A**: RAG-based question answering with multi-turn conversation
- **Session Memory**: Automatic memory extraction and context-aware search via OpenViking Session
- **Knowledge Browsing**: Navigate with L0/L1/L2 context layers (abstract/overview/full)
- **Semantic Search**: Quick and deep search with intent analysis
- **Feishu Integration**: Import documents from Feishu/Lark (optional)

## Quick Start

```bash
# Navigate to memex directory
cd examples/memex

# Install dependencies
uv sync

# Copy and configure
cp ov.conf.example ov.conf
# Edit ov.conf with your API keys

# Run Memex
uv run memex

# Or with verbose logging
uv run memex -v
```

## Configuration

Create `ov.conf` from the example:

```bash
cp ov.conf.example ov.conf
```

OpenAI example:

```json
{
  "embedding": {
    "dense": {
      "api_base": "https://api.openai.com/v1",
      "api_key": "your-api-key",
      "provider": "openai",
      "dimension": 3072,
      "model": "text-embedding-3-large"
    }
  },
  "vlm": {
    "api_base": "https://api.openai.com/v1",
    "api_key": "your-api-key",
    "provider": "openai",
    "model": "gpt-4o"
  }
}
```

Volcengine (豆包) example:

```json
{
  "embedding": {
    "dense": {
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "your-api-key",
      "backend": "volcengine",
      "dimension": "1024",
      "model": "doubao-embedding-vision-250615"
    }
  },
  "vlm": {
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "your-api-key",
    "backend": "volcengine",
    "model": "doubao-seed-1-8-251228"
  }
}
```

## Commands

### Knowledge Management
- `/add <path>` - Add file, directory, or URL
- `/rm <uri>` - Remove resource
- `/import <dir>` - Import entire directory

### Browse
- `/ls [uri]` - List directory contents
- `/tree [uri]` - Show directory tree
- `/read <uri>` - Read full content (L2)
- `/abstract <uri>` - Show summary (L0)
- `/overview <uri>` - Show overview (L1)
- `/stat <uri>` - Show resource metadata

### Search
- `/find <query>` - Quick semantic search
- `/search <query>` - Deep search with intent analysis
- `/grep <pattern>` - Content pattern search
- `/glob <pattern>` - File pattern matching

### Q&A
- `/ask <question>` - Single-turn question
- `/chat` - Toggle multi-turn chat mode
- `/clear` - Clear chat history
- Or just type your question directly!

### Session (Memory)
- `/session` - Show current session info
- `/commit` - End session and extract memories
- `/memories` - Show extracted memories

### Feishu (Optional)
- `/feishu` - Connect to Feishu MCP server
- `/feishu-login` - Login with your Feishu account (OAuth)
- `/feishu-ls [token]` - List files in My Space or folder
- `/feishu-list <query>` - Search and list documents in Feishu
- `/feishu-doc <id>` - Import Feishu document
- `/feishu-search <query>` - Search Feishu documents
- `/feishu-tools` - List available Feishu tools

#### Setup

1. Install [Node.js](https://nodejs.org/) (required for `npx` to run the Feishu MCP server)
2. Create an app on [Feishu Open Platform](https://open.feishu.cn/app) and grant document-related permissions (e.g. `docx:document:readonly`, `search:docs:read`, `drive:drive:readonly`)
3. Add `http://localhost:8089/callback` to the Redirect URLs in "Security Settings" (for `/feishu-login`)
4. Set environment variables:

```bash
export FEISHU_APP_ID="cli_xxxxxxxxxxxx"
export FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
```

5. Start Memex — the "Feishu integration not available" message should disappear
6. Use `/feishu` to connect, then `/feishu-list` to browse files, `/feishu-search` to search, or `/feishu-doc <id>` to import

Document ID can be found in the Feishu URL, e.g. `AbCdEfGhIjKl` from `https://xxx.feishu.cn/docx/AbCdEfGhIjKl`.

### System
- `/stats` - Show knowledge base statistics
- `/info` - Show configuration
- `/help` - Show help
- `/exit` - Exit Memex

## CLI Options

```bash
uv run memex [OPTIONS]

Options:
  --data-path PATH     Data storage path (default: ./memex_data)
  --config-path PATH   OpenViking config file path (default: ./ov.conf)
  --user USER          User name (default: default)
  -v, --verbose        Enable verbose logging (default: off)
```

## Data Storage

Data is stored in `./memex_data/` by default:
- `viking://resources/` - Your knowledge base
- `viking://user/memories/` - User preferences and memories
- `viking://agent/skills/` - Agent skills and memories

## Architecture

Memex uses a modular RAG (Retrieval-Augmented Generation) architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                        Memex CLI                            │
├─────────────────────────────────────────────────────────────┤
│                      MemexRecipe                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Search    │  │   Context   │  │    LLM Generation   │  │
│  │             │→ │   Builder   │→ │    + Chat History   │  │
│  │             │  │             │  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                     MemexClient                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              OpenViking Session API                 │    │
│  │  • Context-aware search with session history        │    │
│  │  • Automatic memory extraction on commit            │    │
│  └─────────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────────┤
│                    OpenViking Core                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Storage  │  │ Retrieve │  │  Parse   │  │  Models  │    │
│  │ (Vector) │  │ (Hybrid) │  │ (Files)  │  │(VLM/Emb) │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | File | Description |
|-----------|------|-------------|
| **MemexRecipe** | `rag/recipe.py` | RAG orchestration: search → context → LLM |
| **MemexClient** | `client.py` | OpenViking client wrapper with session support |
| **MemexConfig** | `config.py` | Configuration management |
| **CLI** | `cli.py` | Main CLI application and command dispatch |
| **Commands** | `commands/*.py` | CLI command implementations |
| **Feishu** | `feishu.py` | Feishu/Lark integration |

### RAG Flow

1. **Session-Aware Search**: Uses OpenViking Session API for context-aware search with intent analysis
2. **Context Building**: Formats search results with source citations
3. **LLM Generation**: Generates response with chat history support
4. **Memory Extraction**: Session commit extracts and stores user/agent memories

## Configuration Options

### RAG Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `search_top_k` | 5 | Number of search results to retrieve |
| `search_score_threshold` | 0.3 | Minimum score for search results |
| `llm_temperature` | 0.7 | LLM response temperature |
| `llm_max_tokens` | 2000 | Maximum tokens in LLM response |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENVIKING_CONFIG_FILE` | Path to OpenViking config file |
| `FEISHU_APP_ID` | Feishu app ID (optional, also accepts `LARK_APP_ID`) |
| `FEISHU_APP_SECRET` | Feishu app secret (optional, also accepts `LARK_APP_SECRET`) |
| `FEISHU_AUTH_MODE` | Auth mode: `tenant` (default), `user`, or `auto` |

## Development

### Project Structure

```
examples/memex/
├── __init__.py
├── __main__.py          # Entry point
├── .python-version      # Python version pin (3.11)
├── cli.py               # Main CLI application
├── client.py            # MemexClient wrapper
├── config.py            # Configuration
├── feishu.py            # Feishu integration
├── rag/
│   ├── __init__.py
│   └── recipe.py        # RAG recipe implementation
├── commands/
│   ├── __init__.py
│   ├── browse.py        # Browse commands (/ls, /tree, /read)
│   ├── knowledge.py     # Knowledge management (/add, /rm)
│   ├── query.py         # Q&A commands (/ask, /chat)
│   ├── search.py        # Search commands (/find, /search)
│   └── stats.py         # Stats commands (/stats, /info)
├── ov.conf              # Your local config (not committed)
├── ov.conf.example      # Example configuration
├── pyproject.toml
└── README.md
```

### Running Tests

```bash
# From memex directory
uv run pytest -v
```

## License

This project is part of OpenViking and is licensed under the Apache License 2.0.
