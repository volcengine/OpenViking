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

## Add Directory

`add.py` supports adding an entire directory of documents at once. Files are automatically classified and parsed by their type (PDF, Markdown, Text, code, etc.). A summary table is printed after import showing which files were processed, failed, unsupported, or filtered.

```bash
# Add all supported files in a directory
uv run add.py ~/Documents/research/

# Only include specific file types
uv run add.py ~/project/ --include '*.md' --include '*.pdf'

# Exclude certain files
uv run add.py ~/project/ --exclude 'test_*' --exclude '*.pyc'

# Skip specific sub-directories
uv run add.py ~/project/ --ignore-dirs node_modules --ignore-dirs .git

# Combine options
uv run add.py ~/project/ --include '*.md' --exclude 'draft_*' --ignore-dirs vendor
```

### Directory Options

| Option | Description |
|--------|-------------|
| `--include PATTERN` | Glob pattern for files to include (can be repeated) |
| `--exclude PATTERN` | Glob pattern for files to exclude (can be repeated) |
| `--ignore-dirs NAME` | Directory names to skip (can be repeated) |

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
add.py              # Add documents/directories CLI
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
- When adding directories, use `--include` / `--exclude` to control which files are imported
