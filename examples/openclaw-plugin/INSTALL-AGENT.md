# OpenViking Plugin Agent Install Guide

This guide is for AI agents such as Claude Code, Cursor, or similar operator flows. Prefer automation. Ask the user only when detection fails or a choice materially changes the outcome.

For user-facing installation details, see [INSTALL.md](./INSTALL.md) or [INSTALL-ZH.md](./INSTALL-ZH.md).

## Goal

Choose the smallest action that matches the user's intent:

| User intent | Command |
| --- | --- |
| Fresh install, latest | `npm install -g openclaw-openviking-setup-helper@latest && ov-install` |
| Upgrade plugin to latest | `npm install -g openclaw-openviking-setup-helper@latest && ov-install -y` |
| Install or upgrade a specific release | `npm install -g openclaw-openviking-setup-helper@latest && ov-install -y --version 0.2.9` |
| Upgrade only the plugin | `ov-install --update` |
| Show installed versions | `ov-install --current-version` |
| Operate on a specific OpenClaw instance | add `--workdir <path>` |
| Start missing OpenViking server | `openviking-server init && openviking-server doctor && openviking-server` |

Default rule: when upgrading, refresh the setup helper first unless the user explicitly asks to pin the helper itself.

## Detection Rules

### 1. Detect OpenClaw instance

If the user did not specify a workdir, check for multiple OpenClaw instances:

```bash
ls -d ~/.openclaw* 2>/dev/null
```

- If only one instance exists, use it.
- If multiple instances exist, ask which instance to operate on, or pass `--workdir`.

### 2. Detect environment

Verify:

```bash
node -v
openclaw --version
```

Requirements:

- Node.js >= 22
- OpenClaw >= 2026.3.7

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

If OpenClaw is missing, tell the user to run:

```bash
npm install -g openclaw && openclaw onboard
```

### 4. Detect existing install state

Use:

```bash
ov-install --current-version
```

This reports:

- installed plugin release
- requested plugin ref
- installation time

## Standard Workflows

### Latest Install

Use for fresh installs:

```bash
npm install -g openclaw-openviking-setup-helper@latest
ov-install
```

Notes:

- `ov-install` is interactive on first install.
- It stores remote connection settings in `plugins.entries.openviking.config`.

### Latest Upgrade

Use when the user wants the plugin upgraded:

```bash
npm install -g openclaw-openviking-setup-helper@latest
ov-install -y
```

Current behavior:

- plugin version defaults to the latest repo tag
- `-y` runs the non-interactive path; verify the resulting plugin config after upgrade if the target instance has custom settings

### Release-Pinned Install or Upgrade

Use when the user names a release such as `0.2.9`:

```bash
npm install -g openclaw-openviking-setup-helper@latest
ov-install -y --version 0.2.9
```

This sets the plugin version to `v0.2.9`.

### Plugin-Only Upgrade

Use only when the user explicitly wants to upgrade just the plugin:

```bash
ov-install --update
```

### Legacy Plugin Cleanup

If the machine previously used `memory-openviking`, run the bundled cleanup script from this repository:

```bash
bash examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh
```

Then continue with install or upgrade.

## Verification

### Check plugin slot

```bash
openclaw config get plugins.slots.contextEngine
```

Expected output:

```text
openviking
```

### Check plugin config

```bash
openclaw config get plugins.entries.openviking.config
```

### Check logs

OpenClaw log:

```bash
openclaw logs --follow
```

Look for:

```text
openviking: registered context-engine
```

OpenViking service log, default path:

```bash
cat ~/.openviking/data/log/openviking.log
```

### Pipeline Health Check (Optional)

If the checks above all pass and you want to further verify the full Gateway → OpenViking pipeline, run the health check script:

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

This injects a real conversation through Gateway and verifies from the OpenViking side that the session was captured, committed, archived, and had memories extracted. See [health_check_tools/HEALTHCHECK.md](./health_check_tools/HEALTHCHECK.md) for full details.

### Start command

```bash
openclaw gateway restart
```

## Plugin Config Reference

Check the whole config first:

```bash
openclaw config get plugins.entries.openviking.config
```

Core OpenClaw plugin fields:

- `baseUrl`
- `apiKey`
- `agent_prefix`: optional; interactive setup accepts only letters, digits, `_`, and `-`

## Uninstall

Plugin only:

```bash
bash examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh
```
