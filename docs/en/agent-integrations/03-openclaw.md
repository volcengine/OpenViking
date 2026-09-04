# OpenClaw Plugin

Add long-term memory to [OpenClaw](https://github.com/openclaw/openclaw). After installation, OpenClaw automatically remembers important facts from conversations and recalls relevant context before every reply.

Source: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## Prerequisites

| Component | Required Version |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.5.27 |

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
| `--peer-role ROLE` | Memory scope: `none`, `assistant`, or `sender` (`person` is a legacy alias) |
| `--uninstall` | Uninstall the plugin |

Full parameter list in the [install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md).

</details>

## Choose the Memory Scope

`peer_role` decides whether long-term memory is shared at the OpenViking user level or attributed to a concrete peer:

| Value | Memory layout | Use case |
| --- | --- | --- |
| `none` (default) | Shared memory at `viking://user/<user_id>/memories/...`; no peer-specific memory subtree is used | General-purpose setup where all conversations for this OpenViking user share user-level memory |
| `assistant` | Assistant-attributed peer memory at `viking://user/<user_id>/peers/<assistant_id>/memories/...` | **Human as OpenViking user**: separate the peer memories of assistants such as `main` and `research` |
| `sender` | Sender-attributed peer memory at `viking://user/<user_id>/peers/<sender_id>/memories/...` | **Agent as OpenViking user**: separate the peer memories of senders such as `customer-42` and `customer-99` |

For example:

```bash
# Alice is the OpenViking user; separate memories by OpenClaw assistant.
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --peer-role assistant --json

# support-agent is the OpenViking user; separate memories by human sender.
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --peer-role sender --json
```

New configuration should use `sender`; existing `peer_role=person` configurations remain compatible and are treated as `sender`. OpenViking initializes the managed `peers/` container for every user, so `none` means that no concrete `peers/<peer_id>/memories` subtree is used. Actor-peer recall includes shared user memory plus the current peer memory, and changing the scope does not move existing memories.

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
| `peer_role` | `none` | `none`, `assistant`, or `sender`; legacy `person` is accepted as `sender` |
| `peer_prefix` | empty | Optional prefix for assistant peer identity when `peer_role=assistant` |
| `autoRecallTimeoutMs` | `5000` | Outer timeout (ms) for the whole auto-recall flow; increase for slow local embedding hardware (clamped 1000–300000) |

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

- [Capability Reference](./16-capability-reference.md)
- [Full install guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — every install path and parameter
- [Plugin design notes](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — architecture, identity & routing, hook lifecycle
- [Agent operator guide](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — for agents driving installation on behalf of a user
