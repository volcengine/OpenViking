OpenViking provides one unified OpenCode plugin for repository context and long-term memory workflows.

## `opencode-plugin`

Source: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

The plugin combines indexed repository context, OpenViking memory tools, session synchronization, lifecycle commit, and automatic recall through OpenCode plugin hooks.

## Step 1: Prepare OpenViking

Install OpenCode, Node.js/npm, and an OpenViking HTTP server. Start the server before launching OpenCode:

```bash
openviking-server --config ~/.openviking/ov.conf
```

In another terminal, check the service:

```bash
curl http://localhost:1933/health
```

For remote or multi-tenant deployments, prepare an OpenViking API key.

## Step 2: Install the plugin

For a published package install, add the plugin to `~/.config/opencode/opencode.json`. If package installation is not available in your environment yet, use the source install path below.

```json
{
  "plugin": ["openviking-opencode-plugin"]
}
```

For development, debugging, or PR testing, copy the plugin from the OpenViking repository:

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

The source install creates:

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

## Step 3: Configure the OpenViking connection

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

Environment variables override the config file.

## Step 4: Verify

Restart OpenCode. The plugin should expose `memsearch`, `memread`, `membrowse`, `memgrep`, `memglob`, `memadd`, `memremove`, `memqueue`, and `memcommit`.

Ask OpenCode to browse OpenViking or commit the current session. Check runtime logs if anything looks wrong:

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-map.json
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Plugin is not loaded | Check `~/.config/opencode/opencode.json` for package installs, or `~/.config/opencode/plugins/openviking.mjs` for source installs |
| Tools call the wrong server | Check `endpoint`, or set `OPENVIKING_PLUGIN_CONFIG` to the intended config path |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; trusted-mode deployments also need `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Recall is empty | Confirm OpenViking has memories/resources and `autoRecall.enabled` is `true` |

## Reference docs

- [Plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin) - full tool list, configuration fields, and runtime details
- [Deployment Guide](https://www.openviking.ai/en/guides/03-deployment) - setting up OpenViking server and CLI config
