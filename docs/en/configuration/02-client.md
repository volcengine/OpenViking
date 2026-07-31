# ovcli Configuration

`ovcli.conf` is the client configuration file for the `ov` CLI. It stores the server connection, authentication identity, and command defaults.

Agent plugins for Codex, Claude Code, OpenCode, and other clients also read their own `OPENVIKING_*` environment variables for Recall, Capture, diagnostics, and other behavior. Those variables are not part of `ovcli.conf`; configure them in the corresponding [Agent Integration](../agent-integrations/01-overview.md) documentation.

Use `ov config` to create and maintain configurations. Use `ov config show` to inspect the active configuration with secrets redacted.

Default path:

```text
~/.openviking/ovcli.conf
```

To select another file:

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## Complete Example

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<user-or-admin-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "actor_peer_id": "agent:research-assistant",
  "timeout": 60,
  "output": "table",
  "echo_command": true,
  "show_progress": false,
  "verbose": false,
  "profile": false,
  "upload": {
    "ignore_dirs": "node_modules,.cache,dist",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  },
  "extra_headers": {
    "X-Tenant": "acme"
  },
  "gateway_token": "<gateway-token>"
}
```

Omit fields you do not need. A local server in `dev` mode usually needs only `url`.

## Connection and Authentication

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<user-or-admin-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "actor_peer_id": "agent:research-assistant",
  "extra_headers": {
    "X-Tenant": "acme"
  },
  "gateway_token": "<gateway-token>"
}
```

| Field | Type / Values | Default | Purpose |
|---|---|---|---|
| `url` | HTTP(S) URL | `http://127.0.0.1:1933` | OpenViking server endpoint |
| `api_key` | string / `null` | `null` | User/admin key for normal data operations |
| `root_api_key` | string / `null` | `null` | Root key for `ov --sudo` administrative operations |
| `account` | string / `null` | `null` | Account identity for trusted or root-key-only configurations |
| `user` | string / `null` | `null` | User identity for trusted or root-key-only configurations |
| `actor_peer_id` | string / `null` | `null` | Default Actor Peer identifier |
| `agent_id` | string / `null` | `null` | Compatibility field; use `actor_peer_id` for new configs and do not set both |
| `extra_headers` | object / `null` | `null` | Additional headers sent with every request; `extra_header` is a compatibility alias |
| `gateway_token` | string / `null` | `null` | `X-Gateway-Token` used when retrying a gateway challenge |

### Choosing API Keys

| Configuration | Normal Commands | `ov --sudo` |
|---|---|---|
| `api_key` only | User/admin key | unavailable |
| `root_api_key` plus `account` and `user` | Root key with explicit identity | Root key |
| Both keys | `api_key` | `root_api_key` |
| No keys | Local server with authentication disabled only | unavailable |

`server.root_api_key` in `ov.conf` is accepted by the server. When the CLI manages that server, `root_api_key` in `ovcli.conf` must match it.

## Command Behavior

```json
{
  "timeout": 120,
  "echo_command": true,
  "show_progress": true,
  "verbose": false,
  "profile": false
}
```

| Field | Type / Values | Default | Purpose |
|---|---|---|---|
| `timeout` | number, seconds, `> 0` | `60` | HTTP request timeout |
| `echo_command` | boolean | `true` | Show effective request parameters for commands such as `find`, `search`, and `ls` |
| `show_progress` | boolean | `false` | Show upload progress by default |
| `verbose` | boolean | `false` | Show upload diagnostics by default |
| `profile` | boolean | `false` | Request performance profiles; also requires `server.profile_enabled` |
| `output` | `"table"` / `"json"` | `"table"` | Compatibility field; use `-o table` or `-o json` to select current command output |

Command-line options such as `--profile`, `--progress`, `--no-progress`, and `--verbose` override the configuration for the current command.

## Upload Filters

```json
{
  "upload": {
    "ignore_dirs": "node_modules,.cache,dist",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  }
}
```

| Field | Type / Format | Default | Purpose |
|---|---|---|---|
| `upload.ignore_dirs` | comma-separated string / `null` | `null` | Directory names to ignore |
| `upload.include` | comma-separated globs / `null` | `null` | Upload only matching files |
| `upload.exclude` | comma-separated globs / `null` | `null` | Exclude matching files |

Local directory uploads also honor `.gitignore`. Command-line `--include` and `--exclude` rules are merged with the configuration.

## Related Environment Variables

The `ov` CLI directly uses only a small set of environment variables:

| Environment Variable | Purpose |
|---|---|
| `OPENVIKING_CLI_CONFIG_FILE` | Select the `ovcli.conf` path |
| `OPENVIKING_UPLOAD_MODE` | Select temporary upload mode: `local` or `shared` |

The `--api-key-env <name>` and `--root-api-key-env <name>` options for `ov config add` and `ov config edit` read keys from a named environment variable and write them to the configuration.

Variables such as `OPENVIKING_AUTO_RECALL`, `OPENVIKING_RECALL_LIMIT`, `OPENVIKING_AUTO_CAPTURE`, and `OPENVIKING_DEBUG` are read by Agent plugin processes and are not `ovcli.conf` fields.

## Multiple Servers

Normal `ov` commands, plus `ov config show` and `ov config validate`, resolve the effective configuration in this order:

1. When `OPENVIKING_CLI_CONFIG_FILE` is set, that path is authoritative; a missing file is an error.
2. When the variable is unset, the default active file:

```text
~/.openviking/ovcli.conf
```

The interactive manager and `ov config list`, `switch`, `add`, `edit`, and `delete` always manage the default store. Named configurations in that store live next to the default active file:

```text
~/.openviking/ovcli.conf.<name>
```

For example, a production configuration can contain:

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<production-api-key>",
  "timeout": 120
}
```

Common commands:

```bash
ov config
ov config list
ov config switch <name>
ov config validate
ov config show
```

`ov config switch <name>` copies the named configuration to the default active file. If `OPENVIKING_CLI_CONFIG_FILE` remains set, normal `ov` commands continue to use the environment-selected file; unset it to use the switched default. New `ov` commands reread the effective file, while already-running Agent clients must restart before reading changes.

See [OpenViking CLI Setup](../getting-started/05-cli-setup.md) for interactive and agent-assisted configuration workflows.
