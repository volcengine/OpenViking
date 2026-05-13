# Other Plugins

The repo also ships several community/experimental plugins beyond the headline Claude Code and OpenClaw integrations. They differ in target runtime, integration depth, and maintenance status — read each one's README before adopting.

## Codex Memory Plugin

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

[Codex](https://github.com/openai/codex) integration with lifecycle hooks plus OpenViking's native `/mcp` endpoint for explicit tools. It follows the same install-first shape as the [Claude Code integration](./02-claude-code.md), but uses Codex hook events.

### Install

Recommended one-line installer:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

It installs from a local `openviking-plugins-local` marketplace, enables `openviking-memory@openviking-plugins-local`, sets `features.plugin_hooks = true`, optionally registers `mcp_servers.openviking` against the OpenViking server's native `/mcp` endpoint, and uses `~/.openviking/ovcli.conf` for the OpenViking connection when present.

Native MCP is recommended but optional. In an interactive shell the installer asks whether to enable it; set `OPENVIKING_CODEX_ENABLE_MCP=0` for a hooks-only install, which removes the installer-managed `mcp_servers.openviking` entry if one exists.

Manual setup:

```bash
node --version    # >= 22
codex --version   # >= 0.124.0
codex features list | grep codex_hooks
```

Enable plugin lifecycle hooks:

```toml
[features]
plugin_hooks = true
```

From an OpenViking checkout:

```bash
mkdir -p /tmp/ov-codex-mp/.claude-plugin
ln -s "$(pwd)/examples/codex-memory-plugin" /tmp/ov-codex-mp/openviking-memory
cat > /tmp/ov-codex-mp/.claude-plugin/marketplace.json <<'EOF'
{
  "name": "openviking-plugins-local",
  "plugins": [
    { "name": "openviking-memory", "source": "./openviking-memory" }
  ]
}
EOF

codex plugin marketplace add /tmp/ov-codex-mp
cat >> ~/.codex/config.toml <<'EOF'

[plugins."openviking-memory@openviking-plugins-local"]
enabled = true
EOF
```

Optional native MCP registration:

```toml
[mcp_servers.openviking]
url = "https://ov.example.com/mcp"
bearer_token_env_var = "OPENVIKING_API_KEY"
startup_timeout_sec = 30
tool_timeout_sec = 120

[mcp_servers.openviking.http_headers]
"X-OpenViking-Account" = "default"
"X-OpenViking-User" = "<your-user>"
"X-OpenViking-Agent" = "codex"
```

For local development, pre-populate Codex's cache so it resolves immediately:

```bash
INSTALL_DIR=~/.codex/plugins/cache/openviking-plugins-local/openviking-memory
mkdir -p "$INSTALL_DIR"
cp -R "$(pwd)/examples/codex-memory-plugin" "$INSTALL_DIR/0.4.0"
```

No npm install or build step is required; the plugin scripts are plain Node.js modules and explicit MCP tools come from OpenViking's native `/mcp` endpoint.

### Configure

Use `~/.openviking/ovcli.conf`, shared with the `ov` CLI:

```jsonc
{
  "url": "https://ov.example.com",
  "api_key": "<your-key>",
  "account": "default",
  "user": "<your-user>"
}
```

Environment variables win over files for hooks. Use `OPENVIKING_CLI_CONFIG_FILE` for an alternate `ovcli.conf`; `OPENVIKING_API_KEY` and `OPENVIKING_BEARER_TOKEN` are equivalent.

Codex's native HTTP MCP transport does not read `ovcli.conf`, so the installer writes a literal `/mcp` URL and header block to `~/.codex/config.toml`. Keep the configured bearer env var, usually `OPENVIKING_API_KEY`, set when starting Codex.

### What it does

- Auto-recall on `UserPromptSubmit`
- Incremental capture on `Stop`
- Commit before compaction on `PreCompact`
- Orphan cleanup on `SessionStart` startup/clear
- Native OpenViking MCP tools such as `search`, `read`, `list`, `store`, `add_resource`, `grep`, `glob`, `forget`, and `health`

Full behavior and validation details are in the [plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin).

## OpenCode plugins

Two OpenCode plugin variants exist with different design choices. Pick whichever matches your usage — we don't make the decision for you.

### `opencode-memory-plugin` — explicit-tool variant

Source: [examples/opencode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)

Exposes OpenViking memories as explicit OpenCode tools and syncs the conversation session into OpenViking.

- the agent sees concrete tools and decides when to call them
- OpenViking data is fetched on demand via tool execution, not pre-injected into every prompt
- the plugin keeps an OpenViking session in sync with the OpenCode conversation and triggers background extraction with `memcommit`

### `opencode/plugin` — context-injection variant

Source: [examples/opencode/plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)

Injects indexed code repos into OpenCode's context and auto-starts the OpenViking server when needed.

- prompt context is augmented with relevant code from indexed repos
- bundles a small launcher that brings up the OpenViking server on demand

## Generic MCP clients

For Cursor, Trae, Manus, Claude Desktop, ChatGPT/Codex, and any other MCP-compatible runtime, you don't need a dedicated plugin — just point the client at the built-in `/mcp` endpoint.

→ See the [MCP Integration Guide](../guides/06-mcp-integration.md).
