# Migration Guide: claude-memory-plugin → claude-code-memory-plugin

This guide helps you migrate from the legacy `claude-memory-plugin` to the new `claude-code-memory-plugin`.

## Key Differences

| Aspect | Old Plugin (`claude-memory-plugin`) | New Plugin (`claude-code-memory-plugin`) |
|--------|--------------------------------------|-------------------------------------------|
| **Architecture** | Bash hooks + Python bridge (`ov_memory.py`) | Native MCP server + Node.js hooks |
| **Auto-Recall** | Skill-based, requires explicit invocation | Transparent, injected via `UserPromptSubmit` hook |
| **Memory Capture** | Accumulates on `Stop`, commits on `SessionEnd` (async) | Incremental capture on each `Stop` (sync, safer) |
| **Crash Safety** | Risk of losing accumulated turns if Claude crashes before `SessionEnd` | Each turn persisted independently — no data loss |
| **Type Safety** | Shell + Python, no compile-time checks | TypeScript MCP server, compiled JS artifact |
| **Tool Discovery** | Custom skill file | Standard MCP tools (`memory_recall`, `memory_store`, etc.) |
| **Config Scope** | Project-local (`./ov.conf`) | Global server config plus dedicated Claude Code client config |
| **Runtime Bootstrap** | Manual setup required | Auto-bootstrap on first `SessionStart` into `${CLAUDE_PLUGIN_DATA}` |

### Why the New Model is Better

1. **Incremental Capture = Crash-Resilient**: The old approach batches all turns until `SessionEnd`. If Claude crashes or the user kills the session, those memories are lost. The new plugin extracts on every `Stop`, so each turn is independently persisted.

2. **Transparent Auto-Recall**: No skill invocation needed — memories are automatically injected into context via `systemMessage` on every prompt. This matches the mental model of "memory just works."

3. **MCP Native**: Standard protocol, better tooling support, type-safe TypeScript implementation. Other MCP-compatible clients can potentially reuse the same server.

4. **Cleaner Configuration Split**: Server settings stay in `~/.openviking/ov.conf`, while Claude Code client behavior and remote connection settings move to `~/.openviking/claude-code-memory-plugin/config.json`.

## Capture Strategy: User Messages Only by Default

One design choice worth explaining in detail:

### Why Auto-Capture User Messages Only

In practice, auto-capturing assistant turns in coding scenarios has proven problematic:

1. **VLM Token Cost**: Each capture triggers an LLM extraction call to OpenViking. Capturing every assistant turn (including tool calls, sub-agent outputs, etc.) quickly becomes expensive.

2. **Signal-to-Noise Ratio**: Claude Code's assistant messages often contain verbose tool outputs, file contents, and nested sub-agent traces. These rarely contain memorable personal context — the valuable signal is typically in the *user's* questions, decisions, and stated preferences.

3. **Coding Workflow Nature**: For coding tasks, "memory" is often about:
   - User preferences ("use tabs not spaces", "prefer arrow functions")
   - Project context ("this repo uses bun, not npm")
   - Decisions made ("let's skip the refactor for now")
   
   These almost always originate from the *user*, not the assistant.

### Current Default

The plugin defaults to `captureAssistantTurns: false` — only user messages are considered for auto-capture. This keeps token usage manageable while still preserving the most valuable context.

Users can opt-in to full capture by setting `"captureAssistantTurns": true` in the Claude Code client config if they want assistant turns included (e.g., for non-coding workflows where assistant insights matter).

## Migration Steps

### 1. Move Server Config to the Global Location

Move your config from project root to the global location:

```bash
# Old location (per-project)
./ov.conf

# New location (global)
~/.openviking/ov.conf
```

If you have project-specific configs, merge them into the global server config.

### 2. Create Claude Code Client Config

Create the dedicated Claude Code client config file:

```bash
mkdir -p ~/.openviking/claude-code-memory-plugin
cat > ~/.openviking/claude-code-memory-plugin/config.json <<'EOF'
{
  "mode": "local",
  "agentId": "claude-code",
  "recallLimit": 6,
  "captureMode": "semantic",
  "captureAssistantTurns": false
}
EOF
```

For remote deployments, switch to:

```json
{
  "mode": "remote",
  "baseUrl": "https://your-openviking.example.com",
  "apiKey": "<your-api-key>",
  "agentId": "claude-code",
  "recallLimit": 6,
  "captureMode": "semantic",
  "captureAssistantTurns": false
}
```

In `local` mode, you may set `apiKey` explicitly in client config. If omitted, the plugin
falls back to `server.root_api_key` from `~/.openviking/ov.conf` (or the path pointed to by
`OPENVIKING_CONFIG_FILE`).

In `remote` mode, `baseUrl` is required.

### 3. Install Node.js Runtime

The new plugin requires Node.js. Ensure Node.js 18+ is installed:

```bash
node --version  # Should be 18.x or higher
```

### 4. Remove Old Plugin, Install New Plugin

```bash
# Uninstall old plugin (if installed via marketplace)
/plugin uninstall openviking-memory

# Install new plugin
/plugin marketplace add Castor6/openviking-plugins
/plugin install claude-code-memory-plugin@openviking-plugin
```

### 5. Remove Per-Project Plugin Files

Delete the old plugin directory from your project if it was checked in:

```bash
rm -rf ./.openviking/memory/
```

### 6. Move Plugin-Specific Overrides Out of `ov.conf`

If you previously stored plugin-specific behavior under `claude_code` in `ov.conf`, move those
fields into `~/.openviking/claude-code-memory-plugin/config.json`:

```json
{
  "mode": "local",
  "agentId": "claude-code",
  "recallLimit": 6,
  "captureMode": "semantic",
  "captureTimeoutMs": 30000,
  "captureAssistantTurns": false
}
```

## Behavior Changes

### Memory Recall

| Old Plugin | New Plugin |
|------------|------------|
| Run `/memory-recall <query>` manually | Automatic recall on every prompt |
| Skill-based, requires Claude tool call | Transparent, injected as system context |

The new plugin automatically injects relevant memories into the context before each prompt. No manual action needed.

### Memory Capture

| Old Plugin | New Plugin |
|------------|------------|
| Captures at `SessionEnd` (batch) | Captures at `Stop` (incremental) |
| Requires explicit commit | Automatic, real-time |

The new plugin captures memories incrementally after each turn, providing faster feedback and better persistence.

## MCP Tools (New Feature)

The new plugin provides MCP tools for explicit memory operations:

| Tool | Description |
|------|-------------|
| `memory_recall` | Manual semantic search |
| `memory_store` | Manually store a memory |
| `memory_forget` | Delete memories by URI or query |
| `memory_health` | Check server status |

These tools are available when you need explicit control over memory operations.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| MCP tools not available | Start a new Claude session; first run installs runtime deps |
| No memories recalled | Ensure OpenViking server is running: `openviking-server` |
| Hook timeout | Increase timeout in `hooks/hooks.json` |
