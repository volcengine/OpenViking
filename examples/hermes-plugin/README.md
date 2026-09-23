# OpenViking Memory Provider

Context database by Volcengine (ByteDance) with filesystem-style knowledge hierarchy, tiered retrieval, and automatic memory extraction.

This directory prepares the standalone OpenViking provider for migration out of
Hermes core. The installation and upgrade steps below are for migration testing
in a separate Hermes profile.

For normal use while Hermes still bundles OpenViking, follow the
[Hermes integration guide](../../docs/en/agent-integrations/05-hermes.md) and run
`hermes memory setup openviking`. No external plugin installation is needed.

See [DEVELOPMENT.md](DEVELOPMENT.md) for the source, license, migration contract,
and test commands.

## Install for migration testing

Use a current Hermes version with repository-subdirectory plugin support:

```bash
hermes plugins install 'https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin'
hermes plugins enable openviking
hermes memory setup openviking
hermes memory status
```

The equivalent shorthand is `volcengine/OpenViking/examples/hermes-plugin`.
Hermes installs this directory as `$HERMES_HOME/plugins/openviking/` and installs
its `pyproject.toml` dependencies under Hermes's dependency constraints.

If Hermes still includes the bundled OpenViking provider, that copy takes
precedence. The external copy becomes active after the bundled copy is removed.
Keep `memory.provider: openviking` and your existing configuration. No memory
data needs to move. Automatic installation after core removal also requires a
published `openviking` entry in the Hermes catalog; this directory alone does
not register one.

## Upgrade a test installation

For a direct subdirectory installation, use force-reinstallation instead of
`hermes plugins update openviking`. Hermes does not retain the repository's
`.git` directory when it installs a subdirectory.

Replace the placeholder with the reviewed OpenViking commit's full 40-character
SHA, and run this command in the same Hermes profile as the original installation:

```bash
hermes plugins install 'volcengine/OpenViking/examples/hermes-plugin' \
  --force --ref '<full-40-character-commit-SHA>' --enable
```

Existing connection settings and server data are retained. Restart Hermes or
the gateway after the upgrade. Once the plugin is registered in the Hermes
catalog, copies installed through the catalog use `hermes plugins update openviking`.

## Requirements

- Python 3.11 or newer in the Hermes environment
- An OpenViking server reachable from Hermes, or OpenViking Service credentials
- For a self-hosted server, OpenViking installed in its own environment or container

The plugin connects over HTTP. Do not install the OpenViking server into the
Hermes environment. For local server start from the setup wizard, make the
`openviking-server` command available on `PATH`.

OpenViking 0.2.14 or newer is required. Hermes can identify older servers that
expose the legacy status-only health response, but those releases do not provide
the authenticated-user identity contract required by this integration.
The `viking://~` home alias requires OpenViking 0.4.16 or newer for user and
admin credentials, and OpenViking 0.4.17 or newer for root or local development.

## Setup

For a self-hosted deployment, prepare OpenViking in its server environment:

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

Hermes-side provider config is read from environment variables in the active
profile's `.env`:

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_ENDPOINT` | `http://127.0.0.1:1933` | Server URL |
| `OPENVIKING_API_KEY` | (none) | User/admin API key for authenticated servers |
| `OPENVIKING_ACCOUNT` | `default` | Tenant account for local/trusted mode |
| `OPENVIKING_USER` | `default` | Tenant user for local/trusted mode |
| `OPENVIKING_AGENT` | (none) | Optional peer ID for separate assistant context |

When `OPENVIKING_API_KEY` is set, Hermes lets OpenViking derive account/user
identity from the key. In local or trusted deployments without an API key,
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
order remains environment, linked OpenViking config, then Hermes YAML. To use
no peer, remove the peer value from each configured source and start a new
Hermes session.

Upgrades do not move or delete existing memories. Installations that relied
on the old implicit `hermes` peer now use user memory for new writes. Without
a peer ID, default OpenViking search covers user memory and existing peer
memories under the same OpenViking user. Old peer memories stay at their
existing paths and remain searchable. Ranking and result limits determine
which memories are returned. Keep a peer ID if you need the narrower view.

Set `agent: hermes` to restore peer-scoped writes. Memories written at user
scope before this change stay there and remain searchable. This setting
changes future writes, not the location of existing memories.

## Tools

| Tool | Description |
|------|-------------|
| `viking_search` | Semantic search with fast/deep/auto modes |
| `viking_read` | Read content at a viking:// URI (abstract/overview/full) |
| `viking_browse` | Filesystem-style navigation (list/tree/stat) |
| `viking_remember` | Submit a fact through OpenViking session memory extraction |
| `viking_forget` | Delete one exact `viking://` memory file URI |
| `viking_add_resource` | Ingest URLs/docs into the knowledge base |

## Memory Writes And Deletes

`viking_remember` creates a one-shot `hermes-remember-<random>` OpenViking
session, adds the fact as one message, and commits the session with no retained
tail. The session remains available in OpenViking for audit. OpenViking then
classifies the source and can add, merge, or skip a memory through its normal
extraction pipeline. The tool returns the one-shot session ID and the
extraction task ID when the server provides one. Extraction continues
asynchronously after the tool returns.

The tool returns `status: submitted` because extraction can add a memory, merge
the fact into an existing memory, or produce no memory operation. It does not
promise that OpenViking created a distinct memory file. The fact is submitted
as an unchanged `user` message so OpenViking owns the final classification.
The legacy `category` argument is still accepted from existing callers but is
not advertised or used. The one-shot session is separate from the live Hermes
conversation, so an explicit remember does not commit or rotate the active
conversation session.

If the message request or commit fails, the error includes the canonical
session URI, the failed stage, the observed message status, and an `ov session
commit <session-id>` recovery command. Inspect the session first. An archive
means the commit completed. A non-empty live `messages.jsonl` with no archive
means the message was accepted but still needs a commit. An empty live file
without an archive is ambiguous and must not trigger an automatic resubmission.
Use the same OpenViking profile and credentials as Hermes for manual recovery.
OpenViking server auto-commit is disabled by default, so an accepted message
whose explicit commit fails normally remains live and unextracted until it is
manually committed.

Hermes built-in `memory` tool additions are mirrored to OpenViking after the
local memory operation succeeds:

| Hermes action | OpenViking operation |
|---------------|----------------------|
| `add` | `content/write` with `mode=create` under user memory, or the configured peer memory directory |

Built-in `replace` and `remove` operations are not mirrored because Hermes
native memory entries do not yet carry stable OpenViking file URIs. Use
`viking_forget` when the user explicitly asks to delete a specific OpenViking
memory URI.

`viking_forget` is intentionally narrow. It only accepts concrete user memory
file URIs, such as
`viking://user/default/peers/hermes/memories/preferences/mem_abc123.md`, or the
`viking://~/...` self alias. Under `viking://user/...` the user id is required
and must match the calling identity; the uid-less `viking://user/memories/...`
and `viking://user/peers/...` shorthands are deprecated and rejected. Files
directly under `memories/`, such as `viking://user/default/memories/profile.md`,
are also allowed because OpenViking supports them. The tool rejects directories,
resources, skills, sessions, generated summary files, and URIs with query
strings or fragments. Use OpenViking's MCP, CLI, or admin APIs for broader
resource and directory cleanup.


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
