# OpenViking CLI

Command-line interface for [OpenViking](https://github.com/volcengine/OpenViking) - an Agent-native context database.

## Installation

### Quick Install (Linux/macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

### From Source

```bash
cargo install --path crates/ov_cli
```

## Configuration

Create `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-api-key"
}
```

## Quick Start

```bash
# Add a resource
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/docs/en/about/01-about-us.md --wait

# List contents
ov ls viking://resources

# Semantic search
ov find "what is openviking"

# Get file tree
ov tree viking://resources

# Read content
ov read viking://resources/...
```

## Command Groups

### Resource Management
- `add-resource` - Import local files or URLs
- `add-skill` - Add a skill
- `export` - Export as .ovpack
- `import` - Import .ovpack

### Relations
- `relations` - List relations
- `link` - Create relation links
- `unlink` - Remove relation

### Filesystem
- `ls` - List directory contents
- `tree` - Get directory tree
- `mkdir` - Create directory
- `rm` - Remove resource
- `mv` - Move/rename
- `stat` - Get metadata

### Content Access
- `read` - Read L2 (full content)
- `abstract` - Read L0 (abstract)
- `overview` - Read L1 (overview)

### Search
- `find` - Semantic retrieval
- `search` - Context-aware retrieval
- `grep` - Content pattern search
- `glob` - File glob pattern

### System
- `system wait` - Wait for async processing
- `system status` - Component status
- `system health` - Health check
- `observer queue` - Queue status
- `observer vikingdb` - VikingDB status
- `observer vlm` - VLM status

### Session
- `session new` - Create session
- `session list` - List sessions
- `session get` - Get session details
- `session delete` - Delete session
- `session add-message` - Add message
- `session commit` - Commit and extract memories

### Config
- `config show` - Show configuration
- `config validate` - Validate config

## Output Formats

```bash
ov --output json ls
ov --output table ls
ov --json ls  # Compact JSON wrapper for scripts
```

## Examples

```bash
# Add URL and wait for processing
ov add-resource https://example.com/docs --wait --timeout 60

# Search with threshold
ov find "API authentication" --threshold 0.7 --limit 5

# Recursive list
ov ls viking://resources --recursive

# Glob search
ov glob "**/*.md" --uri viking://resources

# Session workflow
SESSION=$(ov session new --json | jq -r '.result.session_id')
ov session add-message --session-id $SESSION --role user --content "Hello"
ov session commit --session-id $SESSION
```

## Development

```bash
# Build
cargo build --release

# Run tests
cargo test

# Install locally
cargo install --path .
```
