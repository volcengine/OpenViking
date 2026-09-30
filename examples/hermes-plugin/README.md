# OpenViking Memory Provider

Context database by Volcengine (ByteDance) with filesystem-style knowledge hierarchy, tiered retrieval, and automatic memory extraction.

This plugin connects Hermes to OpenViking for long-term memory and knowledge
retrieval. The installation steps below use a reviewed OpenViking commit.

For Hermes releases that still bundle OpenViking, follow the
[Hermes integration guide](../../docs/en/agent-integrations/05-hermes.md) and run
`hermes memory setup openviking`. No external plugin installation is needed.

For development and licensing details, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Install

Hermes v2026.9.24 is the tested release baseline.

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

Existing connection settings and server data are retained. Restart Hermes or
the gateway after the upgrade. For catalog installations, use
`hermes plugins update openviking`.

### Upgrading to 3.0.0

3.0.0 changes names that other configuration may refer to. Check these before
you upgrade:

- **Tools are renamed, with no aliases.** The six `viking_*` tools are gone.
  The plugin now registers the server's MCP tools as `openviking_*` (see
  [Tools](#tools)). Update any Hermes tool allow-lists, prompts, skills or
  scripts that name `viking_search`, `viking_read`, `viking_browse`,
  `viking_remember`, `viking_forget` or `viking_add_resource`. Roughly,
  `viking_browse` maps to `openviking_list` and `openviking_tree`, and the
  other five keep their suffix.
- **New OpenViking session ids.** Turns are uploaded to
  `hermes-<Hermes session id>` (see [Session ids](#session-ids)). This applies
  to uploads from 3.0.0 on. Sessions already on the server keep their ids,
  and pending commits left by 2.x are recovered under the id they recorded.
- **Minimum server versions.** Recall and capture need OpenViking 0.4.13 or
  newer, the tools need 0.4.14 or newer, and the full tool set needs 0.4.22
  (see [Requirements](#requirements)).
- **Capabilities no longer offered.** `viking_browse`'s `stat` action, the
  batch limits of `viking_read`, and the `wait`, `timeout` and `instruction`
  arguments of `viking_add_resource` have no counterpart. `openviking_read`
  returns full content only; for cheaper views use `openviking_tree` with
  abstracts, the summaries in search results, or read a directory's
  `.overview.md`. `openviking_remember` no longer returns the one-shot session
  id, task id or failed stage that `viking_remember` reported.
- **Recall tool results are captured.** 2.x left the calls and results of its
  search, read and browse tools out of the uploaded turn. 3.0.0 uploads them
  like any other tool result.
- **Root API keys cannot use the tools.** The server's `/mcp` endpoint rejects
  the root key; configure a user or account admin key if you relied on one.

Restart Hermes or the gateway after the upgrade. The first agent fetches the
tool list from the server; rerunning `hermes memory setup openviking` also
refreshes the cached list.

## Requirements

- Python 3.11 or newer in the Hermes environment
- An OpenViking server reachable from Hermes, or OpenViking Service credentials
- For a self-hosted server, OpenViking installed in its own environment or container

The plugin connects over HTTP. Do not install the OpenViking server into the
Hermes environment. For local server start from the setup wizard, make the
`openviking-server` command available on `PATH`.

Server versions:

| OpenViking server | What works |
|-------------------|------------|
| 0.4.13 or newer | Automatic recall and turn capture (the minimum for this plugin) |
| 0.4.14 or newer | The `openviking_*` tools, which the plugin reaches through the server's `/mcp` endpoint |
| 0.4.22 or newer | The full tool set listed under [Tools](#tools) |

With a server older than 0.4.14, recall and capture keep working, the session
registers no OpenViking tools, and one warning is logged. The `viking://~` home
alias requires OpenViking 0.4.16 or newer for user and admin credentials, and
0.4.17 or newer for root or local development.

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

The API key is sent only as `Authorization: Bearer <key>`, on REST and MCP
requests alike. User and admin API keys let OpenViking derive account/user
identity from the key. In local or trusted deployments without an API key,
Hermes sends `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` as identity headers.
The server's `/mcp` endpoint rejects the root API key, so the tools need a user
or account admin key; recall and capture are not affected.

Hermes also sends `User-Agent: openviking-memory-hermes/<plugin version>` on
OpenViking requests. This standard harness identifier contains the plugin
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

The scope applies to automatic query recall on every route below, including its
list-search fallback. If a server does not confirm sender-scoped context
recall, the provider uses scoped list recall. Enabled resource recall includes
common resources and, in `peer` mode, the sender's resources.

This is a retrieval setting, not an access-control boundary. It does not filter
shared conversation history or change the explicit `openviking_*` tools, native
memory mirroring, or credentials. Setting `recall_scope` alone does not change
gateway sessions; the confirmed Shared Agent setup preset applies those
settings. Explicit tools retain the configured assistant view. Use separate
OpenViking users and credentials when participants require separate access
rights.

### Recall routes

Automatic recall picks its request from `recall_scope` and the current sender:

| `recall_scope` | Sender known | Request |
|----------------|--------------|---------|
| `shared` | any | Server context mode (`mode: "context"`, `peer_scope: "all"`) |
| `peer` | yes | Server context mode with `peer_scope: "actor"`; if the server does not confirm the actor scope, list search with explicit roots |
| `peer` | no | List search with explicit roots that hold only common memory (and common resources). The CLI Personal Agent preset takes this route |
| unset | any | The request shape of earlier releases: list search, or context mode when `recall_compress` is on |

On the context-mode routes the injected block is the server's `rendered`
context, and `recall_limit` becomes per-type `quotas`; `recall_full_read_limit`
and `recall_prefer_abstract` apply only to list search. A 400 or 422 response,
or a response without `rendered`, `digest` or `entries`, falls back to list
search within the same deadline. List search tries the session-aware
`search/search` first and then `search/find`.

`recall_context_mode` (`OPENVIKING_RECALL_CONTEXT_MODE`, default `true`)
switches the two context-mode routes back to list search when set to `false`.
It is a transition switch and will be removed once context mode has been
stable for a release. Non-primary contexts (see below) use list search on
these routes unless `recall_compress` is on, and send no `session_id` with the
context request.

### Recall time budget

Hermes waits at most 8 seconds for automatic recall and then drops the result,
so each turn's recall has one deadline of at most 7.5 seconds. It covers the
connection check, the once-per-session profile block and the query recall; the
last two run concurrently. `recall_timeout_seconds` (default 4 s) sets that
deadline and is capped at 7.5 s; `recall_request_timeout_seconds` (default 3 s)
limits each request within it. A request that still has a fallback, such as
the context request or the session-aware search, leaves 1 s of the deadline
for that fallback. If one part
misses the deadline, the turn gets the other part; a profile block that missed
it is injected on a later turn.

### Recall indicator

When automatic recall injects OpenViking context, Hermes shows a status line
such as `OpenViking — recalled 3 memories`. The count is the number of recalled
entries. A turn that injects only the session-start profile block, or a
compressed digest without an entry list, shows `recalled relevant memory`.
Nothing is shown when recall finds nothing, times out, or cannot reach the
server.

If `openviking` is the selected provider but no endpoint is configured, the
Hermes "reports unavailable" warning names the cause: no endpoint in the
profile's `.env` or `config.yaml`, or a linked `ovcli.conf` that is missing,
unreadable, or has no `url`.

## Tools

The tools are the OpenViking server's own MCP tools, reached through the
server's `/mcp` endpoint (OpenViking 0.4.14 or newer). Each is registered in
Hermes as `openviking_<server tool name>`, with the description and input
schema the server's `tools/list` returns. A tool the server does not list is
not registered.

Registered by default:

| Tool | Plugin handling |
|------|-----------------|
| `openviking_find`, `openviking_list`, `openviking_tree`, `openviking_grep`, `openviking_glob`, `openviking_health`, `openviking_remember` | Forwarded unchanged |
| `openviking_search` | In a primary context, the plugin adds the current OpenViking session id (see [Session ids](#session-ids)), so the server can use the conversation for intent analysis |
| `openviking_read` | Forwarded; the YAML front matter of a generated `.abstract.md` or `.overview.md` is removed from the result |
| `openviking_forget` | Checked locally first (see below) and always sent with `recursive: false`; the `recursive` argument is not offered |
| `openviking_add_resource` | Remote URLs are forwarded. A local file or directory is checked against Hermes's read blocklist, uploaded through the REST temp-upload endpoint (a directory as a zip named after it, skipping symlinks, paths that escape it and blocked files), and then passed to the server tool as `temp_file_id` |

`write`, `edit`, `add_skill`, `list_watches` and `cancel_watch` are registered
only when listed in `extra_tools` (comma-separated, with or without the
`openviking_` prefix; `OPENVIKING_EXTRA_TOOLS` overrides it):

```yaml
memory:
  openviking:
    extra_tools: write, edit
```

`openviking_write` and `openviking_edit` change memory files directly and
bypass the native memory mirror's registry (see below).

Tool calls use the same endpoint, credentials, account, user and assistant peer
as automatic recall and capture. Each call opens a short MCP session, with a
3-second limit for the handshake and 15 seconds for the whole call. Text results
longer than 50 KiB or 2000 lines are cut with a note; images and audio are
replaced by a one-line placeholder. Server errors come back as Hermes tool
errors with the HTTP status. After a transport failure a read-only tool is
retried once; a write tool is not replayed, and its error says that the
request may already have been applied.

### Tool catalogue and the first session

Hermes asks for the tool list before the provider is initialized and keeps the
first answer for the whole agent. The plugin answers from, in order:

1. the in-process cache for the current connection;
2. `$HERMES_HOME/openviking/tools_cache.json`, the last successful list for the
   same endpoint;
3. a live `tools/list`, limited to 3 seconds;
4. otherwise no tools for this agent, with one warning. Recall and capture are
   not affected.

A successful `initialize` refreshes both caches in the background, so a changed
server tool list takes effect when Hermes builds the next agent. `hermes memory
setup openviking` fills the disk cache after it validates the connection, so the
first session after setup has its tools even if the server is slow to answer.

The results of `openviking_find`, `openviking_search`, `openviking_read`,
`openviking_list`, `openviking_tree`, `openviking_grep` and `openviking_glob` are
captured with the turn like any other tool result, which lets the server
attribute which memories were used.

## Memory Writes And Deletes

`openviking_remember` is the server's `remember` tool: it submits the fact to
OpenViking's memory extraction, which can add, merge, or skip a memory. It
does not commit or rotate the live Hermes conversation session. The server
returns a short confirmation without a session or task id.

Successful Hermes built-in `memory` mutations are mirrored to OpenViking in
order. The active profile records each mirrored entry's exact URI in
`$HERMES_HOME/openviking/memory_mirror_registry.json`:

| Hermes action | OpenViking operation |
|---------------|----------------------|
| `add` | Create a file under user memory or the configured peer, then record its URI |
| `replace` | Match the committed event's full previous content and target, update the same URI, and wait for semantic/vector refresh |
| `remove` | Match the committed event's full previous content and target, delete that exact URI, and wait for semantic cleanup |

Replacing a mapped entry recreates its file if it was deleted directly in
OpenViking, for example with `openviking_forget`.

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
explicit `openviking_remember` results, and copies created before this registry are
outside its scope. Use `openviking_forget` with an exact URI to remove those copies.

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

`openviking_forget` is intentionally narrow. It only accepts concrete user memory
file URIs, such as
`viking://user/default/peers/hermes/memories/preferences/mem_abc123.md`, or the
`viking://~/...` self alias. Under `viking://user/...` the user id is required
and must match the calling identity; the uid-less `viking://user/memories/...`
and `viking://user/peers/...` shorthands are deprecated and rejected. Files
directly under `memories/`, such as `viking://user/default/memories/profile.md`,
are also allowed because OpenViking supports them. The tool rejects directories,
resources, skills, sessions, generated summary files, and URIs with query
strings or fragments. Use the `ov` CLI or OpenViking's admin APIs for broader
resource and directory cleanup.


### Cloud recall compression

Set `OPENVIKING_RECALL_COMPRESS=server` to enable cloud recall compression, or
`auto` to let the server decide whether to rewrite. Both use search
`mode=context`; `server` sends `rewrite=true`, and `auto` sends `rewrite="auto"`.
The server digest takes precedence over raw rendered context, and `no_relevant`
suppresses injection. The default remains `off`; no local compressor is launched.

The Hermes config equivalent is `memory.openviking.recall_compress: server`.
When enabled without explicit recall timeout settings, the request and total
recall deadlines default to the whole 7.5-second recall budget. The rewrite
request keeps 1 second of that budget for a fallback: if it times out, recall
falls back to the search without rewrite. Older servers fall back to the
existing search path within the same budget.

### Session ids

The OpenViking session for a Hermes session is named `hermes-<Hermes session id>`
(an id that already starts with `hermes-` is kept as is). Recall, the search tool,
turn uploads, commits and pending-commit markers all use this name. Before 3.0 the
plugin used the bare Hermes id; markers left by 2.x are still recovered under the
id they store, so sessions uploaded before the upgrade are committed where they are.
Existing OpenViking sessions keep their ids; only turns uploaded by 3.0 or later
go to the prefixed name, including later turns of a Hermes session that started
before the upgrade.

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

### Turn uploads and failed uploads

`sync_turn` returns at once; the turn is uploaded on a plugin writer thread
with the connection and sender captured when the turn ended. Messages go to
the batch endpoint in batches of at most 100, with one retry for a retryable
failure and then one message at a time. A turn that yields no structured
messages is sent as one user and one assistant text message, each cut to 4000
characters.

Messages that still fail are kept in memory, per OpenViking session and
connection, and sent before that session's next upload or commit:

| Failure | Handling |
|---------|----------|
| Network error, 408, 429, 5xx, or a 409 marked retryable | Kept and resent later |
| 401 or 403 | Not retried at once; kept while the connection settings stay the same, dropped with a warning after they change |
| Any other 4xx | Dropped with a warning |
| 404 or 405 from the batch endpoint | Resent one message at a time |

Each backlog holds at most 2000 messages and 8 MiB; beyond that the oldest are
dropped with a warning. A commit never overtakes unsent messages of its
session: while a backlog is left, the commit is skipped and the pending marker
stays. A backlog of a session left behind by a switch is sent with the next
upload on the same connection, and that session is then committed. The backlog
lives only in memory, so messages still unsent when the process exits are
lost; the pending marker lets a later run commit what reached the server.

### Non-primary contexts

When Hermes initializes the provider with `agent_context` set to `cron`, `subagent`,
or `flush`, recall and profile reads keep working. Automatic turn uploads,
session-end/switch commits, and native memory mirroring are skipped for that
context. Startup recovery can still commit pending messages from earlier sessions.
Explicit `openviking_*` tools keep their normal behavior, including writes and deletes.
Interactive sessions (and hosts that predate `agent_context`) keep the previous
automatic write behavior.
