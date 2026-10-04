# Install and use the CLI

`ov` is the command-line client for OpenViking. OpenViking stores the context of your agents as a file system: resources, memories, and skills are directories and files under `viking://`. With `ov`, you can browse, read, search, and write this content. Agents use the same commands.

`ov` connects to an existing OpenViking server. If you do not have a server yet, do step 1 of the [Quick Start](02-quickstart.md) first.

## Set up with an agent

Click **Copy** below and paste the prompt into your coding agent, for example Claude Code, Codex, or Cursor. The agent installs `ov`, asks you which server to use, and then saves the config and checks the connection.

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

After setup, I may ask you to browse, search, add, or organize content with `ov`. OpenViking content is a directory tree under `viking://`. To read, first use `ov abstract` or `ov overview` to read the directory summary. Then use `ov read` to read only the files you need. Run `ov --help` to see the command groups. Run `ov <command> --help` before you use a command.

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

`ov language` sets the display language. Use `zh-CN` for Chinese. Most commands do not run until you set a language.

A machine that runs the OpenViking server already has `ov`. On that machine, skip `npm i` and only set the language.

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
```

The connection works when all items under **Checks** pass: Config file `valid`, Server `reachable`, Auth `accepted`, and Health `healthy`.

Setup is complete. Next, you can [import and retrieve your first document](02-quickstart.md#_3-import-a-document). To learn more, read on.

## What you can do with `ov`

OpenViking content is a directory tree. Run `ov ls` to list the root, `viking://`:

- `viking://resources/`: imported documents, code repositories, and web pages. Shared in the account.
- `viking://user/<user-id>/`: your memories, private resources, skills, and sessions. `viking://~/` points to this directory.
- `viking://agent/`: skills and agent configuration, shared in the account.

Each directory has an L0 abstract and an L1 overview. The full text of a file is L2. Read the L0 and L1 of a directory first to decide if it is relevant. Then read only the L2 files that you need. For details, see [Viking URI](../concepts/04-viking-uri.md) and [Context Layers](../concepts/03-context-layers.md).

| Task | Commands |
|---|---|
| Browse | `ov ls`, `ov tree`, `ov stat`. `ov tui` opens an interactive browser. |
| Read by layer | `ov abstract` (L0), `ov overview` (L1), `ov read` (L2). `ov get` downloads a file to your machine. |
| Search | `ov find` (semantic search), `ov grep` (match content), `ov glob` (match paths) |
| Import documents and skills | `ov add-resource`, `ov add-skill`, `ov skills` |
| Write and organize | `ov write`, `ov mkdir`, `ov mv`, `ov cp`, `ov rm` |
| Extract memories from a conversation | `ov session new`, `ov session add-message`, `ov session commit`. `ov add-memory` does these three steps in one command. |
| Wait for background processing | `ov task list`, `ov task status`, `ov wait` |
| Save and roll back versions | `ov snapshot` |
| Back up and move data | `ov export`, `ov import`, `ov backup`, `ov restore` |
| Rebuild indexes (`viking://resources` needs an admin key) | `ov reindex` |
| Manage users (admin or root key) | `ov admin list-users`, `ov admin register-user`, `ov admin regenerate-key` |
| Check the server | `ov health`, `ov status` |

After you import content or commit a session, the server processes it in the background: it parses content, extracts memories, generates L0 and L1, and builds indexes. Until processing is complete, `ov find` does not return the new content.

Run `ov <command> --help` to see the options of a command. You can also ask your agent to do any of these tasks.

::: warning Caution
`ov rm -r` deletes a directory and all of its content. The delete runs on the server, so other agents that use the same server also lose this content. Before you delete, use `ov ls` to check the URI.
:::

## Manage several connections

```bash
ov config list     # list saved configs
ov config switch   # select the active config
ov config show     # show the active config, with secrets hidden
```

To edit or delete a config, run `ov config` and select the action. To add a config from a script, use `ov config add`. Run `ov config add --help` for the options.

The active config is `~/.openviking/ovcli.conf`. Each saved config is `~/.openviking/ovcli.conf.<name>`. If you set `OPENVIKING_CLI_CONFIG_FILE`, `ov` uses that file as the active config, and saved configs are in the same directory as that file. For all fields, see [Client Configuration](../configuration/02-client.md).

## API keys

When the server uses API key authentication, a key has one of three roles:

- **User key**: for data commands, such as `ov add-resource` and `ov find`. Most users need only this key.
- **Admin key**: for data commands, and for managing the users of its account.
- **Root key**: for managing the whole server, for example to create accounts. In `api_key` mode, a root key cannot read or write account data.

One config can hold a user key and a root key. Normal commands use the user key. `ov admin`, `ov system`, `ov reindex`, `ov task status`, and `ov task list` use the root key when you add `--sudo`. For details, see [Authentication](../guides/04-authentication.md).

To keep API keys safe:

- Type the key in the `ov config` prompt. Do not put a key in a command, because the shell history keeps it.
- Use `ov config show` to look at a config. It hides secrets. Do not share the content or screenshots of `~/.openviking/ovcli.conf`.
- For demos and trials, use a temporary key that you can revoke.
- If an agent sets up `ov` for you, do not paste the key into the chat. Put the key in an environment variable, or run `ov config` yourself and type the key when the agent asks.

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

Run `ov config`, select **Edit Config**, and enter the key again. For OpenViking Service, copy the key from the console. For a self-hosted server, ask your administrator for the correct key and key type. In `api_key` mode, data commands need a user key or an admin key.

### The wrong config is active

Run `ov config list` to see which config is active. Run `ov config switch` to select another one.

## Next steps

- Import and retrieve your first document: [Quick Start](02-quickstart.md).
- Connect OpenViking to the agent you use every day: [Agent integrations](../agent-integrations/01-overview.md).
