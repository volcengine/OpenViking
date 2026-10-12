# Advanced configuration for Hermes

The setup wizard is enough for normal use. Start with the [quick setup](README.md#get-started).
Use this page to change settings or recover a failed memory operation.

## Manual connection setup

Select OpenViking in the active Hermes profile:

```bash
hermes config set memory.provider openviking
```

Add the connection values to that profile's `.env` file:

```text
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
# OPENVIKING_API_KEY=...
# OPENVIKING_ACCOUNT=default
# OPENVIKING_USER=default
```

The default profile uses `~/.hermes/.env`. A named profile uses
`~/.hermes/profiles/<profile>/.env`. Start a new Hermes session after changing it.

## Quick Local model support

Quick Local copies Hermes's language model settings into its own server config.
It supports a static API key or Hermes's local llama.cpp server through an
OpenAI-compatible or Anthropic-compatible API. OAuth logins and credentials
that need a cloud provider's own authentication are not supported. For those
models, use a Custom server with its own model configuration.

Google AI Studio uses its OpenAI-compatible endpoint. Static OpenAI and xAI
keys use Chat Completions, so the model must support that API. Kimi Coding keeps
Hermes's client headers. Anthropic connections that need Bearer authentication,
including MiniMax and Azure Foundry, require a Custom server. The same applies
to proxy keys that OpenViking's LiteLLM backend identifies as OAuth tokens.

Setup sends one short model request before it activates Quick Local. It retries
once for a timeout, HTTP 429 or HTTP 5xx. Other errors stop setup immediately.
Errors and logs do not include the key or the provider's response body.

The local embedding model is `bge-small-zh-v1.5-f16`, with 512 dimensions.
It is trained for Chinese. Use Custom for a server with another embedding model.

## Quick Local files and backups

Files are stored under `$HERMES_HOME/openviking/`. Logs are stored at
`$HERMES_HOME/logs/openviking-server.log`. Each profile has its own server and
port. New setups select a free port from 1934 to 1953. An existing setup keeps
its saved port if it is free, or selects another one if it is occupied.

The server package comes from PyPI. Its download is checked against PyPI's
SHA-256 hash. The native embedding packages use fixed versions and hashes.
Setup asks before building from source on other platforms. Building requires
native development tools and can take several minutes.

An installed server can use more than 1 GB of disk and several hundred MB of
memory per profile. Upgrades keep old runtime directories and downloaded
packages, so disk use can grow.

Hermes backups include files under the profile home. The plugin also adds a
linked `ovcli.conf` outside the profile if it is within your user home directory.
Back up files outside your user home separately. After restoring on another
machine, run setup again to rebuild any runtime missing from the backup.

## Connection settings

OpenViking's server config is separate from Hermes:

- `ov.conf` configures OpenViking storage, embedding/VLM models, authentication and
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
at activation. A messaging gateway without that snapshot does not read them.
Routed profiles never inherit the launch profile's process values.

OpenViking 0.2.14 or newer is required. Older servers with a status-only health
response do not provide the user identity needed by this plugin. The `viking://~`
home address requires OpenViking 0.4.16 or newer for user and admin keys, or
0.4.17 or newer for root keys and local development.

| Environment variable | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_ENDPOINT` | `http://127.0.0.1:1933` | Server URL |
| `OPENVIKING_API_KEY` | (none) | API key for authenticated servers |
| `OPENVIKING_ACCOUNT` | `default` | Tenant account for local/trusted mode |
| `OPENVIKING_USER` | `default` | Tenant user for local/trusted mode |
| `OPENVIKING_AGENT` | (none) | Optional peer ID for separate assistant context |

User and admin API keys let OpenViking derive account/user identity from the key.
In local or trusted deployments without an API key,
Hermes sends `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` as identity headers.
Hermes also sends `User-Agent: openviking-memory-hermes/<version>` on
OpenViking requests. This header contains the Hermes version. It does not
contain a user identifier or require an extra request.

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
order remains environment, linked OpenViking config, then Hermes YAML for Service
and Custom connections. Quick Local uses the `hermes` peer. To use
no peer on a Service or Custom connection, remove the peer value from each
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
CLI messages without a gateway sender remain linked to the OpenViking user.
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
alternate sender ID is used when Hermes supplies one. Unsafe IDs are encoded
to valid peer IDs. Queued captures retain their own sender when another
participant sends a turn.

The scope applies to automatic query recall, including compression and search
fallbacks. If an older server cannot confirm sender-scoped compression, the
provider uses scoped list recall. Enabled resource recall includes common
resources and, in `peer` mode, the sender's resources.

These recall settings do not change data access rights. They do not hide
shared conversation history or change explicit `viking_*` tools, native memory
mirroring or credentials. Setting `recall_scope` alone does not change gateway
sessions. Shared Agent changes those settings only after confirmation.
Explicit tools keep the configured assistant view. Use separate OpenViking
users and keys when participants need separate access.

## Memory writes and deletes

`viking_remember` creates a separate `hermes-remember-<random>` OpenViking
session, adds the fact as one message and commits all its messages. The session
remains available in OpenViking for audit. OpenViking then
classifies the source and can add, merge, or skip a memory through its normal
extraction pipeline. The tool returns the separate session ID and the
extraction task ID when the server provides one. Extraction continues
asynchronously after the tool returns.

The tool returns `status: submitted` because extraction can add a memory, merge
the fact into an existing memory, or produce no memory operation. It does not
promise that OpenViking created a distinct memory file. The fact is submitted
as an unchanged `user` message so OpenViking owns the final classification.
The legacy `category` argument is still accepted from existing callers but is
not advertised or used. This session is separate from the live Hermes
conversation, so an explicit remember does not commit or rotate the active
conversation session.

If the message request or commit fails, the error includes the exact
session URI, the failed stage, the observed message status, and an `ov session
commit <session-id>` recovery command. Inspect the session first. An archive
means the commit completed. A non-empty live `messages.jsonl` with no archive
means the message was accepted but still needs a commit. An empty live file
without an archive is ambiguous and must not trigger an automatic resubmission.
Use the same OpenViking profile and credentials as Hermes for manual recovery.
OpenViking server auto-commit is disabled by default, so an accepted message
whose explicit commit fails normally remains live without extracted memories
until it is manually committed.

Successful changes through Hermes's built-in `memory` tool are copied to
OpenViking in order. The active profile records each copied entry's exact URI in
`$HERMES_HOME/openviking/memory_mirror_registry.json`:

| Hermes action | OpenViking operation |
|---------------|----------------------|
| `add` | Create a file under user memory or the configured peer, then record its URI |
| `replace` | Match the committed event's full previous content and target, update the same URI, and wait for semantic/vector refresh |
| `remove` | Match the committed event's full previous content and target, delete that exact URI, and wait for semantic cleanup |

Replacing a mapped entry recreates its file if it was deleted directly in
OpenViking, for example with `viking_forget`.

The registry stores the current entry text and a hash that identifies the
connection. It does not store the raw API key. Endpoint, credentials, user,
account and peer changes isolate the new connection from earlier mappings.
New files require a server-confirmed
user identity. Missing or ambiguous mappings block replacement and deletion
with a warning. The plugin never selects a target by semantic similarity.

Replacement and deletion require Hermes to provide the full previous entry
content after its local memory write succeeds. If this data is unavailable,
the plugin skips those mirror operations with a warning. Local memory still
changes. It does not guess which remote entry to change.

Only entries created by this mirror have mappings. Session-extracted memories,
explicit `viking_remember` results, and copies created before this registry are
outside its scope. Use `viking_forget` with an exact URI to remove those copies.

The mirror is asynchronous. Hermes saves its local memory first. Rejected remote
writes leave the registry unchanged and produce a warning. Additions do not wait
for indexing, so the mirror does not report later indexing failures. For `replace`,
if the server reports that the file changed but indexing failed, the registry
retains the new content and exact URI. A warning reports the indexing failure
because search results may be stale. Failed operations are not saved for retry
after a restart. If a remote write succeeds but saving the registry fails, the
registry can differ from the server.
Operations run in order per provider. Instances in the same process share a
registry lock. Separate processes do not share that lock.

Registry files use mode `0600` on POSIX. Protect the profile with normal account
and filesystem permissions on Windows. An unreadable, invalid, or unsupported
registry blocks all mirror writes. Stop Hermes before restoring a valid backup.
For an unsupported version, use a plugin version that supports it. Renaming a
damaged registry starts a new registry but leaves earlier remote copies without
mappings. Those copies need manual cleanup by exact URI.

`viking_forget` is intentionally narrow. It only accepts concrete user memory
file URIs, such as
`viking://user/default/peers/hermes/memories/preferences/mem_abc123.md`, or the
`viking://~/...` self alias. Under `viking://user/...` the user id is required
and must match the calling identity. The `viking://user/memories/...`
and `viking://user/peers/...` paths without a user ID are rejected. Files
directly under `memories/`, such as `viking://user/default/memories/profile.md`,
are also allowed because OpenViking supports them. The tool rejects directories,
resources, skills, sessions, generated summary files, and URIs with query
strings or fragments. Use OpenViking's MCP, CLI, or admin APIs for broader
resource and directory cleanup.


### Cloud recall compression

Set `OPENVIKING_RECALL_COMPRESS=server` to enable cloud recall compression, or
`auto` to let the server decide whether to rewrite. Both use search
`mode=context`. `server` sends `rewrite=true`, and `auto` sends `rewrite="auto"`.
The server digest takes precedence over the original search results, and `no_relevant`
adds no memory to the prompt. The default is `off`. No local compressor is started.

The Hermes config equivalent is `memory.openviking.recall_compress: server`.
When enabled, the default request timeout and total recall budget become 55 seconds.
Explicit recall timeout settings still take precedence. Older servers fall back
to the existing search path within that deadline.

### Active-session commits

The plugin checks OpenViking's `pending_tokens` after each successful
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
integers from 1,000 to 1,000,000. Values outside this range use the nearest limit.
Invalid values use the default of 20,000 tokens. The provider also exposes this
setting through its configuration schema.

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
Explicit `viking_*` tools keep their normal behavior, including writes and deletes.
Interactive sessions (and hosts that predate `agent_context`) keep the previous
automatic write behavior.

## Pinned source installation

Use this flow only when you need a specific reviewed commit instead of the
catalog version. Replace the placeholder with its full 40-character SHA:

```bash
hermes plugins install 'volcengine/OpenViking/examples/hermes-plugin' \
  --ref '<full-40-character-commit-SHA>' --no-enable
hermes plugins enable openviking
hermes memory setup openviking
```

Hermes installs the plugin in `$HERMES_HOME/plugins/openviking/` and resolves
its dependencies under Hermes's constraints when you enable it.

Direct subdirectory installs do not retain a `.git` directory. To update one,
force-reinstall the reviewed commit in the same profile:

```bash
hermes plugins install 'volcengine/OpenViking/examples/hermes-plugin' \
  --force --ref '<full-40-character-commit-SHA>' --enable
```

Connection settings and server data are retained. Restart Hermes or the gateway
after updating.
