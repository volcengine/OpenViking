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

The plugin combines indexed repository context, OpenViking memory tools, session synchronization, lifecycle commit, and automatic recall through OpenCode plugin hooks. Use this plugin for both the former explicit-tool and context-injection use cases.

### Prerequisites

- [OpenCode](https://opencode.ai/)
- Node.js and npm
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

If you are using the published package, add the plugin to `~/.config/opencode/opencode.json`. If package installation is not available in your environment yet, use the source install path below.

```json
{
  "plugin": ["openviking-opencode-plugin"]
}
```

For development, debugging, or PR testing, install the plugin from this repository instead:

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.mjs ~/.config/opencode/plugins/openviking.mjs
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cd ~/.config/opencode/plugins/openviking
npm install
```

This source install creates the layout OpenCode can discover:

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

The top-level `openviking.mjs` is only a wrapper that forwards OpenCode's first-level plugin entry to the installed package directory.

### Configure

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

Prefer environment variables for secrets:

```bash
export OPENVIKING_API_KEY="your-api-key-here"
export OPENVIKING_ACCOUNT="default"   # optional, trusted-mode deployments only
export OPENVIKING_USER="opencode"     # optional, trusted-mode deployments only
export OPENVIKING_PEER_ID="opencode"  # optional, peer-scoped memory routing
```

Environment variables override `openviking-config.json`. `apiKey` is sent as `X-API-Key`; `account` and `user` are trusted-mode headers; `peerId` is sent as request-level `peer_id` for recall, search, and captured session messages.

### Verify

Restart OpenCode after installation. In an OpenCode session, the plugin should expose these tools:

- `memsearch`, `memread`, `membrowse`
- `memgrep`, `memglob`
- `memadd`, `memremove`, `memqueue`
- `memcommit`

Ask OpenCode to search or browse OpenViking memory, or request a manual session commit. Runtime state and errors are written to:

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-map.json
```

### Troubleshooting

| Issue | What to check |
|-------|---------------|
| Plugin does not load | Confirm `~/.config/opencode/opencode.json` references `openviking-opencode-plugin`, or that `~/.config/opencode/plugins/openviking.mjs` exists for source installs |
| Tools call the wrong server | Check `endpoint` in `~/.config/opencode/openviking-config.json`, or set `OPENVIKING_PLUGIN_CONFIG` to the intended config path |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments, also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Recall is empty | Confirm the OpenViking server has indexed memories/resources and that `autoRecall.enabled` is `true` |
| Local `memadd` fails | Pass a file path, not a directory; local directories are not uploaded automatically yet |

For all available tools, configuration fields, and runtime file details, see the [plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin).
