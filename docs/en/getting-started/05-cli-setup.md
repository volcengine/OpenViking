# OpenViking CLI Setup

This guide helps you install the OpenViking CLI, configure it, and verify that it can connect to OpenViking.

`ov` is the client CLI. It connects to an existing OpenViking server or to VolcEngine Cloud. It does not replace server setup. If you still need to install or start a self-managed server, follow the [Quick Start](02-quickstart.md) or the [Server Mode guide](03-quickstart-server.md) first.

Use this page in either of two ways:

- If you are setting up `ov` yourself, follow [Manual Setup](#manual-setup).
- If you are asking an agent to set it up for you, give the agent this page and ask it to follow [Agent-Assisted Setup](#agent-assisted-setup).

Both paths should start by checking the installed CLI help:

```bash
ov --help
ov config --help
```

The CLI evolves quickly. Use `ov --help` and `ov <command> --help` as the source of truth for the commands available in your installed version.

## What This Configures

The CLI uses `~/.openviking/ovcli.conf` as the active client connection config.

When you create named configs, `ov` stores them next to the active file as `~/.openviking/ovcli.conf.<name>`. Switching configs copies the chosen saved config into `~/.openviking/ovcli.conf`.

`ov config` is the human-friendly interactive manager. It can add, edit, delete, validate, and switch configs.

`ov config add`, `ov config edit`, `ov config list`, `ov config switch <name>`, and `ov config delete` are deterministic commands for scripts and agents.

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
- `ov config` opens the interactive config manager.
- `ov config validate` confirms the active config can reach and authenticate with the server.
- `ov health` confirms basic server reachability.
- `ov status` shows the active config and server diagnostics.

## Before You Start

You need:

- A way to install the CLI:
  - Node.js and npm for the standalone `@openviking/cli` package, or
  - Python tooling if you install the full `openviking` package.
- A reachable OpenViking target:
  - VolcEngine Cloud, or
  - a self-managed OpenViking server.
- An API key if your target requires authentication.

API keys are sensitive. Prefer entering them through the interactive `ov config` prompt. For agent-assisted setup, prefer stdin when the agent already holds the key through a secure channel. Use an environment variable only when the key is already present in the shell environment. Avoid putting API keys directly in chat messages or shell history.

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

The npm package is the simplest standalone CLI install. If you also want the Python SDK or server package, the Python package exposes `ov` too:

```bash
uv tool install openviking --upgrade
# or
pip install openviking --upgrade --force-reinstall
```

Verify:

```bash
ov --help
```

If `ov` is still not found, close and reopen the shell, or check the npm global prefix:

```bash
npm prefix -g
```

On macOS and Linux, the global npm binary directory is usually `$(npm prefix -g)/bin`. Make sure that directory is on `PATH`.

## Key Types

OpenViking CLI configs can hold a user key, a root key, or both.

- User key: use this for normal data commands such as `ov add-resource`, `ov find`, and `ov tree`. The server derives the identity from the key, so you usually do not pass `--account` or `--user`. This is what most users want.
- Root key: use this for admin work and commands that require `--sudo`. A root key has no built-in tenant identity. If a config only has a root key, it must also include `--account` and `--user`; that root key then serves normal commands for that identity and `--sudo` commands.
- User key plus root key: use this when the same config should support daily data work and occasional admin work. Normal commands use the user key. `--sudo` commands use the root key with the configured account and user.

## Choose a Target

### VolcEngine Cloud

Choose this when you want OpenViking hosted on VolcEngine Cloud.

- Server endpoint used by `ov`: `https://api.vikingdb.cn-beijing.volces.com/openviking`
- Console page for API keys: https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing
- In the console, go to User Management → API Key to view and copy your key.
- API key is required.
- Standard setup only needs the API key. Do not ask for `--account` or `--user` unless the user's administrator specifically provides identity override values.

### Self-Managed

Choose this when you run or operate your own OpenViking server.

- Default local URL: `http://127.0.0.1:1933`
- API key is usually not needed for a local unauthenticated server.
- API key may be required for a remote or authenticated self-managed server.

## Manual Setup

Use this path when you are reading the guide and configuring `ov` yourself.

Run:

```bash
ov config
```

Then choose:

1. `Add config`
2. `VolcEngine Cloud` or `Self-Managed`
3. A config name, or leave it empty to generate one
4. The required URL and API key values
5. Save the config after validation

If you manage more than one OpenViking target, use:

```bash
ov config switch
```

to choose the active config later.

After setup, continue to [Verify The Setup](#verify-the-setup).

## Agent-Assisted Setup

Use this path when an agent is setting up `ov` for a user. The agent should read this whole page. The manual setup path above is the fallback when deterministic commands do not fit the user's environment.

### Agent Checklist

1. Confirm whether the user wants VolcEngine Cloud or a self-managed server.
2. Run `ov --help`, `ov config --help`, and the relevant config subcommand help before choosing commands.
3. If you have long-term memory and the user permits it, store a short summary of the current `ov --help` command surface. Do not store API keys or other secrets.
4. Use non-interactive `ov config` commands when the required values are known.
5. Always pass `--name` for agent setup so retries target the same saved config.
6. If the agent already holds the API key through a secure channel, pass it with `--api-key-stdin` or `--root-api-key-stdin` and write only the key bytes to stdin. Use `--api-key-env` or `--root-api-key-env` only when that environment variable already exists. Do not ask the user to open a separate shell just to export a key for the agent.
7. Use `-o json` and branch on the JSON result plus the process exit code.
8. Validate the active config with `ov config validate`, then check `ov health` and `ov status`.
9. If non-interactive setup fails because values are missing, auth is unclear, or terminal input is safer, guide the user through `ov config` instead.
10. Do not run commands that add resources or otherwise store data in OpenViking unless the user explicitly permits that action.

### Inspect the Installed CLI

Run:

```bash
ov --help
ov config --help
ov config add --help
ov config add cloud --help
ov config add self-managed --help
ov config edit --help
```

Use the installed CLI help as the source of truth. If this page and the installed help disagree, follow the installed help and tell the user what changed.

### Use Stable Names for Retries

Always pass `--name` when an agent creates a config. If you omit it, `ov` generates a random name; a retry can create a second saved config instead of updating the intended one.

`ov config add` is safe to run again with the same `--name` when the values are identical. It exits `0`, and `--activate` will make that saved config active again. If the same name already exists with different values, the command exits `3` and asks for `--force`.

### Reading Results

When you use `-o json` with the non-interactive config commands, successful results are printed to stdout:

```json
{"status":"ok","result":{"action":"add","name":"prod"}}
```

The result object depends on the subcommand. `add` and `edit` also include fields such as `kind`, `url`, `saved_path`, `active_path`, `activated`, and `validation`, so agents should not assume that only `action` and `name` are present.

Errors are printed to stderr:

```json
{"status":"error","error":{"code":"bad_input","message":"..."}}
```

Agents should branch on the process exit code and the JSON `error.code`, not on human-readable prose.

| Exit code | Meaning |
|-----------|---------|
| `0` | Success, or already in the desired state |
| `2` | Bad input, missing value, invalid name, or unreadable secret source |
| `3` | A config with that name already exists with different values; pass `--force` only if replacement is intended |
| `4` | Server unreachable or config validation failed |
| `5` | Authentication or key-role mismatch, such as passing a root key where a user key is expected |
| `6` | Refused operation, such as deleting the active config |

### List Existing Configs

```bash
ov config list -o json
```

The list output shape is:

```json
{"status":"ok","result":[{"name":"prod","kind":"VolcEngine Cloud","url":"https://api.vikingdb.cn-beijing.volces.com/openviking","active":true}]}
```

For an existence check, inspect `result[].name`. To decide whether a config already needs switching, inspect the matching entry's `active` flag.

If a suitable saved config already exists, activate it by name:

```bash
ov config switch prod -o json
```

Then run the verification commands.

### Add VolcEngine Cloud

If the agent already holds the API key through a secure channel, run:

```bash
ov config add cloud --name prod --api-key-stdin --activate -o json
```

Write only the API key bytes to stdin. Do not place the key in the shell command. This writes a VolcEngine Cloud config using the fixed endpoint `https://api.vikingdb.cn-beijing.volces.com/openviking`. The `cloud` target does not take a custom server URL.

Use an environment variable only if it already exists in the shell:

```bash
ov config add cloud --name prod --api-key-env OV_API_KEY --activate -o json
```

Do not pass `--account` or `--user` for standard VolcEngine Cloud setup. Use them only when the user or their OpenViking administrator provides identity override values.

### Add a Local Self-Managed Server

For a local unauthenticated server:

```bash
ov config add self-managed --name local --url http://127.0.0.1:1933 --activate -o json
```

If the local server is not running, guide the user to start it first. See the [Server Mode guide](03-quickstart-server.md).

### Add a Remote Self-Managed Server

For a hosted self-managed server with a normal API key:

```bash
ov config add self-managed --name hosted --url https://ov.example.com --api-key-stdin --activate -o json
```

Write the API key to stdin. If the key is already in the shell environment, use `--api-key-env OV_API_KEY` instead.

For a self-managed server where the user gives you only a root API key, include the target account and user:

```bash
ov config add self-managed --name hosted --url https://ov.example.com --root-api-key-stdin --account "$OV_ACCOUNT" --user "$OV_USER" --activate -o json
```

Write the root API key to stdin. Root keys require explicit `--account` and `--user` so normal CLI commands know which identity to use.

For a self-managed server where the user has both a user key and a root key, store both in one config:

```bash
ov config add self-managed --name hosted-admin --url https://ov.example.com --api-key-stdin --root-api-key-env OV_ROOT_API_KEY --account "$OV_ACCOUNT" --user "$OV_USER" --activate -o json
```

This keeps normal commands on the user key and lets `--sudo` commands use the root key. Because one command has only one stdin stream, the second key must come from an existing environment variable. If neither key is already available in the environment, use `ov config` and guide the user through the interactive flow.

### Edit or Replace a Config

List configs first:

```bash
ov config list -o json
```

Rename and activate a saved config:

```bash
ov config edit prod --new-name production --activate -o json
```

Replace an API key:

```bash
ov config edit production --api-key-stdin --activate -o json
```

Write the replacement API key to stdin.

Replace a self-managed URL:

```bash
ov config edit local --url http://127.0.0.1:1933 --activate -o json
```

Use `--force` only when you intentionally want to replace an existing saved config name.

### Delete a Saved Config

Delete only non-active saved configs:

```bash
ov config delete old-local -o json
```

If the config is active, switch to another config first:

```bash
ov config switch prod -o json
ov config delete old-local -o json
```

## Verify the Setup

Run:

```bash
ov config show
ov config validate
ov health
ov status
```

Use `ov config show` for inspection because it redacts secrets.

Do not print the raw config file unless you understand that it may contain secrets.

## Learn the Rest of the CLI

After the config is working, use the built-in help to explore the rest of `ov`:

```bash
ov --help
ov config --help
ov add-resource --help
```

Agents should refresh this help before running unfamiliar commands. If an agent keeps long-term memory for the user and the user allows it, the agent may store a concise summary of the command surface for future sessions. It should not store secrets, raw config files, or private server details unless the user explicitly asks.

## Credential Safety

- API keys may grant access to your OpenViking data.
- Prefer the interactive `ov config` prompt for manual setup.
- For agent-assisted setup, prefer stdin when the agent already holds the key. Use environment variables only when they already exist in the shell.
- Do not include API keys directly in shell commands that may be saved in shell history.
- Do not paste API keys into chat unless you intentionally trust that channel.
- Do not print raw `~/.openviking/ovcli.conf`.
- Do not share screenshots that reveal API keys.
- Use temporary or revocable keys for demos and trials.

## Troubleshooting

### `ov` Is Not Found

Run:

```bash
npm i -g @openviking/cli
npm prefix -g
```

Then reopen the shell or add the global npm binary directory to `PATH`. On macOS and Linux, that directory is usually `$(npm prefix -g)/bin`.

### npm Global Install Fails

If npm reports a permission error, use your normal Node.js setup policy. Avoid `sudo npm i -g` unless you intentionally manage global npm packages with sudo.

### Local Server Is Not Running

For self-managed local setup, verify the server first:

```bash
curl http://127.0.0.1:1933/health
```

If it fails, start the server before configuring `ov`. See the [Server Mode guide](03-quickstart-server.md).

### API Key Validation Fails

Run `ov config` again and edit the config. For VolcEngine Cloud, confirm the key came from the OpenViking console URL above. For self-managed servers, confirm whether the server requires authentication.

Agents should not keep retrying unknown keys. Ask the user to confirm the target type, server URL, key type, account, and user.

### The Wrong Config Is Active

Inspect and switch:

```bash
ov config show
ov config list
ov config switch
ov config validate
```

Agents can switch by name:

```bash
ov config list -o json
ov config switch prod -o json
```

### Non-Interactive Setup Does Not Fit

Use the interactive wizard:

```bash
ov config
```

This is the right fallback when a secret should be typed directly by the user, when the target is unclear, or when validation needs human judgment.

### Old Setup Commands

Use `ov config`. Do not use old or removed setup commands such as `ov config setup-cli`.

## Next Steps

Once the CLI is configured, use `ov --help` and `ov <command> --help` to learn the rest of the CLI.

Adding a resource writes data into the active OpenViking server. If you want a small demo, use a resource you are comfortable storing. Agents must ask the user for permission before running this kind of demo command.

```bash
ov add-resource https://github.com/volcengine/OpenViking --wait
ov find "what is OpenViking"
ov tree viking://resources/ -L 2
```

For all commands:

```bash
ov --help
ov config --help
ov add-resource --help
```
