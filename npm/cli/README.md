# @openviking/cli

Native CLI for [OpenViking](https://github.com/volcengine/openviking) — an agent-native context database for AI workflows.

Store, search, and retrieve context (memories, resources, embeddings) across conversations and agents.

## Install

```bash
npm i -g @openviking/cli
```

This installs the `ov` binary for your platform (macOS, Linux, Windows).

### Alternative install methods

```bash
# via curl
curl -fsSL https://openviking.tos-cn-beijing.volces.com/cli/install.sh | bash

# one-shot with npx
npx @openviking/cli --help
```

## Quick start

```bash
# Check connectivity
ov health

# Store context
ov add-resource ./notes.md
ov add-memory "key insight from today's debugging session"

# Retrieve context
ov find "what did I learn about caching?"
ov ls
ov read /path/to/resource

# Semantic search
ov search "authentication flow"
ov grep "TODO"
```

## Commands

### Data

| Command | Description |
|---------|-------------|
| `add-resource` | Add files or URLs into OpenViking |
| `add-memory` | Store a memory in one shot |
| `add-skill` | Add a skill from a directory or SKILL.md |
| `ls` | List directory contents |
| `tree` | Hierarchical tree view |
| `read` | Read full file content |
| `abstract` | Brief summary (L0) |
| `overview` | Medium detail (L1) |
| `write` | Write or append text content |
| `find` | Semantic retrieval with scoring |
| `search` | Context-aware retrieval (experimental) |
| `grep` | Pattern search with regex |
| `glob` | File glob pattern search |
| `rm` | Remove a resource or directory |
| `mv` | Move or rename a resource |
| `stat` | Get resource metadata |
| `get` | Download file to local path |
| `session` | Manage sessions |
| `export` / `import` | Backup and restore as .ovpack |

### Interactive

| Command | Description |
|---------|-------------|
| `tui` | Interactive file explorer |
| `chat` | Chat with vikingbot agent |

### Status

| Command | Description |
|---------|-------------|
| `health` | Quick health check |
| `status` | Show server components status |
| `config` | Configuration management |
| `version` | Show CLI and server version |
| `task` | Track async processing tasks |

## Supported platforms

| Platform | Architecture | Package |
|----------|-------------|---------|
| macOS | Apple Silicon | `@openviking/cli-darwin-arm64` |
| macOS | Intel | `@openviking/cli-darwin-x64` |
| Linux | x64 | `@openviking/cli-linux-x64` |
| Linux | ARM64 | `@openviking/cli-linux-arm64` |
| Windows | x64 | `@openviking/cli-win32-x64` |

## Links

- [GitHub](https://github.com/volcengine/openviking)
- [Documentation](https://github.com/volcengine/openviking#readme)

## License

Apache-2.0
