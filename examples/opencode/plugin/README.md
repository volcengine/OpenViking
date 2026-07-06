# openviking-opencode

OpenViking plugin for [OpenCode](https://opencode.ai). Provides native tools for semantic search, memory, and code retrieval across your indexed repositories.

## What's New in v1.0.0

**Breaking Change**: This version replaces the skill-based integration with native OpenCode tools.

- **Before (v0.x)**: Required `skill("openviking")` + bash `ov` commands
- **Now (v1.0)**: Tools appear directly in the agent's inventory — no skill loading needed

Benefits:
- Tools are always visible to the agent (no forgotten skills)
- No shell command translation overhead
- Faster and more reliable execution

## Prerequisites

Install OpenViking and configure `~/.openviking/ov.conf`:

```bash
pip install openviking --upgrade
```

Start the OpenViking server before launching OpenCode:

```bash
openviking-server --config ~/.openviking/ov.conf
```

## Installation

Add the plugin to `~/.config/opencode/opencode.json`:

```json
{
  "plugin": ["openviking-opencode"]
}
```

Restart OpenCode.

## Configuration

Create `~/.config/opencode/openviking-config.json`:

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "peerId": "",
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.15,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000
  }
}
```

Environment variables override config file values:

```bash
export OPENVIKING_API_KEY="your-api-key"
export OPENVIKING_ACCOUNT="default"   # trusted-mode only
export OPENVIKING_USER="opencode"     # trusted-mode only
export OPENVIKING_PEER_ID="opencode"  # peer-scoped memory
```

## Tools

### Memory & Search Tools

| Tool | Description |
|------|-------------|
| `memsearch` | Semantic search across memories, resources, and skills |
| `memread` | Read content at a specific `viking://` URI |
| `membrowse` | Browse filesystem structure (list, tree, stat) |
| `memgrep` | Pattern/keyword search in content |
| `memglob` | Glob file matching |
| `memadd` | Add remote URL or local file to OpenViking |
| `memremove` | Remove a `viking://` resource |
| `memqueue` | Check observer queue status |
| `memcommit` | Commit session for memory extraction |

### Code Tools

| Tool | Description |
|------|-------------|
| `codesearch` | Search symbol names across indexed code repositories |
| `codeoutline` | Show symbol structure of a source file |
| `codeexpand` | Return full source of a named symbol |

## Usage Examples

The agent uses these tools automatically when relevant. You can also request them directly:

```
"Search for authentication middleware in the fastapi repo"
→ Agent uses memsearch with target_uri=viking://resources/fastapi/

"Find where UserService is defined"
→ Agent uses codesearch query="UserService"

"Add https://github.com/tiangolo/fastapi to OpenViking"
→ Agent uses memadd with path and to arguments
```

## Auto-Recall

When enabled, the plugin automatically injects relevant memories into the conversation context. Configure thresholds in `autoRecall` settings.

## Session Management

The plugin automatically:
- Maps OpenCode sessions to OpenViking sessions
- Captures user and assistant messages
- Commits sessions at lifecycle boundaries for memory extraction

## Runtime Files

Default location: `~/.config/opencode/openviking/`

- `openviking-memory.log` — plugin debug logs
- `openviking-session-map.json` — session mapping state

Set `runtime.dataDir` in config to override.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tools not appearing | Verify plugin is in `opencode.json`, restart OpenCode |
| Connection errors | Check `endpoint` in config, ensure `openviking-server` is running |
| 401/403 errors | Verify `OPENVIKING_API_KEY` or account/user for trusted-mode |
| Empty search results | Confirm repos are indexed via `ov ls viking://resources/` |

## Migration from v0.x

If you were using the skill-based version:

1. Update to v1.0.0: `npm update openviking-opencode`
2. Remove any manual skill files from `~/.config/opencode/skills/openviking/`
3. Restart OpenCode

The agent will now use native tools automatically — no `skill("openviking")` calls needed.

## License

Apache-2.0

