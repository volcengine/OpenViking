# OpenClaw Plugin

Use OpenViking as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw). After installation, OpenClaw will automatically remember important facts from conversations and recall relevant context before replying.

This plugin is registered as the `openviking` context engine — it owns long-term memory retrieval, session archiving, archive summaries, and memory extraction across the OpenClaw lifecycle.

Source: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## Prerequisites

| Component | Required Version |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.3.7 |

The plugin connects to an existing OpenViking server. Make sure you have one reachable over HTTP — see the [Deployment Guide](../guides/03-deployment.md). Quick check:

```bash
node -v
openclaw --version
```

> **Upgrading from the legacy `memory-openviking` plugin?** It's not compatible with the new `openviking` plugin. Run the cleanup script first:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
> bash cleanup-memory-openviking.sh
> ```

## Install via ClawHub (recommended)

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

Then run the interactive setup wizard:

```bash
openclaw openviking setup
```

The wizard prompts for your remote OpenViking server URL and optional API key, then writes configuration to `$OPENCLAW_STATE_DIR/openclaw.json` (default: `~/.openclaw/openclaw.json`).

Restart the gateway:

```bash
openclaw gateway restart
```

## Install via `ov-install` (alternative)

The `ov-install` helper automates plugin deployment:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

Common variants:

```bash
# Target a specific OpenClaw data directory
ov-install --workdir ~/.openclaw-second

# Pin to a specific plugin release
ov-install -y --version 0.2.9
```

To upgrade later:

```bash
npm install -g openclaw-openviking-setup-helper@latest && ov-install -y
```

### `ov-install` parameters

| Parameter                  | Meaning                                                            |
| -------------------------- | ------------------------------------------------------------------ |
| `--workdir PATH`           | Target OpenClaw data directory                                     |
| `--version VER`            | Set plugin version (e.g. `0.2.9` → plugin `v0.2.9`)                |
| `--current-version`        | Print the currently installed plugin version                       |
| `--plugin-version REF`     | Set plugin version — auto-detected: npm version (e.g. `2026.5.8`) or npm dist-tag (e.g. `dev`) resolves via npm; other refs (e.g. `v0.3.16`, `main`) resolve via the GitHub repo; default `npm latest` |
| `--plugin-source=npm\|github` | Plugin download source (default `npm`)                            |
| `--plugin-package=NAME`    | npm plugin package name (default `@openviking/openclaw-plugin`)    |
| `--github-repo owner/repo` | Use a different GitHub repo for plugin files (default `volcengine/OpenViking`) |
| `--update`                 | Upgrade only the plugin                                            |
| `-y`                       | Non-interactive mode, use default values                           |

## Plugin configuration

The plugin configuration lives under `plugins.entries.openviking.config`. Setup usually writes this for you — manual edits are only needed if you change servers later.

```bash
openclaw config get plugins.entries.openviking.config
```

| Parameter      | Default                  | Meaning                                                  |
| -------------- | ------------------------ | -------------------------------------------------------- |
| `baseUrl`      | `http://127.0.0.1:1933`  | Remote OpenViking HTTP endpoint                          |
| `apiKey`       | empty                    | Optional OpenViking API key                              |
| `agent_prefix` | `default`                | Agent prefix used by this OpenClaw instance on the server |

Common settings:

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agent_prefix your-prefix
```

## Verify

For a one-shot health check covering plugin registration, server connectivity, and version compatibility, run:

```bash
openclaw openviking status
```

For automation, append `--json` to `openclaw openviking status` to get a machine-readable result. `openclaw openviking setup` also supports `--json` when used with `--base-url`.

Or check the underlying signals manually. The plugin owns the `contextEngine` slot:

```bash
openclaw config get plugins.slots.contextEngine
```

If the output is `openviking`, the plugin is active.

Follow OpenClaw logs for the registration message:

```bash
openclaw logs --follow
# expect: openviking: registered context-engine
```

OpenViking server log (default location):

```bash
cat ~/.openviking/data/log/openviking.log
```

Currently-installed plugin version:

```bash
ov-install --current-version
```

### Pipeline health check (optional)

For an end-to-end sanity check (Gateway → OpenViking pipeline), run:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

This script injects a real conversation through Gateway and verifies from the OpenViking side that the session was captured, committed, archived, and had memories extracted. See [HEALTHCHECK.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/health_check_tools/HEALTHCHECK.md) for details.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

For a non-default OpenClaw state directory, append `--workdir ~/.openclaw-second`.

## See also

- [Full install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — every install path, parameter, and verification step
- [Plugin design notes](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — architecture, identity & routing, hook lifecycle
- [Agent operator guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — for agents driving installation on behalf of a user