# Install OpenViking for OpenClaw

OpenViking provides long-term memory, knowledge base search, semantic retrieval, and RAG-style context for OpenClaw through the `@openclaw/openviking` plugin.

This guide describes the current plugin install flow. It is written for both people and automation agents.

## Do Not Install The Skill By Mistake

`@openclaw/openviking` is an OpenClaw plugin.

Do not use this command for plugin installation:

```bash
clawhub install openviking
```

That command installs an AgentSkill named `openviking`, not the OpenClaw plugin.

Use this plugin command instead:

```bash
openclaw plugins install @openclaw/openviking
```

## Requirements

| Component | Required |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.4.8 |

The plugin connects to an existing OpenViking server. It does not start the OpenViking server for you. Start OpenViking first, keep it running, then point the plugin `baseUrl` at that HTTP service. The default local URL is `http://127.0.0.1:1933`.

OpenClaw plugin package boundaries:

- `2026.4.8` is the minimum supported OpenClaw version for the current plugin.
- `2026.5.3` starts validating package installs so TypeScript plugin entries need compiled JavaScript output.
- `2026.5.4` and later stop falling back to `.ts` source for installed/global plugin runtime loading when compiled JavaScript is missing.
- The recommended `openclaw plugins install @openclaw/openviking` path installs a published package that already includes `dist/*.js`.
- `ov-install` is the backup/source install path. Use it when ClawHub or the OpenClaw plugin manager path is unavailable/rate-limited, or when explicitly testing a source ref. For OpenClaw `>= 2026.5.3`, it builds the plugin during installation.

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

## Recommended Install Path

Use this path for normal users, production installs, and agent-assisted installs.

### 1. Install The Plugin

```bash
openclaw plugins install @openclaw/openviking
```

If your OpenClaw installation requires an explicit registry prefix, use:

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

### 2. Configure The Plugin

For interactive human setup:

```bash
openclaw openviking setup
```

For non-interactive agent setup:

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
```

Example:

```bash
openclaw openviking setup --base-url http://127.0.0.1:1933 --api-key sk-xxx --json
```

The setup command writes `plugins.entries.openviking.config` and activates `plugins.slots.contextEngine=openviking`.

If the OpenViking server is temporarily unreachable but you still want to save the config:

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --allow-offline --json
```

If your API key is a root key, setup may require tenant context:

```bash
openclaw openviking setup \
  --base-url <OPENVIKING_URL> \
  --api-key <ROOT_API_KEY> \
  --account-id <ACCOUNT_ID> \
  --user-id <USER_ID> \
  --json
```

If another context engine already owns the slot, setup will not replace it by default. To intentionally replace the current owner:

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --force-slot --json
```

If you need a custom agent routing prefix (optional; most users leave this empty):

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --agent-prefix <PREFIX> --json
```

### 3. Restart OpenClaw Gateway

```bash
openclaw gateway restart
```

If your OpenClaw version uses a different restart command, use the equivalent gateway restart for your environment.

### 4. Verify

```bash
openclaw openviking status --json
```

Expected result:

| JSON field | Expected value |
| --- | --- |
| `configured` | `true` |
| `slotActive` | `true` |
| `health.ok` | `true` when the server is reachable |

You can also inspect the raw OpenClaw config:

```bash
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

`plugins.slots.contextEngine` should be `openviking`.

## Agent Result Handling

Automation should prefer `--json` and branch on these fields:

| Result | Meaning | Recommended action |
| --- | --- | --- |
| `success: true` | Config saved and setup completed | Restart gateway, then run status |
| `success: false`, `action: "slot_blocked"` | Config may be saved, but another plugin owns `contextEngine` | Ask before rerunning with `--force-slot` |
| `success: false`, `action: "error"` | Validation failed | Show `error`; do not claim install succeeded |
| `health.ok: false` | Server unreachable | Check URL/server, or rerun with `--allow-offline` only if the user accepts |
| `keyProbe.keyType: "root_key"` | Root key needs tenant context | Rerun with `--account-id` and `--user-id` |

## Configuration Reference

The plugin config lives at:

```text
plugins.entries.openviking.config
```

Core fields:

| Field | Default | Description |
| --- | --- | --- |
| `mode` | `remote` | Legacy compatibility field. Only remote mode is supported. |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking HTTP endpoint |
| `apiKey` | empty | OpenViking API key |
| `agent_prefix` | empty | Optional prefix for OpenClaw agent IDs. If no agent ID is available, the plugin uses `main`. Interactive setup accepts only letters, digits, `_`, and `-`. |
| `accountId` | empty | Required when using a root API key |
| `userId` | empty | Required when using a root API key |

Use setup for normal changes when possible:

```bash
openclaw openviking setup --reconfigure
```

Manual config inspection:

```bash
openclaw config get plugins.entries.openviking.config
```

## Upgrade

```bash
openclaw plugins update openviking
openclaw gateway restart
openclaw openviking status --json
```

Confirm that `configured` and `slotActive` are both `true`.

## Uninstall

```bash
openclaw plugins uninstall openviking
openclaw config set plugins.slots.contextEngine legacy
openclaw gateway restart
```

Current OpenClaw native uninstall does not always reset `plugins.slots.contextEngine`. The explicit `config set` step avoids leaving the slot pointed at an uninstalled plugin.

## Optional Pipeline Health Check

After status passes, you can run the bundled end-to-end health check from a repository checkout:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

This checks the Gateway to OpenViking path by injecting a real conversation and verifying capture, commit, archive, and memory extraction. See [health_check_tools/HEALTHCHECK.md](./health_check_tools/HEALTHCHECK.md).

## Backup Path: ov-install

`ov-install` is the backup path, not the primary install path. Use it when `openclaw plugins install @openclaw/openviking` cannot reach ClawHub, is rate-limited, or when you explicitly need to install/test plugin files from a Git branch or source ref.

Try the OpenClaw plugin manager first. If that path is unavailable, run:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

Useful backup/source flags:

| Flag | Meaning |
| --- | --- |
| `--workdir PATH` | Target OpenClaw state directory |
| `--version REF` | Git ref, tag, branch, or release version to install |
| `--current-version` | Print the version tracked by the helper |
| `--base-url URL` | OpenViking server URL (enables non-interactive mode) |
| `--api-key KEY` | OpenViking API key |
| `--agent-prefix PREFIX` | Agent routing prefix |
| `--update` | Update an existing helper-managed install |

For user-facing installs, use `openclaw plugins install @openclaw/openviking` first. Choose `ov-install` only as the backup path.

## Migrate From ov-install To openclaw plugin install

If you previously installed OpenViking with `ov-install`, follow these steps before switching to the recommended `openclaw plugins install` path.

### Same Plugin ID (openviking, version >= 0.3.x)

The ov-install context-engine deployment writes files to `~/.openclaw/extensions/openviking/`. After installing via npm, OpenClaw may still load from the old directory. Clean it up:

```bash
# Remove ov-install deployed files
rm -rf ~/.openclaw/extensions/openviking/

# Install via the OpenClaw plugin manager
openclaw plugins install @openclaw/openviking

# Reconfigure (your existing config in openclaw.json is preserved)
openclaw openviking setup --reconfigure
openclaw gateway restart
openclaw openviking status --json
```

Your existing config fields (`baseUrl`, `apiKey`, `agentId`, etc.) are preserved. The new plugin version reads old field names at runtime — no manual config edits needed.

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

After installation (if you skipped reconfigure above):

```bash
openclaw gateway restart
openclaw openviking status --json
```

### Old Plugin ID (memory-openviking, version < 0.3.x)

The old memory plugin used a different plugin ID and slot:

```bash
# Uninstall old plugin
openclaw plugins uninstall memory-openviking 2>/dev/null || true

# Clean up old slot and files
openclaw config set plugins.slots.memory none
rm -rf ~/.openclaw/extensions/memory-openviking/

# Install new plugin
openclaw plugins install @openclaw/openviking
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
openclaw gateway restart
openclaw openviking status --json
```

Or use the cleanup script:

```bash
bash examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh
```

See also: [INSTALL-ZH.md](./INSTALL-ZH.md) and [INSTALL-AGENT.md](./INSTALL-AGENT.md).
