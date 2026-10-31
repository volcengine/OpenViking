# OpenViking Memory for Magpie

Adds automatic OpenViking context recall to model requests passing through
[Magpie](https://github.com/yetone/magpie). Reads the existing OpenViking client
configuration; no second copy of your server address or credentials is needed.

[中文](README_CN.md)

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
`extra_headers` (string values). Existing `auth_mode: "ldap"` configurations use
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

Behavior keys resolve through the shared schema and config builder; Magpie's
file-only policy excludes environment/workspace layers. Behavior keys use the
existing shared plugin names. `plugin.magpie` overrides
the corresponding `plugin` values; global `plugin.enabled: false` disables the
plugin. Tools require `plugin.magpie.mcpEnabled: true` specifically; a shared
`plugin.mcpEnabled` does not enable this gateway's tools.

## Automatic recall

- Only host-classified `local` and `magpie-key` origins receive memory. Shared
  gateway keys, forwarded/remote traffic and missing origins are skipped. A
  client-supplied agent name, session or cwd is never used as authenticated identity.
- Recall calls the server's context assembly API with the latest user query,
  coding purpose and shared context settings. Defaults avoid session query
  expansion and rewriting to keep latency bounded; explicitly configured
  context options are resolved by the shared builder. Server-rendered context preserves source URIs.
- Chat Completions, Responses, Anthropic Messages and Gemini requests each get
  context in their own system/instructions field. Existing messages, images,
  tools, model and streaming settings are preserved. Recall text is marked as
  historical reference, and a request already containing this plugin's block
  is not injected again.
- Default scope is the configured user's cross-project context. For project
  isolation set `recallPeerScope: "actor"` and an explicit `actor_peer_id` in
  the connection file. Without that peer, actor recall is skipped; cwd is never
  used to guess it. No OV session is created for automatic recall.
- The default recall timeout is 700 ms, inside Magpie's default 1000 ms hook
  budget. If increasing it, set Magpie's `gatewayTimeoutMs` plugin option above
  the recall timeout (leave startup/file-loading margin). The client timeout
  also caps the recall timeout. Cancellation propagates to the HTTP request.
- Empty results, unavailable services, invalid configuration and failed
  authentication leave the model request unchanged. Diagnostics use fixed
  status codes and byte counts, without credentials or remembered text.

Disable automatic recall in any Agent-native OV plugin when Magpie owns recall
for that Agent. Native plugins may still own session capture; this plugin does
not collect conversations or implement `onResponse` capture.

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

Magpie prefixes tool names with the plugin's short name. The tool callback has
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
