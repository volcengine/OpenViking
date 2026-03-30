# Installing OpenViking for OpenClaw

Use [OpenViking](https://github.com/volcengine/OpenViking) as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw). After installation, OpenClaw will automatically remember important facts from conversations and recall relevant context before replying.

> This document covers the current OpenViking plugin built on OpenClaw's `context-engine` architecture.

## Prerequisites

| Component | Required Version |
| --- | --- |
| Python | >= 3.10 |
| Node.js | >= 22 |
| OpenClaw | >= 2026.3.7 |

Quick check:

```bash
python3 --version
node -v
openclaw --version
```

## Legacy Upgrade Note

If you previously installed the legacy `memory-openviking` plugin, remove it first, then continue with the install or upgrade commands below.

- The new `openviking` plugin is not compatible with the legacy `memory-openviking` plugin.
- If you never installed the legacy plugin, skip this section.

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

## Install

The recommended path is `npm` + `ov-install`.

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

Common variant:

```bash
ov-install --workdir ~/.openclaw-second
```

## Upgrade

To upgrade both OpenViking and the plugin to the latest version:

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
| `--version VER` | Set both plugin version and OpenViking version. For example, `0.2.9` maps to plugin `v0.2.9` |
| `--current-version` | Print the currently installed plugin version and OpenViking version |
| `--plugin-version REF` | Set only the plugin version. Supports tag, branch, or commit |
| `--openviking-version VER` | Set only the PyPI OpenViking version |
| `--github-repo owner/repo` | Use a different GitHub repository for plugin files. Default: `volcengine/OpenViking` |
| `--update` | Upgrade only the plugin, without upgrading the OpenViking runtime |
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

### Local Mode

Use this mode when the OpenClaw plugin should start and manage a local OpenViking process.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `mode` | `local` | Start a local OpenViking process |
| `agentId` | `default` | Logical identifier used by this OpenClaw instance in OpenViking |
| `configPath` | `~/.openviking/ov.conf` | Path to the local OpenViking config file |
| `port` | `1933` | Local OpenViking HTTP port |

In `local` mode, service-side settings such as VLM, embedding, API keys, and storage live in `~/.openviking/ov.conf`, not in the OpenClaw plugin config. The most important `ov.conf` fields are:

| Config Key | Meaning |
| --- | --- |
| `vlm.api_key` / `vlm.model` / `vlm.api_base` | VLM used for memory extraction |
| `embedding.dense.api_key` / `embedding.dense.model` / `embedding.dense.api_base` | Embedding model used for vectorization |
| `server.port` | OpenViking service port |

Common local-mode settings:

```bash
openclaw config set plugins.entries.openviking.config.mode local
openclaw config set plugins.entries.openviking.config.configPath ~/.openviking/ov.conf
openclaw config set plugins.entries.openviking.config.port 1933
```

### Remote Mode

Use this mode when you already have a running OpenViking server and want OpenClaw to connect to it.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `mode` | `remote` | Connect to an existing OpenViking server |
| `baseUrl` | `http://127.0.0.1:1933` | Remote OpenViking HTTP endpoint |
| `apiKey` | empty | Optional OpenViking API key |
| `agentId` | `default` | Logical identifier used by this OpenClaw instance on the remote server |

Common remote-mode settings:

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agentId your-agent-id
```

## Start

After installation:

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

Windows PowerShell:

```powershell
. "$HOME/.openclaw/openviking.env.ps1"
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

## Uninstall

To remove only the OpenClaw plugin and keep the OpenViking runtime:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

For a non-default OpenClaw state directory:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh --workdir ~/.openclaw-second
```

To also remove the local OpenViking runtime and data:

```bash
python3 -m pip uninstall openviking -y && rm -rf ~/.openviking
```

---

See also: [INSTALL-ZH.md](./INSTALL-ZH.md)
