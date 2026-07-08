# Community Plugins

Community-maintained integrations for various agent runtimes. Each differs in target platform, integration depth, and maintenance status — check the linked README before adopting.

## AstrBot plugin

[AstrBot](https://github.com/AstrBotDevs/AstrBot) is a multi-platform IM bot framework supporting QQ, Telegram, Discord, Lark, and 20+ other platforms.

Source: [astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

Provides auto-capture of group/DM conversations, semantic recall before each LLM request, and configurable venue memory isolation.

**Install**: In AstrBot WebUI, search **OpenViking Memory** in the Plugin Marketplace; or install from URL: `https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

**Key features**:

- Auto-recall and auto-capture via hooks — the model doesn't need to invoke tools
- Three isolation modes: `venue_user` (per-group/DM), `venue_user_fanout` (cross-venue sharing), `global_user` (single user)
- Four auto-commit triggers: message count, token threshold, idle timeout, and process-exit flush
- Backfills platform message history on first venue encounter

## OpenCode plugin

OpenViking provides one unified OpenCode plugin for repository context and long-term memory workflows.

Source: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

The plugin combines indexed repository context, session synchronization, lifecycle commit, and automatic recall through OpenCode plugin hooks. Model-callable tools come from the same OpenViking stdio MCP proxy used by the Claude Code and Codex memory plugins.

### Prerequisites

- [OpenCode](https://opencode.ai/)
- Node.js 18+
- An OpenViking HTTP server
- An OpenViking API key when your server requires authentication

Start your OpenViking server first:

```bash
openviking-server --config ~/.openviking/ov.conf
```

In another terminal, check the service:

```bash
curl http://localhost:1933/health
```

### Install

The published npm package is `@openviking/opencode-plugin`. For a first-time OpenCode config:

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@openviking/opencode-plugin"]
}
JSON
opencode
```

If `~/.config/opencode/opencode.json` already exists, do not overwrite it; only merge `"@openviking/opencode-plugin"` into the existing `plugin` array. OpenCode downloads the npm package at startup.

If package installation is not available in your environment, use the source install path below.

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

This source install creates the layout OpenCode can discover:

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

The top-level `openviking.js` is only a wrapper that forwards OpenCode's first-level plugin entry to the installed package directory.
Use the `.js` wrapper for source installs; OpenCode's local plugin scanner discovers JavaScript/TypeScript plugin files.

### Configure

Credentials are shared with the Claude Code and Codex memory plugins. Run the setup wizard once, or set `OPENVIKING_*` environment variables:

```bash
node examples/opencode-plugin/scripts/setup.mjs
```

`~/.config/opencode/openviking-config.json` is now for behavior knobs only:

```json
{
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.35,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000,
    "minQueryLength": 3
  },
  "commitTokenThreshold": 20000,
  "commitKeepRecentCount": 10,
  "profileTokenBudget": 10000,
  "resumeContextBudget": 32000
}
```

Environment variables override `ovcli.conf`:

```bash
export OPENVIKING_API_KEY="your-api-key-here"
export OPENVIKING_ACCOUNT="default"   # optional, trusted-mode deployments only
export OPENVIKING_USER="opencode"     # optional, trusted-mode deployments only
export OPENVIKING_PEER_ID="opencode"  # optional, peer-scoped memory routing
```

API keys are sent as `Authorization: Bearer ...` by both hooks and the MCP proxy. `account` and `user` are trusted-mode headers; `peerId` is sent as `X-OpenViking-Actor-Peer` and as `peer_id` on captured session messages. Existing `openviking-config.json` credential fields are still read as a migration fallback, but new installs should use `ovcli.conf` or env vars.

### Verify

Restart OpenCode after installation. In an OpenCode session, the plugin should expose the `openviking` MCP server. OpenCode namespaces MCP tools as `openviking_*`, for example:

- `openviking_recall`, `openviking_search`, `openviking_find`
- `openviking_read`, `openviking_list`, `openviking_grep`, `openviking_glob`
- `openviking_remember`, `openviking_add_resource`, `openviking_forget`, `openviking_health`
- `openviking_code_search`, `openviking_code_outline`, `openviking_code_expand`

Ask OpenCode to search or browse OpenViking memory. Runtime state and errors are written to:

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-state.json
```

### Troubleshooting

| Issue | What to check |
|-------|---------------|
| Plugin does not load | Confirm `~/.config/opencode/opencode.json` references `@openviking/opencode-plugin`, or that `~/.config/opencode/plugins/openviking.js` exists for source installs |
| MCP tools call the wrong server | Check `~/.openviking/ovcli.conf`, or set `OPENVIKING_*` env vars / `OPENVIKING_PLUGIN_CONFIG` to the intended config path |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments, also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Recall is empty | Confirm the OpenViking server has indexed memories/resources and that `autoRecall.enabled` is `true` |
| Local `openviking_add_resource` fails | Pass a file path, not a directory; local directories are not uploaded automatically yet |

For all available tools, configuration fields, and runtime file details, see the [plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin).

## pi coding agent extension

OpenViking also ships a native pi extension.

Source: [examples/pi-coding-agent-extension](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension)

The extension uses pi lifecycle events for session-start profile injection, current-prompt recall, turn capture, threshold commit, pre-compact commit, and shutdown commit. It keeps pi's native tool surface (`viking_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, `viking_add_resource`, `viking_archive_expand`) rather than MCP.

Install through the shared installer:

```bash
bash examples/memory-plugin-shared/install.sh --harness pi
```

Credentials are resolved from env vars, `~/.openviking/ovcli.conf`, then `~/.openviking/ov.conf`. The extension-local `config.json` only contains behavior knobs such as `recallTokenBudget`, `scoreThreshold`, `profileTokenBudget`, `resumeContextBudget`, and `commitTokenThreshold`.
