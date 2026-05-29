# OpenViking CLI Setup

This guide explains how to install the OpenViking CLI, configure it, and verify that it can connect to OpenViking.

`ov` connects to an existing OpenViking server. It does not replace server setup. If you still need to install or start the server, follow the [Quick Start](02-quickstart.md) or the [Server Mode guide](03-quickstart-server.md) first.

## What This Configures

The CLI uses `~/.openviking/ovcli.conf` for client connection settings.

Use `ov config` to create or manage this file interactively. The command can add, edit, or delete saved configs, and can make a config active after validation.

## Quick Path

```bash
npm i -g @openviking/cli
ov --help
ov config
ov config validate
ov health
ov status
```

Expected result:

- `ov --help` prints the OpenViking command list.
- `ov config validate` confirms the active config can reach and authenticate with the server.
- `ov health` confirms basic server reachability.
- `ov status` shows the active config and server diagnostics.

## Before You Start

You need:

- Node.js and npm.
- A reachable OpenViking target:
  - Volcengine Cloud, or
  - a self-managed OpenViking server.
- An API key if your target requires authentication.

API keys are sensitive. Prefer entering them through the interactive `ov config` prompt. For demos or trials, use a temporary key that can be revoked later.

## Install `ov`

Check whether `ov` is already installed:

```bash
command -v ov
ov --version
```

Install or upgrade the npm package:

```bash
npm i -g @openviking/cli
```

Verify:

```bash
ov --help
```

If `ov` is still not found, close and reopen the shell, or check the npm global bin directory:

```bash
npm bin -g
```

## Choose A Target

### Volcengine Cloud

Choose this when you want OpenViking hosted by Volcengine.

- Server URL is fixed: `https://api.vikingdb.cn-beijing.volces.com/openviking`
- API key is required.
- Get the API key from: https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing

### Self-Managed

Choose this when you run or operate your own OpenViking server.

- Default local URL: `http://127.0.0.1:1933`
- API key is usually not needed for a local unauthenticated server.
- API key may be required for a remote or authenticated self-hosted server.

## Configure The CLI

Run:

```bash
ov config
```

Then choose:

1. `Add config`
2. `Volcengine Cloud` or `Self-Managed`
3. A config name, or leave it empty to generate one
4. The required URL/API key values
5. Save the config after validation

If you manage more than one OpenViking target, use:

```bash
ov config switch
```

to change the active config later.

## Validate

After setup, run:

```bash
ov config show
ov config validate
ov health
ov status
```

Use `ov config show` for inspection because it redacts secrets.

Do not print the raw config file unless you understand that it may contain secrets.

## Credential Safety

- API keys may grant access to your OpenViking data.
- Prefer entering API keys through the interactive `ov config` prompt.
- Do not include API keys directly in shell commands.
- Do not store API keys in shell history.
- Do not print raw `~/.openviking/ovcli.conf`.
- Do not share screenshots that reveal API keys.
- Use temporary or revocable keys for demos and trials.

## Troubleshooting

### `ov` Is Not Found

Run:

```bash
npm i -g @openviking/cli
npm bin -g
```

Then reopen the shell or add the npm global bin directory to `PATH`.

### npm Global Install Fails

If npm reports a permission error, use your normal Node.js setup policy. Avoid `sudo npm i -g` unless you intentionally manage global npm packages with sudo.

### Local Server Is Not Running

For self-managed local setup, verify the server first:

```bash
curl http://127.0.0.1:1933/health
```

If it fails, start the server before configuring `ov`. See the [Server Mode guide](03-quickstart-server.md).

### API Key Validation Fails

Run `ov config` again and edit the config. For Volcengine Cloud, confirm the key came from the OpenViking console URL above. For self-managed servers, confirm whether the server requires authentication.

### The Wrong Config Is Active

Inspect and switch:

```bash
ov config show
ov config switch
ov config validate
```

### Old Setup Commands

Use `ov config`. Do not use old or removed setup commands such as `ov config setup-cli`.

## Next Steps

Once the CLI is configured:

```bash
ov add-resource https://github.com/volcengine/OpenViking --wait
ov find "what is OpenViking"
ov tree viking://resources/ -L 2
```

For all commands:

```bash
ov --help
ov <command> --help
```
