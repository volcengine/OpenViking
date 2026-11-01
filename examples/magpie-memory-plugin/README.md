# OpenViking for Magpie

Adds automatic OpenViking context recall to model requests passing through
[Magpie](https://github.com/yetone/magpie). Reads the existing OpenViking client
configuration; no second copy of your server address or credentials is needed.

[中文](README_CN.md)

The npm package is `@openviking/magpie-plugin`. Its marketplace display name is
**OpenViking**; the npm package name is a separate distribution identifier.
After the first npm publication, install it with:

```sh
magpie plugin add @openviking/magpie-plugin
```

## Install from this repository

Requires Bun through Magpie's plugin host and an OpenViking server supporting
`POST /api/v1/search/search` with `mode: "context"`. Use a Magpie version with
asynchronous gateway hooks and host-derived `origin`; `magpie-key` recognition
requires v0.1.2208 or later. Unknown origins on older hosts are skipped.

From the OpenViking repository root:

```sh
node examples/memory-plugin-shared/sync.mjs
magpie plugin add ./examples/magpie-memory-plugin
```

The generator prepares shared modules before a source-folder installation.
`npm pack` runs it automatically and produces a self-contained package. The
package release workflow publishes on main; installing by npm name requires the first
publication to have completed. Adding the whole OpenViking git repository as a
Magpie plugin does not select this subdirectory.

## Connection

The plugin reads `~/.openviking/ovcli.conf`, or the path in
`OPENVIKING_CLI_CONFIG_FILE` **in Magpie's process environment**. On a remote
Magpie host this is the remote host's file. Restart Magpie after changing its
environment; editing the file applies to the next operation without restarting.

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "<your-user-api-key>",
  "plugin": {
    "magpie": {
      "enabled": true,
      "autoRecall": true,
      "autoCapture": false,
      "recallContextTimeoutMs": 700,
      "recallMaxTokens": 1600,
      "recallPeerScope": "all",
      "mcpEnabled": false
    }
  }
}
```

Connection fields are the top-level `url`, `api_key`, `account`/`account_id`,
`user`/`user_id`, `actor_peer_id`/`peer_id`, `timeout` (seconds), and
`extra_headers` (or the CLI alias `extra_header`, string values). Existing `auth_mode: "ldap"` configurations use
`ldap_username` (or `user`) and `ldap_password` for Basic authentication;
`auth_mode: "oidc"` uses `oidc_token` (or `api_key`) as the bearer token.
LDAP/OIDC identities are resolved by the server, without asserted account/user
headers. API-key identity headers follow the shared client's
trusted-identity behavior when account/user are configured. Use the credentials
and identity appropriate to your server's authentication mode.

The file is read-only. Credentials from other environment variables, `ov.conf`,
`root_api_key` and `gateway_token` are not used. Missing or malformed files do
not fall back to a local server or retain an earlier connection. Redirects are
refused. Custom headers cannot replace authorization, identity or transport
headers (including `X-API-Key`). CLI output and echo options have no effect on this plugin.

Behavior keys reuse the existing shared schema and settings resolver; Magpie's
own adapter applies its file-only policy, hook timeout and opt-in tools without
changing other plugins. Behavior keys use the
existing shared plugin names. `plugin.magpie` overrides
the corresponding `plugin` values; global `plugin.enabled: false` disables the
plugin. Tools and capture require explicit `plugin.magpie.mcpEnabled: true` and
`plugin.magpie.autoCapture: true`, respectively; shared settings alone do not
enable either gateway capability.

Magpie's **Options** editor now accepts `autoRecall`, `autoCapture`, `mcpEnabled`,
`recallMaxTokens`, `recallContextTimeoutMs` and `nativeAgents`. These behavior
options override the corresponding file settings for this plugin; connection
credentials and identity always remain file-owned. `gatewayTimeoutMs` controls
the host's hook budget. The package supplies an example, not saved defaults;
omitted keys retain the file/default behavior. Hooks and tools use the same
options. Enabling tools or capture requires the literal boolean `true`.

File settings `plugin.magpie.bypassSession: true` or `bypassSessionPatterns`
matching the Magpie session ID or working directory skip automatic recall and
capture. Existing queued snapshots pause while bypass applies and can resume
once it is removed. Workers recheck bypass before appends and commits; already
in-flight operations cannot be undone. Explicit memory tools remain separately
controlled by `mcpEnabled`.

## Automatic recall

- The first user turn also reads the authenticated user's
  `viking://~/memories/profile.md`, so stable facts such as occupation and email
  do not depend on semantic search ranking. Profile and search run in parallel
  within the recall timeout; either can provide context if the other fails.
  The profile respects `profileTokenBudget` and uses at most half of
  `recallMaxTokens`, using the shared CJK-aware token estimate. Combined profile
  and search context stays within `recallMaxTokens` and is persisted with that
  turn. Retries and later turns replay the original snapshot.
- Only host-classified `local` and `magpie-key` origins with a nonempty Magpie
  session ID receive memory. Shared gateway keys, forwarded traffic and missing
  identities are skipped. The file's OV credentials remain the authority.
- Only a new textual user message triggers retrieval. Tool continuations replay
  known context without retrieving. Magpie skips its own internal title/classifier
  calls; explicit auxiliary/compaction/title/subagent request kinds are also skipped.
- Memory is appended to the corresponding **user message**, never to system
  instructions. The plugin persists a decision for that message's full history
  prefix before returning an enhanced request. Subsequent requests reconstruct
  those same blocks in the same positions. Retries reuse the decision, including
  empty or failed recalls. Identical text in a later turn has a different anchor.
- Chat Completions, Anthropic Messages and Gemini retain original system fields,
  text/image/tool blocks, model and streaming settings. Responses requires
  `store: false` and full `input` history; `previous_response_id`, `conversation`,
  `background` and `item_reference` requests are skipped.
- A private local ledger is partitioned by the actual OV connection identity,
  peer scope, config path, Magpie agent/session/workspace, protocol and model.
  Credential rotation or another agent/session cannot reuse the previous scope.
  Anthropic cache annotations are excluded only from protocol content-block
  identity; original wire fields and tool-input data remain unchanged.
  Prefix hashes distinguish branches; unknown trimmed/compacted histories skip
  enhancement rather than matching messages by text. A fresh history containing
  one new user message can start a new prefix under the same session.
- Recognized OpenViking context envelopes or explicit `nativeAgents` settings
  yield automatic enhancement. This is not installation detection or a guarantee
  against every duplicate. Disabling `autoRecall` prevents new searches while
  replaying known history; disabling the plugin entirely disables both.
- The shared context builder owns query settings, budgets and result relevance.
  Default recall timeout is 700 ms inside Magpie's default 1000 ms hook budget.
  Increasing it requires raising Magpie's `gatewayTimeoutMs` with I/O margin.
  Cancellation reaches the HTTP request. Query filters use the shared parser.
- Default peer scope is the configured user's cross-project context. Actor scope
  requires an explicit `actor_peer_id`; a missing peer skips enhancement.

### Local replay state

The default directory is `~/.openviking/magpie/recall/`; set
`plugin.magpie.dataDir` to change its parent directory. Files contain hashed
message anchors and the exact recalled blocks, including any empty/error
outcome, not raw credentials or a copy of the client transcript. They may contain
sensitive recalled content. The directory is mode 0700 and JSON files are 0600.
Writes use a per-session lock and atomic rename, shared by app and CLI hosts.
A retired host's lock can be reclaimed after one minute.

There is no automatic deletion of replay files in this version. Retain them for
active chats; deleting them, corrupt state or unrecognized history changes cause
existing conversations to skip enhancement. Start a new chat to establish a fresh
history. Restoring signed reasoning depends on retaining the matching history
and replay state; live-provider signature behavior has not been validated.
Each session file is capped at 1 MiB; request replay supports up to 2 MiB of JSON
and 2,000 messages. Unsupported shapes, storage failures and capacity limits
leave the request unchanged. Diagnostics contain fixed codes and byte counts,
not credentials or recalled text.

The plugin directly calls the ordinary OV REST/MCP service. **OV Gateway need
not be enabled.** Automatic recall does not create OV sessions.

## Automatic capture and native-plugin ownership

The first human turn cannot have capturable assistant/tool content before it.
Known title/compression exchanges remain excluded from subsequent full-history
recovery and final snapshots; recall anchors also skip these utility exchanges
without changing the original messages sent to the model. Known failed or
truncated turns are recorded as hashes, so later history cannot silently restore
them. A successful completion callback for a retry can resolve the rejection;
tool-call continuations and genuinely lost callbacks retain their recovery path.
Revisions invalidate stale worker snapshots after a known rejection.

Capture is **off by default**. Set `plugin.magpie.autoCapture: true` in
`ovcli.conf`, or save `"autoCapture": true` in Magpie's plugin Options.
Start a new chat after enabling it. The first request binds capture to that
connection and first user message; unknown existing history is skipped,
including after credentials change, instead of copying earlier private text
into a newly selected identity.
Completed exchanges are queued locally, then a detached bounded worker appends
only new messages to a connection/agent/session/workspace/protocol-scoped OV
session. System instructions, injected memory, reminders and private reasoning
are excluded. Complete tool call/result evidence is preserved; incomplete tool
loops, failed and truncated replies are not saved as completed turns. The next
human request can recover a previous completed exchange whose response callback
was lost, provided full client history is still available.

After a complete queued exchange is written, the worker calls the ordinary
OV Session `commit` endpoint with `keep_recent_count: 0`. Session-side automatic
commit is disabled so there is one submission owner and no dependency on an
OV idle scheduler or OV Gateway. Replayed callbacks do not submit another
commit. The response acknowledges archive creation and schedules extraction;
it is not proof that new memories were produced. A lost commit acknowledgement
is reconciled from session metadata before any further appends. An unresolved
outcome pauses the queue rather than requesting extraction twice.

Before either recall or capture, Magpie checks for complete, nonempty
`<openviking-context>`, `<relevant-memory>` or `<relevant-memories>` envelopes
on their own lines in system/user text. Markdown code fences, blockquotes,
indented code, inline mentions and incomplete tags do not establish ownership;
assistant/tool payloads and non-text fields are ignored. The legacy
`[OpenViking memory context]` header alone no longer triggers automatic yielding.

This is a payload heuristic, not detection of an installed plugin or proof that
native capture is active. An unquoted example formatted exactly like an actual
injection cannot be reliably distinguished. For known native-managed agents,
configure `nativeAgents` explicitly. Recognized envelopes yield both automatic
recall and capture for that connection/session and discard queued snapshots.
Pending raw snapshots are removed immediately on yielding, without waiting for
a worker. Workers reload connection and capture settings and recheck ownership
before appends/commits, and do not delete a new retry snapshot when retiring an
older one. Already in-flight HTTP operations
and previously delivered records cannot be undone by local cleanup.
Ownership persists if markers disappear from later history. Existing ownership
records are retained; start a fresh chat after removing a native plugin or
correcting an earlier false match. Explicit tools are unaffected.

Known native OpenViking memory-compression and Claude Code session-title utility requests are excluded from both automatic recall and capture, even when they run without injection markers. This is a fallback based on known prompt framing, not a complete classification of every auxiliary request. Configure `nativeAgents` for agents known to have a native OV plugin, including their unmarked internal requests.

A remote gateway cannot inspect client installations, and a capture-only native
plugin might inject no marker. Use `"nativeAgents": ["pi", "codex", "claude"]`
in Magpie Options (or `plugin.magpie.nativeAgents`) to skip both automatic
capabilities for known native-managed agents. IDs must match Magpie's agent IDs.
Installed-but-inactive plugins are not inferred from filenames.

Capture snapshots live under `<dataDir>/capture/`, with private mode-0700
directories and mode-0600 files. Unlike recall state, pending files contain
conversation text and tool output. They contain no connection credentials.
At most 32 pending snapshots per session, 3 MiB per snapshot and 2,000 messages
are supported. Successful delivery removes the queued snapshot; acknowledgement
hashes and ownership state remain. Old queues are retried during a bounded
worker run and on subsequent capture-enabled requests. Disabling capture pauses
file-configured queued work; identity changes cannot redirect it to another user.

Retries and concurrent workers use stable prefix IDs and durable progress.
`source_message_ids` are provenance, not OV idempotency keys. If an append's
acknowledgement is lost, the worker checks source IDs in the active session and
up to 32 archives. It never blindly resends an uncertain append: unresolved
delivery stays pending for inspection. Changed delivered history pauses that
session instead of copying it again; use a new chat for rewritten/compacted
histories. These conservative cases may delay or skip capture. A host crash
before a completion callback, without a subsequent full-history request, cannot
be recovered from Magpie's best-effort callback alone.

## Optional tools

Set `plugin.magpie.mcpEnabled: true` (or `mcpEnabled: true` in Magpie Options), then enable this plugin's server in
Magpie's Library/MCP router for the intended agents. The plugin also participates
in Magpie's combined `/mcp/magpie/tools` server. Tools are:

| Tool | Behavior |
| --- | --- |
| `status` | Authenticated connectivity check; fixed status without secrets |
| `find` | Search accessible memories, resources and skills |
| `read` | Read up to 200 lines from a `viking://` URI |
| `ls` | List up to 100 entries under a `viking://` directory |
| `remember` | On explicit user request, create a session, add the supplied text and submit memory extraction |

With the npm installation, Magpie's current tool naming rules produce
`magpie-plugin_status`, `magpie-plugin_find`, `magpie-plugin_read`,
`magpie-plugin_ls` and `magpie-plugin_remember`.
Source-folder installations use the folder's name as the prefix instead.
The tool callback has
no authenticated caller identity: **enabling tools delegates this file's OV
identity to callers allowed to use the Magpie MCP server**. Keep tools disabled
on a shared gateway unless every permitted tool caller should have that access.
Server ACLs still apply, and tool arguments cannot override credentials or identity.
Configured `actor_peer_id` is also sent as each saved message's `peer_id`,
so project memory extraction receives the intended ownership. Tools remain registered when disabled, but calls are refused (`status` reports
`disabled`), so file changes take effect without reloading the tool list.

Tool failures preserve sanitized HTTP status, error category (such as
`not_found`, `conflict`, `rate_limited`, `timeout`, or `cancelled`) and trace ID
on the error object; Magpie displays the safe message without upstream error
text or credentials. Automatic recall still fails open with coarse diagnostic codes.

`remember` submits asynchronous extraction; the returned task ID is not proof
that a memory has been created. Writes are not automatically retried. An
interrupted write can leave a partially populated session; inspect OV before
repeating an uncertain submission.

## Marketplace metadata

[marketplace.json](marketplace.json) contains the proposed **OpenViking** listing,
with the official icon and English/Chinese descriptions. Its `kind: "middleware"`
is the discovery category used for gateway memory integrations; the package
runs through asynchronous `magpie.hooks`. The same icon is declared in
`package.json` under `magpie.icon` for npm discovery and installed rows.

Publishing the npm package does not automatically add the recommended listing.
After publication, submit the entry in `plugins` to
[Magpie's community registry](https://github.com/magpie-community/plugins/blob/main/registry.json).
The entry does not claim the Magpie community-maintained badge.

## Development

```sh
node examples/memory-plugin-shared/sync.mjs
npm run check --prefix examples/magpie-memory-plugin
npm test --prefix examples/magpie-memory-plugin
cd examples/magpie-memory-plugin
npm pack
```

The tests use temporary configuration and local HTTP fixtures, not your OV
configuration. An additional test runs against Magpie's actual Bun plugin host
when `MAGPIE_PLUGIN_HOST` points to its `internal/plugin/host.js`. Set
`MAGPIE_PLUGIN_TARGET` to an unpacked npm artifact to exercise that package.
Use `BUN_BIN` when Bun is not available as `bun`.

See [Magpie's plugin contract](https://usemagpie.ai/docs/plugins) for host loading,
gateway hook budgets, origin classification and MCP routing.
