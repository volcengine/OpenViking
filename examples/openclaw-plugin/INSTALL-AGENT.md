# OpenViking Plugin Agent Install Guide

This guide is for AI agents such as Claude Code, Cursor, or similar operator flows. Prefer automation. Ask the user only when detection fails or a choice materially changes the outcome.

For user-facing installation details, see [INSTALL.md](./INSTALL.md) or [INSTALL-ZH.md](./INSTALL-ZH.md).

## Goal

Choose the smallest action that matches the user's intent:

| User intent | Command |
| --- | --- |
| Fresh install, latest | `npm install -g openclaw-openviking-setup-helper@latest && ov-install` |
| Upgrade plugin + OpenViking to latest | `npm install -g openclaw-openviking-setup-helper@latest && ov-install -y` |
| Install or upgrade a specific release | `npm install -g openclaw-openviking-setup-helper@latest && ov-install -y --version 0.2.9` |
| Upgrade only the plugin | `ov-install --update` |
| Show installed versions | `ov-install --current-version` |
| Operate on a specific OpenClaw instance | add `--workdir <path>` |

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
python3 --version
node -v
openclaw --version
```

Requirements:

- Python >= 3.10
- Node.js >= 22
- OpenClaw >= 2026.3.7

If OpenClaw is missing, tell the user to run:

```bash
npm install -g openclaw && openclaw onboard
```

### 3. Detect existing install state

Use:

```bash
ov-install --current-version
```

This reports:

- installed plugin release
- requested plugin ref
- installed OpenViking version
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
- In local mode, it generates `~/.openviking/ov.conf` and `~/.openclaw/openviking.env`.
- In remote mode, it stores remote connection settings in `plugins.entries.openviking.config`.

### Latest Upgrade

Use when the user wants both the plugin and OpenViking runtime upgraded:

```bash
npm install -g openclaw-openviking-setup-helper@latest
ov-install -y
```

Current behavior:

- plugin version defaults to the latest repo tag
- OpenViking runtime is upgraded through pip during install
- `-y` runs the non-interactive path; verify the resulting plugin config after upgrade if the target instance has custom settings

### Release-Pinned Install or Upgrade

Use when the user names a release such as `0.2.9`:

```bash
npm install -g openclaw-openviking-setup-helper@latest
ov-install -y --version 0.2.9
```

This is shorthand for:

- plugin version `v0.2.9`
- OpenViking version `0.2.9`

### Plugin-Only Upgrade

Use only when the user explicitly wants to keep the current OpenViking runtime version unchanged:

```bash
ov-install --update
```

Do not combine `--update` with `--version` or `--openviking-version`.

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

OpenViking service log, default local path:

```bash
cat ~/.openviking/data/log/openviking.log
```

### Start commands

Local mode:

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

Remote mode:

```bash
openclaw gateway restart
```

## Plugin Config Reference

### Local Mode

Check the whole config first:

```bash
openclaw config get plugins.entries.openviking.config
```

Core OpenClaw plugin fields:

- `mode=local`
- `configPath`
- `port`
- `agentId`

Service-side model configuration is not stored in the OpenClaw plugin config. It lives in `~/.openviking/ov.conf`, especially:

- `vlm.api_key`
- `vlm.model`
- `embedding.dense.api_key`
- `embedding.dense.model`
- `server.port`

### Remote Mode

Check the whole config first:

```bash
openclaw config get plugins.entries.openviking.config
```

Core OpenClaw plugin fields:

- `mode=remote`
- `baseUrl`
- `apiKey`
- `agentId`

## Uninstall

Plugin only:

```bash
bash examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh
```

Plugin + local OpenViking runtime:

```bash
python3 -m pip uninstall openviking -y && rm -rf ~/.openviking
```
