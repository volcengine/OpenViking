Add persistent, cross-session memory and indexed repository context to [OpenCode](https://opencode.ai/). Install it once, and the plugin will automatically recall memories on every prompt, capture turns into OpenViking sessions, and commit before compaction. Model-callable tools come from the same OpenViking MCP proxy used by the Claude Code and Codex plugins.

Source: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

OpenCode shares this one installer with Claude Code and Codex. It asks which tools to install, which source to use (GitHub or the TOS mirror), your language (English/中文), and OpenViking credentials; each step is idempotent, so it is safe to rerun. On the TOS channel the plugin is installed as local files — rerun the installer to update.

<details>
<summary><b>Manual installation</b></summary>

Prerequisites: OpenCode, Node.js 18+, and a reachable OpenViking server (`curl http://localhost:1933/health`).

1. **Configure the connection** - write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or run the bundled wizard `node <plugin-dir>/scripts/setup.mjs` after installing.

2. **Register the npm plugin** (needs npm registry access) — merge `"@openviking/opencode-plugin"` into the `plugin` array of `~/.config/opencode/opencode.json`:

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "plugin": ["@openviking/opencode-plugin"]
   }
   ```

   OpenCode downloads the package at startup, and the plugin registers its `openviking` MCP server automatically.

</details>

## Step 2: Verify

Restart OpenCode. The plugin exposes MCP tools with the `openviking_` prefix, for example `openviking_search`, `openviking_read`, `openviking_remember`, `openviking_health`. Ask OpenCode to search or browse OpenViking memory.

Behavior knobs (recall limits, commit thresholds) live in `~/.config/opencode/openviking-config.json`; credentials come from `~/.openviking/ovcli.conf` or `OPENVIKING_*` environment variables. Runtime logs:

```bash
~/.config/opencode/openviking/openviking-memory.log
~/.config/opencode/openviking/openviking-session-state.json
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Plugin is not loaded | Check `~/.config/opencode/opencode.json` references `@openviking/opencode-plugin`, or `~/.config/opencode/plugins/openviking.js` for file installs |
| MCP tools call the wrong server | Check `~/.openviking/ovcli.conf`, or set `OPENVIKING_*` env vars / `OPENVIKING_PLUGIN_CONFIG` |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; trusted-mode deployments also need `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Recall is empty | Confirm OpenViking has memories/resources and `autoRecall.enabled` is `true` |

## Reference docs

- [Plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin) - full tool list, configuration fields, and runtime details
- [Deployment Guide](https://www.openviking.ai/en/guides/03-deployment) - setting up OpenViking server and CLI config
