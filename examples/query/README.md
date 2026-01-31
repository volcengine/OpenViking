# OpenViking RAG Query Tool

Simple RAG (Retrieval-Augmented Generation) example using OpenViking + LLM.

## Quick Start

```bash
# 0. install dependencies
uv sync

# 1. Add documents to database
uv run add.py ~/xxx/document.pdf
uv run add.py https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md

# 2. Query with LLM
uv run query.py "What do we have here?"
uv run query.py "What do we have here?" --score-threshold 0.5

# 3. redo
mv data/ data.bak/ # or rm -rf if you want
```

### Query Options

| Option | Default | Description |
|--------|---------|-------------|
| `--top-k` | 5 | Number of search results to use |
| `--temperature` | 0.7 | LLM creativity (0.0-1.0) |
| `--max-tokens` | 2048 | Maximum response length |
| `--verbose` | false | Show detailed information |
| `--score-threshold` | 0.0 | Minimum similarity score for results |

## Debug Mode

Enable detailed logging:

```bash
OV_DEBUG=1 uv run query.py "question"
OV_DEBUG=1 uv run add.py file.pdf
```

## Configuration

Edit `ov.conf` to configure:
- Embedding model
- LLM model (VLM)
- API keys

## Files

```
rag.py              # RAG pipeline library
add.py     # Add documents CLI
query.py            # Query CLI
q                   # Quick query wrapper
logging_config.py   # Logging configuration
ov.conf             # OpenViking config
data/               # Database storage
```

## Tips

- Use `./q` for quick queries (clean output)
- Use `uv run query.py` for more control
- Set `OV_DEBUG=1` only when debugging
- Resources are indexed once, query unlimited times
