# OpenClaw Plugin

Add long-term memory to [OpenClaw](https://github.com/openclaw/openclaw). After installation, OpenClaw automatically remembers important facts from conversations and recalls relevant context before every reply.

Source: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## Prerequisites

| Component | Required Version |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.4.8 |

The plugin connects to a running OpenViking server — see the [Deployment Guide](../guides/03-deployment.md) if you need one.

<details>
<summary><b>Upgrading from the legacy <code>memory-openviking</code> plugin?</b></summary>

The old plugin is not compatible. Run the cleanup script first:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

</details>

## Install

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --json
openclaw gateway restart
```

The `setup` wizard writes configuration and activates the plugin. After install, start a conversation — OpenClaw will begin remembering and recalling automatically.

<details>
<summary><b>Alternative: install via <code>ov-install</code></b></summary>

If ClawHub is unavailable:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install --base-url http://your-server:1933
```

Key parameters:

| Parameter | Meaning |
| --- | --- |
| `--workdir PATH` | OpenClaw data directory (default `~/.openclaw`) |
| `--plugin-version=VER` | Plugin version: npm version, dist-tag, or Git ref |
| `--base-url URL` | OpenViking server URL |
| `--api-key KEY` | OpenViking API key |
| `--uninstall` | Uninstall the plugin |

Full parameter list in the [install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md).

</details>

## Verify

```bash
openclaw openviking status
```

This checks plugin registration, server connectivity, and version compatibility in one command. Append `--json` for machine-readable output.

<details>
<summary><b>Manual verification</b></summary>

Check the plugin owns the `contextEngine` slot:

```bash
openclaw config get plugins.slots.contextEngine
# expect: openviking
```

For an end-to-end pipeline test:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

See [HEALTHCHECK.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/health_check_tools/HEALTHCHECK.md) for details.

</details>

<details>
<summary><b>Configuration</b></summary>

Plugin config lives under `plugins.entries.openviking.config`. Setup usually writes this for you.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking server endpoint |
| `apiKey` | empty | OpenViking API key |
| `agent_prefix` | `default` | Agent prefix for memory namespace isolation |

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
```

</details>

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

## See also

- [Full install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — every install path and parameter
- [Plugin design notes](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — architecture, identity & routing, hook lifecycle
- [Agent operator guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — for agents driving installation on behalf of a user
