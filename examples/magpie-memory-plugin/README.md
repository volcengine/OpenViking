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
plugin. Tools require `plugin.magpie.mcpEnabled: true` specifically; a shared
`plugin.mcpEnabled` does not enable this gateway's tools.

## Automatic recall

- The first user turn also reads the authenticated user's
  `viking://~/memories/profile.md`, so stable facts such as occupation and email
  do not depend on semantic search ranking. Profile and search run in parallel
  within the recall timeout; either can provide context if the other fails.
  The profile uses at most half the character budget and is persisted with
  that turn. Retries and later turns replay the original snapshot.
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
  Prefix hashes distinguish branches; unknown trimmed/compacted histories skip
  enhancement rather than matching messages by text. A fresh history containing
  one new user message can start a new prefix under the same session.
- Existing OpenViking context markers skip enhancement, including those from
  native OV plugins or OV Gateway. Disabling `autoRecall` prevents new searches
  while replaying known history; disabling the plugin entirely disables both.
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
not be enabled.** It does not collect conversations or create OV sessions for
automatic recall; Agent-native plugins may still own capture. Choose one owner
for automatic recall when also using an Agent-native OV plugin.

## Optional tools

Set `plugin.magpie.mcpEnabled: true`, then enable this plugin's server in
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
