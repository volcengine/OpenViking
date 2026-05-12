# OpenViking Plugin Agent Install Guide

This guide is for AI agents and operator automation. Prefer deterministic commands, parse JSON output, and ask the user only when a choice changes the outcome.

User-facing docs:

- [INSTALL.md](./INSTALL.md)
- [INSTALL-ZH.md](./INSTALL-ZH.md)

## Identity

This package is the OpenClaw plugin `@openclaw/openviking`.

| User intent | Command |
| --- | --- |
| Fresh install, latest | `openclaw plugins install @openclaw/openviking` |
| Upgrade plugin to latest | `openclaw plugins install @openclaw/openviking` |
| Install or upgrade a specific release | Use the published ClawHub package selector if available; otherwise ask before using backup `ov-install --plugin-version=<REF>` |
| Upgrade only the plugin | `openclaw plugins install @openclaw/openviking` |
| Show installed plugins | `openclaw plugins list` |
| Backup install when ClawHub is unavailable | `npx -y openclaw-openviking-setup-helper@latest --base-url <URL> [--api-key <KEY>]` |
| Operate on a specific OpenClaw instance | add `--workdir <path>` |
| Start missing OpenViking server | `openviking-server init && openviking-server doctor && openviking-server` |

Do not install it with:

```bash
clawhub install openviking
```

That installs an AgentSkill, not the plugin. The plugin install command is:

```bash
openclaw plugins install @openclaw/openviking
```

## Required Inputs

Before setup, determine:

| Input | Required | How to get it |
| --- | --- | --- |
| OpenViking base URL | Yes | Ask user or read `OPENVIKING_BASE_URL` |
| API key | Usually | Ask user or read `OPENVIKING_API_KEY` |
| Account ID | Only for root API keys | Ask user if setup reports root-key tenant context is needed |
| User ID | Only for root API keys | Ask user if setup reports root-key tenant context is needed |
| Slot replacement approval | Only if another context engine owns the slot | Ask user before using `--force-slot` |

Never silently replace another context engine.

## Primary Workflow

Use this workflow for normal installs and upgrades from published packages.

```bash
openclaw plugins install @openclaw/openviking
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
openclaw gateway restart
openclaw openviking status --json
```

If the registry prefix is required:

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

## Setup JSON Contract

Run setup with `--json` whenever possible:

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
```

Branch on the result:

| JSON result | Meaning | Agent action |
| --- | --- | --- |
| `success: true` | Setup completed | Restart gateway, then run status |
| `success: false`, `action: "slot_blocked"` | Another plugin owns `plugins.slots.contextEngine` | Ask user before rerunning with `--force-slot` |
| `success: false`, `action: "error"` | Validation failed | Report `error`; do not continue as success |
| `health.ok: false` | Server unreachable | Check URL/service; use `--allow-offline` only with user approval |
| `keyProbe.keyType: "root_key"` | Root key requires tenant fields | Rerun with `--account-id <ACCOUNT_ID> --user-id <USER_ID>` |
| `health.compatibility: "server_too_old"` | Server may not support plugin features | Warn user and recommend server upgrade |
| `health.compatibility: "server_too_new"` | Plugin may be too old | Warn user and recommend plugin upgrade |

Root-key retry:

```bash
openclaw openviking setup \
  --base-url <OPENVIKING_URL> \
  --api-key <ROOT_API_KEY> \
  --account-id <ACCOUNT_ID> \
  --user-id <USER_ID> \
  --json
```

Custom agent routing prefix (optional; only when the user explicitly requests a prefix):

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --agent-prefix <PREFIX> --json
```

Slot replacement retry, only after user approval:

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --force-slot --json
```

Offline config save, only after user approval:

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --allow-offline --json
```

## Status JSON Contract

After restart, run:

```bash
openclaw openviking status --json
```

Ready state:

```json
{
  "configured": true,
  "slotActive": true
}
```

Also inspect:

| Field | Use |
| --- | --- |
| `health.ok` | Confirms server reachability |
| `health.version` | Records server version |
| `health.compatibility` | Determines whether to warn |
| `config.hasApiKey` | Confirms whether an API key was saved |
| `config.agent_prefix` | Confirms configured agent prefix when present |

## Environment Detection

Check tools:

```bash
node -v
openclaw --version
```

Requirements:

- Node.js >= 22
- OpenClaw >= 2026.4.8

Version boundaries:

- `2026.4.8` is the minimum supported OpenClaw version for the current `@openclaw/openviking` plugin.
- `2026.5.3` starts requiring compiled JavaScript runtime output during package install when a plugin package declares TypeScript entries.
- `2026.5.4` and later no longer fall back to `.ts` source for installed/global plugin runtime loading when the compiled JavaScript output is missing; the plugin may be skipped.
- Published ClawHub packages are built before release and include `dist/*.js`, so normal users do not need to build locally.
- `ov-install` is the backup/source install path. Use it only after the OpenClaw plugin manager or ClawHub path is unavailable/rate-limited, or when the user explicitly asks to install a source ref. For OpenClaw `>= 2026.5.3`, it builds the plugin during installation.

### 3. Detect or start OpenViking server

The OpenClaw plugin only connects to an OpenViking HTTP server. It does not start the server.

Check the default local server first:

```bash
curl -fsS http://127.0.0.1:1933/health
```

If no OpenViking server is running and the user wants a local server:

```bash
pip install openviking --upgrade --force-reinstall
openviking-server init
openviking-server doctor
openviking-server
```

Keep `openviking-server` running while OpenClaw uses the plugin. Use `http://127.0.0.1:1933` as the plugin `baseUrl` for the default local setup.

For a remote server, confirm the reachable URL with the user and use that URL as `baseUrl`.

If OpenClaw is missing, tell the user to install and initialize OpenClaw:

```bash
npm install -g openclaw
openclaw onboard
```

### 4. Detect existing install state

If the user has multiple OpenClaw state directories, ask which one to operate on before changing config.

## Existing State Detection

Try status first:

```bash
openclaw openviking status --json
```

If the command is unavailable, install the plugin first. If it returns `configured: true` and `slotActive: true`, do not reinstall unless the user requested upgrade or reconfigure.

Manual inspection:

```bash
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
openclaw plugins list
```

## Standard Operations

Fresh install:

```bash
openclaw plugins install @openclaw/openviking
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
openclaw gateway restart
openclaw openviking status --json
```

Reconfigure:

```bash
openclaw openviking setup --reconfigure
openclaw gateway restart
openclaw openviking status --json
```

Upgrade:

```bash
openclaw plugins update openviking
openclaw gateway restart
openclaw openviking status --json
```

Uninstall:

```bash
openclaw plugins uninstall openviking
openclaw config set plugins.slots.contextEngine legacy
openclaw gateway restart
```

Native uninstall may not reset `plugins.slots.contextEngine`. Always run the explicit `config set` step after uninstall.

## Config Fields

Config path:

```text
plugins.entries.openviking.config
```

Core fields:

| Field | Meaning |
| --- | --- |
| `mode` | Legacy compatibility field. Expected value: `remote`. |
| `baseUrl` | OpenViking HTTP endpoint |
| `apiKey` | OpenViking API key |
| `agent_prefix` | Optional; prefix for OpenClaw agent IDs when set. Interactive setup accepts only letters, digits, `_`, and `-`. If unset, the plugin follows session agent IDs. |
| `accountId` | Required for root API keys |
| `userId` | Required for root API keys |

## Verification

Quick verification:

```bash
openclaw openviking status --json
```

Manual verification:

```bash
openclaw config get plugins.slots.contextEngine
openclaw config get plugins.entries.openviking.config
openclaw logs --follow
```

Expected log signal:

```text
openviking: registered context-engine
```

Optional end-to-end health check from a repository checkout:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

## Migrate From ov-install

If ov-install was previously used, clean up before switching to `openclaw plugins install`:

Same plugin ID (openviking, >= 0.3.x):

```bash
rm -rf ~/.openclaw/extensions/openviking/
openclaw plugins install @openclaw/openviking
openclaw openviking setup --reconfigure
openclaw gateway restart
openclaw openviking status --json
```

Old plugin ID (memory-openviking, < 0.3.x):

```bash
openclaw plugins uninstall memory-openviking 2>/dev/null || true
openclaw config set plugins.slots.memory none
rm -rf ~/.openclaw/extensions/memory-openviking/
openclaw plugins install @openclaw/openviking
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
openclaw gateway restart
openclaw openviking status --json
```

Existing config fields are preserved during migration. The new plugin reads old field names at runtime.

- `baseUrl`
- `apiKey`
- `agent_prefix`: optional; interactive setup accepts only letters, digits, `_`, and `-`

## Backup Path: ov-install

`ov-install` is the backup path, not the primary user install path. Use it when `openclaw plugins install @openclaw/openviking` cannot reach ClawHub, is rate-limited, or when the user explicitly wants a source ref / Git branch install.

Backup install:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

Backup/source commands:

| Intent | Command |
| --- | --- |
| Install from a source ref | `ov-install --plugin-version=<REF>` |
| Non-interactive backup install | `ov-install --base-url <URL> --api-key <KEY>` |
| Target a non-default OpenClaw state directory | `ov-install --workdir <PATH>` |
| Show helper-tracked version | `ov-install --current-version` |
| Update helper-managed install | `ov-install --update` |

For user installs, always try `openclaw plugins install @openclaw/openviking` first. Choose `ov-install` only as the backup path.
