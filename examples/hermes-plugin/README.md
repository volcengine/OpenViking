# OpenViking Memory Provider

Context database by Volcengine (ByteDance) with filesystem-style knowledge hierarchy, tiered retrieval, and automatic memory extraction.

This plugin connects Hermes to OpenViking for long-term memory and knowledge
retrieval. The installation steps below use a reviewed OpenViking commit.

For Hermes releases that still bundle OpenViking, follow the
[Hermes integration guide](../../docs/en/agent-integrations/05-hermes.md) and run
`hermes memory setup openviking`. No external plugin installation is needed.

For development and licensing details, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Install

Validated with Hermes v2026.9.24. CI also checks the reviewed Hermes main
commit pinned in the [test workflow](../../.github/workflows/hermes-plugin-tests.yml).

For a direct installation, replace the placeholder with the reviewed OpenViking
commit's full 40-character SHA:

```bash
hermes plugins install 'https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin' \
  --ref '<full-40-character-commit-SHA>' --no-enable
hermes plugins enable openviking
hermes memory setup openviking
hermes memory status
```

The equivalent shorthand is `volcengine/OpenViking/examples/hermes-plugin`.
Hermes installs this directory as `$HERMES_HOME/plugins/openviking/`.
In this two-step flow, Hermes resolves its `pyproject.toml` dependencies under
Hermes's dependency constraints when you enable the plugin.

If Hermes still includes the bundled OpenViking provider, that copy takes
precedence. The external copy becomes active after the bundled copy is removed.
Keep `memory.provider: openviking` and your existing configuration. No memory
data needs to move.

## Upgrade

For a direct subdirectory installation, use force-reinstallation instead of
`hermes plugins update openviking`. Hermes does not retain the repository's
`.git` directory when it installs a subdirectory.

Replace the placeholder with the reviewed OpenViking commit's full 40-character
SHA, and run this command in the same Hermes profile as the original installation:

```bash
hermes plugins install 'volcengine/OpenViking/examples/hermes-plugin' \
  --force --ref '<full-40-character-commit-SHA>' --enable
```

Existing connection settings and server data are retained. When upgrading from
the native `viking_*` tools, run `hermes memory setup openviking` once to configure
MCP. Restart Hermes or the gateway after the upgrade. For catalog installations, use
`hermes plugins update openviking`.

## Requirements

- Python 3.11 or newer in the Hermes environment
- A reachable OpenViking server, OpenViking Service credentials, or Quick Local setup
- For a self-hosted server, OpenViking installed in its own environment or container

The plugin connects over HTTP. Keep the server in a separate environment.
Quick Local creates that environment for you. For Custom setup to start a local
server, make `openviking-server` available on `PATH`.

OpenViking 0.4.1 or newer with its `/mcp` endpoint is required for MCP tools.
When the MCP entry is disabled, setup validates automatic memory access only.
Quick Local supplies OpenViking 0.4.22. Hermes can identify older servers that
expose the legacy status-only health response, but those releases do not provide
the authenticated-user identity contract required by this integration.
The `viking://~` home alias requires OpenViking 0.4.16 or newer for user and
admin credentials, and OpenViking 0.4.17 or newer for root or local development.

## Setup

For a Custom self-hosted deployment, prepare OpenViking in its server environment:

```bash
openviking-server init
openviking-server doctor
openviking-server
```

Then configure Hermes:

```bash
hermes memory setup openviking
```

The setup can link to an existing `~/.openviking/ovcli.conf`, copy its current
connection values into Hermes, or create a minimal `ovcli.conf` when one does
not exist.

For a new connection, choose **OpenViking Service**, **Custom**, or **Quick Local**.

Quick Local installs OpenViking 0.4.22 in a private runtime. It uses
`bge-small-zh-v1.5-f16` for local embeddings and copies the configured Hermes LLM
settings for memory extraction. The LLM must use a static API key or Hermes's
local llama.cpp server, with an OpenAI-compatible or Anthropic-compatible API.
OAuth and cloud-native
credentials cannot be copied; use Custom for those deployments.
Google AI Studio uses its OpenAI-compatible endpoint. Static OpenAI and xAI
API keys use Chat Completions; the selected model must support that API.
Kimi Coding retains Hermes's client attribution. Anthropic routes that need
Bearer authentication, including MiniMax and Azure Foundry, and proxy keys
that LiteLLM would treat as OAuth require a separately configured Custom server.
Setup checks the copied LLM with one short completion before activation.
It retries once for a timeout, HTTP 429, or HTTP 5xx. Other errors fail
immediately. The message and server log identify the error without recording
credentials or the provider's response body.

Quick Local stores its runtime, data, model cache, and private configuration in
`$HERMES_HOME/openviking/`. Setup validates it, then leaves its localhost server
running so the first Hermes turn can use memory. On later starts, Hermes starts
the server in the background if it has stopped. The embedding model downloads once, about
46 MiB. The LLM can still use a remote API. Server logs are in
`$HERMES_HOME/logs/openviking-server.log`.
The first installation needs network access. It downloads prebuilt packages
with pinned hashes on macOS 14+ (Apple Silicon), Linux with glibc 2.31+
(x86-64 or ARM64), and Windows x86-64. Setup asks before a source build on
other platforms. A source build needs native development tools and can take
several minutes. You can choose Custom instead and use a separate server.
Downloads require access to PyPI, GitHub and Hugging Face. The local BGE model
is trained for Chinese; use Custom for a server with a different embedding model.

Run setup again after changing the Hermes model or API key. Setup validates
the new settings and restarts this profile's server. Data and the model cache
are retained. Each profile has its own server and port; Quick Local moves to
a free port if another service takes its saved port.
New setups use ports 1934–1953, leaving OpenViking's default port 1933 free.
Existing Quick Local profiles keep their saved port when it is available.

The server stays running after Hermes exits. Use these commands in the same
Hermes profile to control it:

```bash
hermes openviking local status
hermes openviking local stop
hermes openviking local start
hermes openviking local restart
```

Stopping the server retains its data. Hermes starts it again when memory is
next used, including from a running gateway. Close those sessions or disable
OpenViking memory for it to stay stopped. While background startup or recovery
is in progress, new turns are not captured until the server is ready.
These commands and Quick Local setup require the external provider
to be active; a bundled copy still takes precedence where present.
Switching to Cloud or Custom stops this profile's managed server and retains
its data. `local start` and `local restart` require Quick Local to be selected;
`local status` and `local stop` remain available for the retained server.
The existing Cloud and Custom connection choices remain available.
Hermes backups include Quick Local files under the profile home; the plugin
adds a linked `ovcli.conf` only when it is outside that home.

Before removing the plugin, close Hermes sessions and stop the gateway for
this profile. Run `hermes openviking local stop`, then select a
different memory provider or disable OpenViking memory. Finally, run
`hermes plugins remove openviking` and remove `mcp_servers.openviking` from this
profile's `config.yaml`. The private runtime, model cache and data
remain in `$HERMES_HOME/openviking/`; they can use more than 1 GB per profile.
Plugin removal does not stop a running server.
Each running profile also uses several hundred MB of memory. Package upgrades
retain old runtime generations and wheel files, so disk use can increase.
After restoring a backup on another machine, run setup again to rebuild any
runtime excluded from the backup before using Quick Local.

Setup first asks how the Hermes instance is used:

| Preset | Hermes conversation history | OpenViking long-term recall |
|--------|-----------------------------|-----------------------------|
| **Personal Agent** | Keeps existing group/thread session settings | Common memory and the current sender's memory (`peer`) |
| **Shared Agent** | Shares each group or thread session between its participants | Common memory and all sender memories under the same OpenViking user (`shared`) |

Shared Agent requires confirmation before it sets `group_sessions_per_user` and
`thread_sessions_per_user` to `false`. Different groups still have separate
conversation histories. Restart the gateway to apply changed session settings.
Personal Agent does not make an already shared conversation private.

Both presets retain sender attribution during capture. After commit and
extraction, OpenViking can recall those memories across chats according to the
chosen scope. Upgrading the plugin alone does not apply a preset or change
session settings. Rerunning setup preselects the saved recall choice; a new
setup starts on Personal Agent.

Or manually:

```bash
hermes config set memory.provider openviking
```

Add the connection settings to the active profile's `.env` file. For the
default profile that is `~/.hermes/.env`; for a named profile use
`~/.hermes/profiles/<profile>/.env`.

```text
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
# OPENVIKING_API_KEY=...
# OPENVIKING_ACCOUNT=default
# OPENVIKING_USER=default
```

## Config

OpenViking's server config is separate from Hermes:

- `ov.conf` configures OpenViking storage, embedding/VLM models, auth, and
  server behavior. OpenViking reads it from `--config`,
  `OPENVIKING_CONFIG_FILE`, or `~/.openviking/ov.conf`.
- `ovcli.conf` stores client/CLI connection values such as `url`, `api_key`,
  `account`, and `user`. It is read from `OPENVIKING_CLI_CONFIG_FILE` or
  `~/.openviking/ovcli.conf`.

Hermes reads provider settings from the initialized profile's `config.yaml`,
profile secrets, and linked `ovcli.conf`. Connection values resolve in this order:
profile environment, linked OpenViking config, Hermes YAML, then defaults. API keys
come from profile secrets or the linked OpenViking config, not Hermes YAML.
Quick Local uses its own saved connection and ignores connection environment
overrides. Recall and commit settings still accept their documented overrides.
After initialization, the provider keeps that profile for connection, identity,
and recall settings, including when another profile is active in the same
process. For the launch profile, process-level `OPENVIKING_*` values fill missing
`.env` values. Under multi-profile hosting, Hermes uses the process values frozen
at activation; a messaging gateway without that snapshot does not read them.
Routed profiles never inherit the launch profile's process values.

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_ENDPOINT` | `http://127.0.0.1:1933` | Server URL |
| `OPENVIKING_API_KEY` | (none) | User/admin API key for authenticated servers |
| `OPENVIKING_ACCOUNT` | `default` | Tenant account for local/trusted mode |
| `OPENVIKING_USER` | `default` | Tenant user for local/trusted mode |
| `OPENVIKING_AGENT` | (none) | Optional peer ID for separate assistant context |

User and admin API keys let OpenViking derive account/user identity from the key.
In local or trusted deployments without an API key,
Hermes sends `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` as identity headers.
Hermes also sends `User-Agent: openviking-memory-hermes/<version>` on
OpenViking requests. This standard harness identifier contains the Hermes
version, but no per-user identifier, and does not add a separate request.

### Optional peer identity

New connections use the OpenViking user's memory directory by default. Setup
does not ask for a peer ID. Without a configured peer, Hermes sends neither
`X-OpenViking-Actor-Peer` nor assistant-message `peer_id`.

For separate assistant context, set the existing `agent` field in the active
profile's `config.yaml`:

```yaml
memory:
  openviking:
    agent: work-assistant
```

Existing non-empty `OPENVIKING_AGENT`, YAML `agent`, and linked OpenViking
`actor_peer_id` or legacy `agent_id` values retain their behavior. Resolution
order remains environment, linked OpenViking config, then Hermes YAML for Cloud
and Custom connections. Quick Local uses the `hermes` peer. To use
no peer on a Cloud or Custom connection, remove the peer value from each
configured source and start a new Hermes session.

Upgrades do not move or delete existing memories. Installations that relied
on the old implicit `hermes` peer now use user memory for new writes. Without
a peer ID, default OpenViking search covers user memory and existing peer
memories under the same OpenViking user. Old peer memories stay at their
existing paths and remain searchable. Ranking and result limits determine
which memories are returned. Keep a peer ID if you need the narrower view.

Set `agent: hermes` to restore peer-scoped writes. Memories written at user
scope before this change stay there and remain searchable. This setting
changes future writes, not the location of existing memories.

### Gateway senders and automatic recall

The external provider attaches the current gateway sender to captured user
messages as a peer, for example `telegram.123456`. The OpenViking account and
user stay unchanged. Assistant messages keep the configured `agent` peer.
CLI messages without a gateway sender keep their existing user-level attribution.
Existing memories are not moved.

The setup presets save the automatic recall scope. You can also set it in the
active profile's `config.yaml`:

```yaml
memory:
  openviking:
    recall_scope: peer
```

| Value | Automatic recall |
|-------|------------------|
| `shared` | Common memory and all peer memories under the same OpenViking user. |
| `peer` | Common memory and the current gateway sender's memory. With no sender, only common memory is recalled. |

With no scope set, the provider preserves the previous requests: normally
shared recall, but an explicitly configured assistant peer can narrow list
recall. Existing compression behavior is also retained. This is compatibility
handling for existing installations, not a third setup mode. Invalid values
warn and preserve that behavior.

`OPENVIKING_RECALL_SCOPE` overrides YAML. On successful setup, the wizard removes
this override from the profile's `.env` so the selected preset takes effect.
An override supplied again by a service or shell still takes precedence.
The configuration schema exposes only `shared` and `peer`. A stable
alternate sender ID is used when Hermes supplies one; unsafe IDs are encoded
to valid peer IDs. Queued captures retain their own sender when another
participant sends a turn.

The scope applies to automatic query recall, including compression and search
fallbacks. If an older server cannot confirm sender-scoped compression, the
provider uses scoped list recall. Enabled resource recall includes common
resources and, in `peer` mode, the sender's resources.

This is a retrieval setting, not an access-control boundary. It does not filter
shared conversation history or change explicit MCP tools, native memory
mirroring, or credentials. Setting `recall_scope` alone does not change gateway
sessions; the confirmed Shared Agent setup preset applies those settings.
Explicit tools retain
the configured assistant view. Use separate OpenViking users and credentials
when participants require separate access rights.

## Tools

Setup configures `mcp_servers.openviking` in the active Hermes profile. Restart
Hermes after setup. Hermes discovers the server's tools and exposes them as
`mcp__openviking__<tool>`, with the server's descriptions, arguments and results.
For example, `mcp__openviking__search` searches and `mcp__openviking__read` reads.
Available tools depend on the OpenViking server version.

The six `viking_*` tools are replaced by these MCP tools. After upgrading from
an earlier plugin, run `hermes memory setup openviking` once, then restart Hermes.
Existing memories, automatic recall, capture, commits and native memory
mirroring are retained.

The adapter reads the same connection settings as automatic memory, including
linked `ovcli.conf` changes and Quick Local port recovery. Credentials stay in
the existing secret files. The adapter does not start or stop the server.
Discovery waits up to `connect_timeout` (60 seconds by default) for an unreachable
server, including Custom and remote servers. Tools can be unavailable on the
first turn while discovery runs. After a longer outage, run `/reload-mcp` in the
Hermes CLI once OpenViking is ready, or restart the gateway.

Setup exposes all server tools and defaults to `trust: full`, matching Hermes.
OpenViking tools run without per-call approval. They can modify data, and
`forget` can permanently delete entire directories. OpenViking still enforces
the connected user's access permissions. Setup prints this policy before it exits.

To require Hermes approval for tools classified as writable, set `trust: untrusted`
under `mcp_servers.openviking` in the profile's `config.yaml`, then reload MCP or
restart Hermes. Hermes builds containing [#133532](https://github.com/NousResearch/hermes-agent/pull/133532)
recognize read-only tools such as `find`, `read` and `grep` correctly. `search`,
`remember`, `write` and `forget` still require approval; unattended runs (`-q`
and cron) cannot approve them. On the supported v2026.9.24 release, read-only
tools also require approval; its classic CLI cannot display these prompts, but the TUI can. See
[compatibility details](DEVELOPMENT.md#mcp-host-compatibility) before opting in.
Automatic recall, capture and commits do not use this MCP approval gate.

Existing trust settings, tool filters, timeouts, TLS
settings and `enabled: false` are preserved. CA and proxy environment settings
are passed to the adapter. Hermes can defer tool discovery until `tool_search`
and temporarily pause the toolset after repeated tool errors.
Use `hermes tools` to select the OpenViking MCP toolset. Disabling the `memory`
toolset alone does not disable MCP tools; disable the OpenViking MCP entry or
toolset as well if needed.

These are the standard OpenViking tool contracts. `remember` accepts messages
for extraction; `write` and `edit` change specific files. `forget` supports the
server's permitted URI scopes and its `recursive` argument. Confirm the exact
target before deletion. For local paths, follow the `add_resource` response:
current servers return a temporary upload URL and upload instructions. The
adapter does not automatically upload or zip files from the Hermes machine.
Use the OpenViking CLI if the agent cannot perform the requested upload.
Use the discovered schemas instead of arguments from the old `viking_*` tools.

## Memory Writes And Deletes

Successful Hermes built-in `memory` mutations are mirrored to OpenViking in
order. The active profile records each mirrored entry's exact URI in
`$HERMES_HOME/openviking/memory_mirror_registry.json`:

| Hermes action | OpenViking operation |
|---------------|----------------------|
| `add` | Create a file under user memory or the configured peer, then record its URI |
| `replace` | Match the committed event's full previous content and target, update the same URI, and wait for semantic/vector refresh |
| `remove` | Match the committed event's full previous content and target, delete that exact URI, and wait for semantic cleanup |

Replacing a mapped entry recreates its file if it was deleted directly in
OpenViking, for example with the MCP `forget` tool.

The registry stores the current entry text and a connection fingerprint, not
the raw API key. Endpoint, credentials, user, account, and peer changes isolate
the new connection from earlier mappings. New files require a server-confirmed
user identity. Missing or ambiguous mappings block replacement and deletion
with a warning; the plugin never selects a target by semantic similarity.

Replacement and deletion require Hermes to provide the full previous entry
content after its local memory write succeeds. If this data is unavailable,
the plugin skips those mirror operations with a warning; local memory still
changes. It does not guess which remote entry to change.

Only entries created by this mirror have mappings. Session-extracted memories,
explicit MCP `remember` results, and copies created before this registry are
outside its scope. Use the MCP `forget` tool with an exact URI to remove those copies.

The mirror is asynchronous. Hermes saves its local memory first. Rejected remote
writes leave the registry unchanged and produce a warning. Additions do not wait
for indexing, so the mirror does not report later indexing failures. For `replace`,
if the server reports that the file changed but indexing failed, the registry
retains the new content and exact URI; a warning reports the indexing failure
because search results may be stale. There is no durable replay. A remote mutation
followed by a failed registry save can also cause drift. Operations are ordered
per provider; registry updates are serialized across instances sharing a profile
in one process, not across processes.

Registry files use mode `0600` on POSIX. Protect the profile with normal account
and filesystem permissions on Windows. An unreadable, invalid, or unsupported
registry blocks all mirror writes. Stop Hermes before restoring a valid backup.
For an unsupported version, use a plugin version that supports it. Renaming a
damaged registry starts a new registry but leaves earlier remote copies without
mappings; those copies need manual cleanup by exact URI.

### Cloud recall compression

Set `OPENVIKING_RECALL_COMPRESS=server` to enable cloud recall compression, or
`auto` to let the server decide whether to rewrite. Both use search
`mode=context`; `server` sends `rewrite=true`, and `auto` sends `rewrite="auto"`.
The server digest takes precedence over raw rendered context, and `no_relevant`
suppresses injection. The default remains `off`; no local compressor is launched.

The Hermes config equivalent is `memory.openviking.recall_compress: server`.
When enabled, the default request and total recall deadlines become 55 seconds;
explicit recall timeout settings still take precedence. Older servers fall back
to the existing search path within that deadline.

### Active-session commits

The standalone provider checks OpenViking's `pending_tokens` after each successful
turn upload. At **20,000 tokens** by default, it requests a background commit
without ending the Hermes session. Memory extraction then runs on the server.
Session-end and session-switch commits still flush messages below this threshold.

Set a different threshold in the active Hermes profile's `config.yaml`:

```yaml
memory:
  openviking:
    commit_token_threshold: 8000
```

`OPENVIKING_COMMIT_TOKEN_THRESHOLD` overrides the YAML value. The setting accepts
integers from 1,000 to 1,000,000; values outside this range are clamped. Invalid
values use the 20,000-token default. The provider also exposes this setting through
its configuration schema.

This is a client-side commit trigger. It does not set or replace the server's
`auto_commit_policy`. If a server policy is enabled, both triggers operate
independently. Server locking serializes their archive operations, but explicit
client commits do not use the server scheduler's interval or retention settings.
The plugin retains the existing `keep_recent_count: 0` commit behavior.
The threshold is not a hard limit on extraction input: one turn can
exceed it, and the server may include other context during extraction.

### Non-primary contexts

When Hermes initializes the provider with `agent_context` set to `cron`, `subagent`,
or `flush`, recall and profile reads keep working. Automatic turn uploads,
session-end/switch commits, and native memory mirroring are skipped for that
context. Startup recovery can still commit pending messages from earlier sessions.
MCP tools remain subject to Hermes tool permissions, including writes and deletes.
Interactive sessions (and hosts that predate `agent_context`) keep the previous
automatic write behavior.
