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

## Workspace Configuration

A repository can carry its own plugin settings, so the memory behavior of a project travels with the checkout instead of living in each contributor's home directory. Two files sit under the workspace root, and a third layer is kept per machine:

```text
<repo-root>/.openviking/config.json         # committed, shared by the team
<repo-root>/.openviking/config.local.json   # private, not committed
~/.openviking/workspaces/<slot>.json        # per-machine registry, one file per workspace
```

The workspace root is the nearest ancestor directory holding a `.git`, or one holding `.openviking/config.json` (or `config.local.json`) — whichever the walk upward reaches first; `$HOME` and the filesystem root are never workspace roots. A directory that is neither is not a workspace at all: no configuration layer, no registry entry, and no peer of its own. The registry slot name combines the root's directory name with a hash of its full path, so two clones of one repository on one machine never share an entry. These layers are read by the Claude Code and Codex plugins, not by `ov` commands.

### Precedence

Highest first:

| Layer | Scope |
|---|---|
| `OPENVIKING_*` environment variables | Current process |
| `~/.openviking/workspaces/<slot>.json` | This machine, this workspace |
| `<repo-root>/.openviking/config.local.json` | This checkout, private |
| `<repo-root>/.openviking/config.json` | This repository, committed |
| `ovcli.conf` `plugin.<harness>` | This machine, one harness |
| `ovcli.conf` `plugin` | This machine, every harness |
| `ov.conf` harness section | Compatibility layer for older deployments |
| Built-in defaults | |

A scalar from a higher layer replaces the lower one. Lists are unioned across layers; a leading `"!reset"` element drops everything the lower layers contributed, so `["!reset", "*/scratch/*"]` is the whole list.

No command writes the registry file. Create it by hand at the path `ov-memory-doctor` prints, with `version: 1` and the same schema as the workspace files.

### Schema

`version: 1` is required. A file declaring another version is skipped with a warning rather than guessed at.

```json
{
  "version": 1,
  "peer": { "source": "git" },
  "recall": { "peer_scope": "actor", "max_items": 20 },
  "capture": { "commit_token_threshold": 20000 },
  "labels": { "team": "search" }
}
```

| Key | Type / Values | Purpose |
|---|---|---|
| `peer.source` | `"git"` / `"cwd"` / `"none"` / template / list of templates | How the workspace peer is derived; only a git repository gets one by default |
| `peer.id` | string | Pin the peer explicitly; takes precedence over `peer.source` |
| `recall.enabled` | boolean | Whether the plugin recalls at all |
| `recall.peer_scope` | `"all"` / `"actor"` | `all` also sweeps this user's other peers, demoted in ranking; `actor` reads only user-level memories and this workspace's peer |
| `recall.dedup_turns` | integer, `0`–`20` | Recent turns a recalled item is deduplicated against |
| `recall.max_items` | integer, `1`–`100` | Maximum recalled items |
| `recall.score_threshold` | number, `0`–`1` | Minimum score for a recalled item |
| `capture.enabled` | boolean | Whether the session is captured |
| `capture.commit_token_threshold` | integer, `1000`–`1000000` | Tokens accumulated before a capture commits |
| `bypass.session_patterns` | list of globs | A session whose id or working directory matches skips recall and capture |
| `labels` | object | Free-form metadata for humans; not read by the plugins |

An out-of-range number is clamped to the nearest bound and reported; an unrecognized enum value is ignored. Keys outside this list are kept in the file and ignored.

### Workspace Peer

A peer is a path prefix under your own user space — `viking://user/<you>/peers/<peer>/memories` — that keeps one project's memories together. By default only a git repository gets one: the normalized `origin` URL, else the repository root path. A directory that is not a repository sends no peer at all, and what is remembered there goes to your user-level space at `viking://user/<you>/memories` instead. That is deliberate: an application that opens a fresh directory for every task would otherwise mint a fresh, empty peer for every task.

`peer.source` decides the rule. The same setting is spelled `OPENVIKING_PEER_SOURCE` in the environment and `plugin.peerSource` or `plugin.<harness>.peerSource` in `ovcli.conf`.

#### Give a Directory Its Own Peer

Create `.openviking/config.json` in the directory:

```json
{"version": 1, "peer": {"id": "my-project"}}
```

That directory and everything below it now writes to the peer `my-project`, repository or not. The id names no path, so it survives a move, a rename and a second machine — and two directories carrying the same id share one memory, which is how you merge them on purpose.

The other ways to set it, highest precedence first:

| Where | What it does |
|---|---|
| `OPENVIKING_PEER_ID=my-project` | Pins the peer for one process, whatever the files say |
| `peer.id` in `.openviking/config.json` | Names this workspace's peer. The recommended way; `config.local.json` is the same key kept out of the commit |
| `peer.source` in the same file | Derives the peer instead of naming it — `"cwd"` for the directory path, `"team-{dir}"` for a template |
| `plugin.peerSource` in `ovcli.conf`, or `OPENVIKING_PEER_SOURCE` | The same choice for every directory on this machine; `"cwd"` restores the pre-`git` behavior everywhere |

| `peer.source` | Meaning |
|---|---|
| `"git"` | Default. The normalized `origin` URL, else the repository root path — equivalent to `["{git_remote}", "{git_root}"]`. Outside a repository nothing is sent. No prefix is added. |
| `"cwd"` | The working directory with every non-alphanumeric character replaced by `-`, byte for byte what earlier releases sent |
| `"none"` | Send no peer at all; `OPENVIKING_WORKSPACE_PEER=0` means the same |
| template / list of templates | For example `"git-{git_remote}"` or `["{git_remote}", "team-{dir}"]`; templates are tried in order, and one whose variables are empty falls through to the next |

| Variable | Value | Empty when |
|---|---|---|
| `{git_remote}` | Normalized `origin`, as `github.com-org-repo` | Outside a git repository, or the repository has no `origin` |
| `{git_root}` | Repository root path, with every non-alphanumeric character replaced by `-` | Outside a git repository. A `.openviking/config.json` inside a repository still leaves this the repository's own root, so marking a subdirectory does not split the default peer |
| `{cwd}` | Working directory, with every non-alphanumeric character replaced by `-` | Never — and it is in no default chain, so a bare path becomes a peer only when you ask for one |
| `{dir}` | The workspace root's directory name: the repository root, or the directory holding `.openviking/config.json` | The directory is not a workspace |
| `{harness}` | The name of the agent running (`claude-code`, `codex`, `dsh`, `opencode`, `pi`, `cursor`, `trae`, `trae-cn`, `zcode`) | Never — but the MCP proxy takes no part in derivation, so a read path that goes only through it cannot resolve it |

In `/Users/x/Dev/OpenViking/examples/codex-memory-plugin` with `origin` `git@github.com:volcengine/OpenViking.git`, the peer is `github.com-volcengine-openviking` — the same value from any subdirectory, worktree, machine, or clone. Every clone of one repository therefore shares one peer, while a fork has a different `origin` and stays separate. The derivation reads the repository's files directly instead of running `git`, so it also works where `git` is missing from `PATH`, and the URL is normalized so that the ssh and https spellings of one repository agree and a token embedded in the URL never reaches the peer id.

#### By Situation

| Situation | What to do |
|---|---|
| A repository with an `origin` | Nothing. Every clone, worktree and subdirectory shares one peer |
| A fork | Separate from upstream by default, because `origin` differs. To merge the two, write the same `peer.id` in both |
| A local repository with no remote | The repository root path by default, which changes on another machine. For anything long-lived, write a `peer.id` |
| A long-lived directory that is not a repository | Create `.openviking/config.json` with a `peer.id` |
| One subproject of a monorepo needing its own memory | Put a `config.json` in the subdirectory with `peer.source: "{git_remote}-{dir}"`. A marker file alone keeps the repository's peer, because `{git_remote}` resolves first |
| A throwaway task directory (a dated folder an app creates, an unpacked archive) | Nothing. Its memories go to your user-level space |
| Each agent keeping its own memory of one repository | `peer.source: "{git_remote}-{harness}"`. Not the default — one shared project memory across agents is usually what you want, so this one is opt-in |
| Several directories sharing one memory | Write the same `peer.id` in each |
| Not wanting per-project separation at all | `peer.source: "none"` (the same as `OPENVIKING_WORKSPACE_PEER=0`) |

### Recall Isolation

`peer.source` decides where memories are written; `recall.peer_scope` decides what is read back. A peer is a path prefix, not a tenant boundary. The same setting is spelled `plugin.recallPeerScope` in `ovcli.conf` and `OPENVIKING_RECALL_PEER_SCOPE` in the environment.

| `recall.peer_scope` | What recall reads |
|---|---|
| `"all"` (default) | User-level memories and this workspace's peer at full weight, plus a sweep across the user's other peers whose hits are demoted by category — the server's `other_peer_penalty` defaults to 0.1 for events and entities, 0.02 for preferences, experiences, resources and skills. Another project can therefore only ever come last |
| `"actor"` | User-level memories and this workspace's peer only. The plugin additionally asks once for the peer the pre-`git` rule would have derived here, so nothing written by an earlier release is lost |

User-level memories are read at full weight under both, and that is the cost of sending no peer outside a repository: what a throwaway task teaches is recalled in every project afterwards. For stronger separation, give such a directory a `peer.id` of its own, or switch to `"actor"`.

Switching to the `git` default needs no migration and moves nothing: memories written under the earlier cwd-derived peer stay where they are, and recall keeps reaching them — through the cross-peer sweep under `"all"`, and through the extra query under `"actor"`. `peer_scope` is a per-request parameter; against a server too old to know it, the plugin records the downgrade once and warns rather than silently reading everything.

### What a Workspace File May Not Set

A hook runs without a prompt, so these files are trusted; what is refused is structural instead:

- Connection and credential keys — `url`, `api_key`, `root_api_key`, `account`, `user`, `extra_headers`, and the rest — are stripped with a warning wherever they appear. Which server the data goes to stays answerable from `ovcli.conf` and the environment alone.
- `${VAR}` is never expanded in these files.

What a committed file switches off is announced rather than blocked: the plugin's `ov-memory-doctor` reports every workspace-scoped value, the layer it came from, and what it shadowed.

`.gitignore` must not ignore all of `.openviking/`, or `config.json` can never be committed. Narrow the rule to the parser's scratch directories and the private file:

```text
.openviking/media/
.openviking/downloads/
.openviking/config.local.json
```

`ov-memory-doctor` warns when a blanket rule is in effect.

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
