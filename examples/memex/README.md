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
# Install dependencies
uv sync

# Copy and configure
cp ov.conf.example ov.conf
# Edit ov.conf with your API keys

# Run Memex
uv run memex
```

## Configuration

Create `ov.conf` from the example:

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

### Search
- `/find <query>` - Quick semantic search
- `/search <query>` - Deep search with intent analysis
- `/grep <pattern>` - Content pattern search

### Q&A
- `/ask <question>` - Single-turn question
- `/chat` - Toggle multi-turn chat mode
- `/clear` - Clear chat history
- Or just type your question directly!

### Feishu (Optional)
- `/feishu` - Connect to Feishu
- `/feishu-doc <id>` - Import Feishu document
- `/feishu-search <query>` - Search Feishu documents

Set `FEISHU_APP_ID` and `FEISHU_APP_SECRET` environment variables to enable.

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
  --user USER          User name (default: default)
  --llm-backend NAME   LLM backend: openai or volcengine (default: openai)
  --llm-model MODEL    LLM model name (default: gpt-4o-mini)
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
| **Commands** | `commands/*.py` | CLI command implementations |

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
| `FEISHU_APP_ID` | Feishu app ID (optional) |
| `FEISHU_APP_SECRET` | Feishu app secret (optional) |

## Development

### Project Structure

```
examples/memex/
├── __init__.py
├── __main__.py          # Entry point
├── app.py               # Main application
├── client.py            # MemexClient wrapper
├── config.py            # Configuration
├── rag/
│   ├── __init__.py
│   └── recipe.py        # RAG recipe implementation
├── commands/
│   ├── __init__.py
│   ├── base.py          # Base command class
│   ├── browse.py        # Browse commands (/ls, /tree, /read)
│   ├── feishu.py        # Feishu integration
│   ├── knowledge.py     # Knowledge management (/add, /rm)
│   ├── query.py         # Q&A commands (/ask, /chat)
│   ├── search.py        # Search commands (/find, /search)
│   └── system.py        # System commands (/stats, /info)
├── ov.conf.example      # Example configuration
└── README.md
```

### Running Tests

```bash
# From project root
uv run pytest examples/memex/tests/ -v
```

## License

This project is part of OpenViking and is licensed under the Apache License 2.0.
