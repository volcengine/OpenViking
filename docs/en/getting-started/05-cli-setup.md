# Install and use the CLI

`ov` is the command-line client for OpenViking. It connects to an existing OpenViking server. It does not install a server. If you do not have a server yet, do step 1 of the [Quick Start](02-quickstart.md) first.

## Set up with an agent

Click **Copy** below and paste the prompt into your coding agent, for example Claude Code, Codex, or Cursor. The agent installs `ov`, asks you which server to use, and then configures and checks the connection.

<AgentPrompt>

````markdown
# openviking-cli

> `ov` is the command-line client for OpenViking, a context database for AI agents. It connects to an existing OpenViking server or to OpenViking Service on Volcengine.

I want you to install and configure the OpenViking CLI (`ov`) for me. Execute all the steps below autonomously. Stop and ask me only where a step says ASK.

OBJECTIVE: Install `ov`, save a named connection to my OpenViking server, and make it the active config.

DONE WHEN: `ov config validate` shows every check as passed (config file valid, server reachable, auth accepted, healthy), and `ov health -o json` returns `"healthy": true`.

## TODO

- [ ] Install `ov` and set its display language
- [ ] Find out which server to connect to
- [ ] Save and activate a named config
- [ ] Verify the connection

## Rules

- You must not guess the target. Existing configs, local files, open ports, and running services are not my consent.
- You must ASK before you switch, replace, or delete a config, probe or start a local server, or write data.
- You must keep API keys out of command text, shell history, logs, memory, and printed config files. Pass a key only through stdin or through an environment variable that already exists.
- If you cannot pass a key that way, ASK me to run `ov config` and type the key myself.
- You must pass `--name` to `ov config add`, so that a retry updates the same config.
- You must add `-o json` to `ov config add|edit|list|switch|delete`. Branch on the exit code and `error.code`, not on prose.
- If the installed `ov --help` differs from this file, follow the installed help and tell me the difference.

## Step 1: Install ov

You need Node.js and npm.

```bash
command -v ov || npm i -g @openviking/cli
ov language en
ov --version
```

Use `ov language zh-CN` instead if I write to you in Chinese. Most `ov` commands exit with code 2 in a non-interactive shell until a display language is saved.

If `ov` is not found after the install, add `$(npm prefix -g)/bin` to `PATH`. Do not use `sudo npm`. If npm is not available, ASK me before you build from source with `cargo install --git https://github.com/volcengine/OpenViking ov_cli`.

Read the help for the commands you will use:

```bash
ov config add ov-service --help
ov config add custom --help
```

## Step 2: Find out the target

Run `ov config list -o json`. If a saved config already matches the target, ASK me before you activate it with `ov config switch <NAME> -o json`.

Otherwise ASK me which target to use, unless I already told you:

| Target | URL | API key |
|---|---|---|
| OpenViking Service (Volcengine) | Fixed. Do not pass `--url`. | Required. I get it in the [console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing) under User Management → API Key. |
| Remote custom server | ASK me. | ASK me. |
| Local custom server | `http://127.0.0.1:1933` | Usually none. |

For a local custom server only, check that it runs: `curl -fsS http://127.0.0.1:1933/health`. If the check fails, ASK me to start the server. See https://docs.openviking.ai/en/guides/03-deployment.

Do not ask for `--account` or `--user` unless my administrator gave me these values.

## Step 3: Save and activate the config

Replace `<NAME>`, `<URL>`, and `<ENV_VAR>` with confirmed values. Remove the angle brackets. `$OV_API_KEY` stands for a trusted runtime source of the key, not the literal key.

OpenViking Service:

```bash
printf '%s' "$OV_API_KEY" | ov config add ov-service --name <NAME> --api-key-stdin --activate -o json
```

Remote custom server with an API key:

```bash
printf '%s' "$OV_API_KEY" | ov config add custom --name <NAME> --url <URL> --api-key-stdin --activate -o json
```

Local custom server without authentication:

```bash
ov config add custom --name <NAME> --url http://127.0.0.1:1933 --activate -o json
```

Special key cases:

- If the key is already in an environment variable, use `--api-key-env <ENV_VAR>` instead of `--api-key-stdin`.
- Root key only, on a server in `trusted` mode: use `--root-api-key-stdin --account <ACCOUNT> --user <USER>`. On a server in `api_key` mode, a root key cannot read data. ASK me for a user or admin key.
- User key and root key together: use `--api-key-stdin --root-api-key-env <ENV_VAR>`. A command has only one stdin, so the second key must come from an existing environment variable.

Exit codes of `ov config` subcommands:

| Code | Meaning | Your action |
|---|---|---|
| `0` | Success, or already in the target state | Continue. |
| `2` | Bad input, missing argument, or no display language | Fix the input, or run `ov language <code>`. |
| `3` | A config with this name exists with different content | ASK me before you add `--force`. |
| `4` | Server unreachable, or validation failed | ASK me to confirm the URL and that the server runs. |
| `5` | Authentication failed, or wrong key role | ASK me to confirm the key and key type. |
| `6` | Operation refused, for example deleting the active config | ASK me how to continue. |

Do not retry with guessed values.

## Step 4: Verify

```bash
ov config validate
ov health -o json
```

Read the output. Exit code 0 alone does not prove that the server is healthy. To inspect the config, use `ov config show`, which hides secrets. Do not print `~/.openviking/ovcli.conf`.

Do not import data as a demo unless I ask.

After setup, I may ask you to browse, search, add, or organize content with `ov`. Run `ov --help` to see the command groups. Run `ov <command> --help` before you use a command.

EXECUTE NOW: Complete the above TODO list to achieve: `ov config validate` shows every check as passed, and `ov health -o json` returns `"healthy": true`.

For more context, read https://docs.openviking.ai/llms.txt.
````

</AgentPrompt>

The rest of this page describes manual setup.

## Before you start

You need Node.js and npm.

You also need connection details. They depend on the server type:

| Server type | Server URL | API key |
|---|---|---|
| OpenViking Service (Volcengine) | Fixed. You do not enter it. | Required. Get it in the [OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing) under **User Management → API Key**. |
| Remote self-hosted server | Get it from your administrator. | Get it from your administrator. |
| Self-hosted server on this machine | `http://127.0.0.1:1933` | Not needed for the default setup. |

## 1. Install `ov`

```bash
npm i -g @openviking/cli
ov language en
ov --version
```

The second command sets the display language of the CLI. Use `zh-CN` for Chinese. You must set a language before you use most commands.

A machine that runs the OpenViking server already has `ov`. The server package (`uv tool install openviking`) installs it.

## 2. Add a connection

```bash
ov config
```

Follow the prompts:

1. Select **Add Config**.
2. Select the server type. For OpenViking Service, select **OpenViking Service (VolcEngine Cloud)**. For a self-hosted server, select **Custom**.
3. Enter a name for the config. If you leave it empty, `ov` generates a name.
4. Enter the server URL and the API key that the prompts ask for.
5. After validation passes, select **Save and activate**.

## 3. Check the connection

```bash
ov config validate
ov health
```

`ov config validate` checks the active config. The connection works when all checks pass: config file valid, server reachable, auth accepted, and healthy. `ov health` shows the server status as **Connected (Healthy)**.

Setup is complete. Next, you can [import and retrieve your first document](02-quickstart.md#_3-import-a-document). To learn more, read on.

## What you can do with `ov`

| Task | Commands |
|---|---|
| Browse | `ov ls`, `ov tree`, `ov stat` |
| Read content | `ov abstract`, `ov overview`, `ov read`, `ov get` |
| Search | `ov find`, `ov search`, `ov grep`, `ov glob` |
| Add content | `ov add-resource`, `ov add-skill`, `ov add-memory`, `ov write` |
| Organize content | `ov mkdir`, `ov mv`, `ov cp`, `ov rm`, `ov set-tags` |
| Track background tasks | `ov task list`, `ov task status`, `ov wait` |
| Manage sessions | `ov session new`, `ov session add-message`, `ov session commit` |
| Back up and move data | `ov export`, `ov import`, `ov backup`, `ov restore`, `ov snapshot` |
| Manage users and accounts (admin or root key) | `ov admin list-users`, `ov admin register-user`, `ov admin regenerate-key` |
| Check the connection and server | `ov config`, `ov health`, `ov status` |

Run `ov <command> --help` to see the options of a command. You can also ask your agent to do any of these tasks.

::: warning Caution
`ov rm -r` deletes a directory and all of its content. Before you delete, use `ov ls` to check the URI.
:::

## Manage several connections

```bash
ov config list     # list saved configs
ov config switch   # select the active config
ov config show     # show the active config, with secrets hidden
```

To edit or delete a config, run `ov config` and select the action. To configure `ov` from a script, use `ov config add`. Run `ov config add --help` for the options.

The active config is `~/.openviking/ovcli.conf`. Each saved config is `~/.openviking/ovcli.conf.<name>`. When you switch, `ov` copies the saved config to the active file.

If you set `OPENVIKING_CLI_CONFIG_FILE`, `ov` uses that file as the active config. Saved configs are then in the same directory as that file. For all fields, see [Client Configuration](../configuration/02-client.md).

## API key types

- **User key**: for data commands, such as `ov add-resource` and `ov find`. Most users need only this key.
- **Root key**: for administration commands and commands with `--sudo`.

One config can hold both keys. Normal commands use the user key. Commands with `--sudo` use the root key. For details, see [Authentication](../guides/04-authentication.md).

## Keep API keys safe

- Type the API key in the `ov config` prompt. Do not put a key in a command, because the shell history keeps it.
- Use `ov config show` to look at a config. It hides secrets.
- Do not share the content or screenshots of `~/.openviking/ovcli.conf`.
- Use a temporary key that you can revoke for demos and trials.
- If an agent sets up `ov` for you, give it the key only through a channel that you trust.

## Troubleshooting

### `ov` is not found

Open a new terminal. If `ov` is still not found, add the npm global binary directory to `PATH`. On macOS and Linux, this directory is usually `$(npm prefix -g)/bin`.

### npm reports a permission error

Fix the permissions in the way you usually manage Node.js, for example with nvm. Do not run `sudo npm i -g` unless you always manage global packages that way.

### A command asks for a display language

Run `ov language en` or `ov language zh-CN`. Then run the command again.

### The local server does not respond

Check the server:

```bash
curl http://127.0.0.1:1933/health
```

If this fails, start the server first. See [Deployment](../guides/03-deployment.md).

### API key validation fails

Run `ov config`, select **Edit Config**, and enter the key again. For OpenViking Service, copy the key from the console. For a self-hosted server, ask your administrator for the correct key and key type.

### The wrong config is active

Run `ov config list` to see which config is active. Run `ov config switch` to select another one.

### `ov config setup-cli` does not work

This command was removed. Use `ov config`.

## Rebuild indexes

`ov reindex <uri>` checks and repairs the indexes of imported content:

```bash
ov reindex viking://resources/my-project --mode vectors_only
ov reindex viking://resources/my-project --mode semantic_and_vectors
```

- `vectors_only` (default): rebuilds vectors only.
- `semantic_and_vectors`: regenerates the abstract and overview (`.abstract.md`, `.overview.md`), then rebuilds vectors.

By default, the command processes the full subtree and waits until it is complete. It skips resources and skills whose MD5 fingerprint did not change. Add `--force` to rebuild all data in scope. Add `--recursive false` to process only the target itself. Run `ov reindex --help` for all options.

## Next steps

- Import and retrieve your first document: [Quick Start](02-quickstart.md).
- Connect OpenViking to the agent you use every day: [Agent integrations](../agent-integrations/01-overview.md).
- See all commands with `ov --help`. See the options of one command with `ov <command> --help`.
