# Installing OpenViking for OpenClaw

Use [OpenViking](https://github.com/volcengine/OpenViking) as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw). After installation, OpenClaw will automatically remember important facts from conversations and recall relevant context before replying.

> This document covers the current OpenViking plugin built on OpenClaw's `context-engine` architecture.

## Prerequisites

| Component | Required Version |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.4.24 |

The plugin connects to an existing OpenViking server. It does not start the OpenViking server for you. Start OpenViking first, keep it running, then point the plugin `baseUrl` at that HTTP service. The default local URL is `http://127.0.0.1:1933`.

Quick check:

```bash
node -v
openclaw --version
```

## Start OpenViking Server

For a local OpenViking server on the same machine as OpenClaw:

```bash
pip install openviking --upgrade --force-reinstall
openviking-server init
openviking-server doctor
openviking-server
```

`openviking-server init` writes the server configuration, `openviking-server doctor` validates local model/provider auth, and `openviking-server` starts the HTTP API. Keep this process running while OpenClaw uses the plugin.

To run the server in the background:

```bash
mkdir -p ~/.openviking/data/log
nohup openviking-server > ~/.openviking/data/log/openviking.log 2>&1 &
```

If OpenViking runs on another machine, start it on a reachable host/port, for example:

```bash
openviking-server --host 0.0.0.0 --port 1933
```

Then configure the OpenClaw plugin `baseUrl` to that address, such as `http://your-server:1933`.

Verify the server before installing or restarting the plugin:

```bash
curl http://127.0.0.1:1933/health
```

## Legacy Upgrade Note

If you previously installed the legacy `memory-openviking` plugin, remove it first, then continue with the install or upgrade commands below.

- The new `openviking` plugin is not compatible with the legacy `memory-openviking` plugin.
- If you never installed the legacy plugin, skip this section.

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

## Install via ClawHub (Recommended)

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

After installation, run the interactive setup wizard:

```bash
openclaw openviking setup
```

The wizard prompts for your remote OpenViking server URL and optional API key, then writes configuration to `$OPENCLAW_STATE_DIR/openclaw.json` (default: `~/.openclaw/openclaw.json`).

## Install via ov-install (Alternative)

The `ov-install` helper automates plugin deployment:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

Common variant:

```bash
ov-install --workdir ~/.openclaw-second
```

## Upgrade

To upgrade the plugin to the latest version:

```bash
npm install -g openclaw-openviking-setup-helper@latest && ov-install -y
```

## Install or Upgrade a Specific Release

To install or upgrade to a specific release:

```bash
ov-install -y --version 0.2.9
```

## Parameters

| Parameter | Meaning |
| --- | --- |
| `--workdir PATH` | Target OpenClaw data directory |
| `--version VER` | Set plugin version. For example, `0.2.9` maps to plugin `v0.2.9` |
| `--current-version` | Print the currently installed plugin version |
| `--plugin-version REF` | Set only the plugin version. Supports tag, branch, or commit |
| `--github-repo owner/repo` | Use a different GitHub repository for plugin files. Default: `volcengine/OpenViking` |
| `--update` | Upgrade only the plugin |
| `-y` | Non-interactive mode, use default values |

If you need to pin the installer itself:

```bash
npm install -g openclaw-openviking-setup-helper@VERSION
```

## OpenClaw Plugin Configuration

The plugin configuration lives under `plugins.entries.openviking.config`.

Get the current full plugin configuration:

```bash
openclaw config get plugins.entries.openviking.config
```

### Configuration Parameters

The plugin connects to an existing remote OpenViking server.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:1933` | Remote OpenViking HTTP endpoint |
| `apiKey` | empty | Optional OpenViking API key |
| `agent_prefix` | empty | Optional prefix for OpenClaw agent IDs. If no agent ID is available, the plugin uses `main`. Interactive setup accepts only letters, digits, `_`, and `-` |

Common settings:

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agent_prefix your-prefix
```

## Start

After installation:

```bash
openclaw gateway restart
```

Windows PowerShell:

```powershell
openclaw gateway restart
```

## Verify

Check that the plugin owns the `contextEngine` slot:

```bash
openclaw config get plugins.slots.contextEngine
```

If the output is `openviking`, the plugin is active.

Follow OpenClaw logs:

```bash
openclaw logs --follow
```

If you see `openviking: registered context-engine`, the plugin loaded successfully.

Check the OpenViking service log:

By default the log file lives under `workspace/data/log/openviking.log`. With the default setup this is usually:

```bash
cat ~/.openviking/data/log/openviking.log
```

Check installed versions:

```bash
ov-install --current-version
```

### Pipeline Health Check (Optional)

If the steps above all look good and you want to further verify the full Gateway → OpenViking pipeline, run the plugin's health check script:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

This script injects a real conversation through Gateway and then verifies from the OpenViking side that the session was captured, committed, archived, and had memories extracted. See [health_check_tools/HEALTHCHECK.md](./health_check_tools/HEALTHCHECK.md) for full details.

## Uninstall

To remove the OpenClaw plugin:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

For a non-default OpenClaw state directory:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh --workdir ~/.openclaw-second
```

---

See also: [INSTALL-ZH.md](./INSTALL-ZH.md)
