---
description: Match an OpenViking Gateway symptom, Studio label or error code to its cause and fix.
---

# Troubleshoot OpenViking Gateway

Use this page when a client gets an error, memory or saving is missing, or Studio flags a problem. Look in three places first: the **OpenViking** card on the **Overview** tab, the **Requests** tab filtered to **Issues**, and the server log (`docker compose logs openviking` with Docker Compose, or the standalone gateway's own output). Then find your symptom in the table below; each section reads on its own and links to the setting involved.

## Common symptoms {#common-symptoms}

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| The server stops right after you enable the gateway | The embedded gateway failed its startup checks. | [The gateway does not start](#the-gateway-does-not-start) |
| Studio shows a card instead of the gateway tabs | The gateway is off, your OpenViking API key is missing or invalid, or the gateway cannot reach OpenViking. | [Studio shows a setup card](#studio-shows-a-setup-card) |
| The Upstreams and Profiles tabs are read-only | The deployer set a gateway management key. | [Unlock editing](06-manage-in-studio.md#unlock-editing); if nobody has the key, [Lost the management key](../guides/22-gateway-operations.md#lost-the-management-key) |
| **Couldn't issue the key** | The OpenViking API key behind the new gateway key did not pass the check. | [Issuing a key fails](#issuing-a-key-fails) |
| A client gets an error with a `reason` in its body | The gateway refused the request itself, mostly before it reached a provider. | [Clients get an error from the gateway](#clients-get-an-error-from-the-gateway) |
| A request in the log shows HTTP 400 or above | The provider rejected or failed the call. | [The upstream returned an error](#the-upstream-returned-an-error) |
| A conversation gets no recall and is not saved | An OpenViking plugin was detected, and the gateway left recall, saving and tools to the plugin. | Expected: it keeps content from being added or saved twice. See [When both are in use](../guides/15-gateway.md#when-both-are-in-use). |
| Requests go without memory | OpenViking unreachable or too slow, budget used up, nothing relevant, and so on. | [No memory is added](#no-memory-is-added) |
| Saving shows **Failed** | Writing a finished turn to OpenViking failed: OpenViking was unreachable or too slow, or refused the write. | Nothing retries it; once OpenViking is back, the conversation's next turn that ends saves what is missing. If it keeps failing, fix the cause. See [Conversations do not appear in OpenViking](#conversations-do-not-appear-in-openviking). |
| Conversations are saved, but are never archived or turned into memories | The session was not committed. By default the gateway commits: the idle time has not passed yet; the commit keeps failing (`could not commit` in the gateway log; the gateway tries again every minute, and drops a commit that OpenViking refuses with 401, 403 or 404); or the key used to save the conversation was revoked or expired before the idle commit was made, and the commit was dropped. With `openviking_auto_commit: true`, OpenViking commits: the conversation stays below **Commit after**, and the OpenViking server does not have `memory.session_auto_commit.enabled` on. | See [Sessions appear, but no memories come from them](#conversations-do-not-appear-in-openviking). |
| The profile has tools on, but the conversation has none | No request that could use tools has arrived yet, loading the tool list failed (the next such request tries again), the client does not send the full history, or the client has its own OpenViking tools. | Check the reason in the request log; see [When tools are not offered](04-openviking-tools.md#when-tools-are-not-offered). **The client already has OpenViking tools** (`tool_name_collision`) is expected when the client has the OpenViking MCP server; **OpenViking's tool list couldn't be loaded** (`tools_unavailable`) clears on its own once OpenViking answers; for other reasons, fix the cause and start a new conversation. |
| The **First-call cache hit rate** dropped, or Overview warns about cache misses | Memory could not be replayed, the conversation switched upstreams, or the client or relay changes the request prefix. | [The first-call cache hit rate dropped](#the-first-call-cache-hit-rate-dropped) |
| The provider rejects a long conversation as too long | The gateway does not know the model's real window and compacts too late. | [Long conversations hit the context limit](#long-conversations-hit-the-context-limit) |
| A file import or download link fails | `public_url` is missing or wrong, or the proxy does not forward the file paths. | [File imports or downloads fail](#file-imports-or-downloads-fail) |
| A request is flagged with an issue label | The gateway could not fully handle that request. | [Issues on degraded requests](#issues-on-degraded-requests) |

## The gateway does not start {#the-gateway-does-not-start}

When an embedded gateway cannot start, the whole OpenViking Server stops. Read the startup error in the server log (`docker compose logs openviking` with Compose) and match it below.

| Problem (log text) | Cause and fix |
| --- | --- |
| v0.5 data without the old key (`holds data encrypted by OpenViking Gateway 0.5`) | The storage directory holds data encrypted by the v0.5 gateway, but there is neither `OPENVIKING_GATEWAY_ENCRYPTION_KEY` nor `encryption.key` in `storage_path`. Provide the original key; see step 5 of [Migration](../guides/22-gateway-operations.md#migration). |
| Removed flags or fields (`unrecognized argument --…`, `Extra inputs are not permitted`) | A standalone gateway was started with a removed `openviking-gateway` flag, or its `gateway.conf` still has a field it does not accept, such as `enabled`. Move the settings into `gateway.conf` or environment variables; see step 2 of [Migration](../guides/22-gateway-operations.md#migration). Leftover fields in the `gateway` section of `ov.conf` only log `Ignoring removed gateway settings` and do not stop startup. |
| Management key too short (`management_key must resolve to at least 32 characters`) | The [gateway management key](../guides/22-gateway-operations.md#gateway-management-key) needs at least 32 characters. An unset environment variable referenced with `$VAR` causes the same error. |
| Encryption settings (`Gateway data is encrypted; configure the encryption settings it was written with`, or an encryption config error) | The server's `encryption` section (embedded) or the `encryption` block in `gateway.conf` (standalone) is missing, invalid, or its encryption root key cannot be loaded. See the [Encryption guide](../guides/08-encryption.md). |
| Incompatible storage (`Unsupported gateway schema`) | The storage directory was written by an incompatible gateway version (an older one, or a newer one if you downgraded). Use a fresh, empty `storage_path`. |
| Standalone without an OpenViking URL (`OpenViking Gateway needs an OpenViking server URL`) | Run `openviking-gateway init`, or set `OPENVIKING_GATEWAY_OPENVIKING_URL`. |
| Standalone config file not found (`Gateway config file not found`) | The file named by `--config` or `OPENVIKING_GATEWAY_CONFIG_FILE` does not exist. Fix the path, or create the file with `openviking-gateway init --config <path>`. |
| Standalone on a non-loopback address (`A publicly bound OpenViking Gateway requires a successful synchronous probe`) | The startup check must reach OpenViking and confirm `api_key` authentication, or the gateway exits. Start OpenViking first (in containers, wait until it is ready), or bind to `127.0.0.1`. |
| OpenViking in trusted mode (`OpenViking Gateway does not support trusted authentication`) | Standalone only: the startup check refuses OpenViking's trusted mode on any bind address. Switch OpenViking Server to `api_key` authentication (`server.auth_mode`); see [Startup checks](../guides/22-gateway-operations.md#startup-checks). |

## Studio shows a setup card {#studio-shows-a-setup-card}

Studio shows a setup card in place of the gateway tabs when it cannot load the gateway for you. The card title names the problem:

| Card | What to do |
| --- | --- |
| **The gateway is not enabled on this OpenViking server** (404) | Run `openviking-server init` → **Update gateway**, or set `gateway.enabled: true` in `ov.conf` and restart; see [Enable it in the server](../guides/22-gateway-operations.md#enable-it-in-the-server). On a server that uses API keys, root can choose **Enable gateway** on the card instead. Account admins and regular users see a card that only asks them to contact whoever deployed the server. |
| **Connect to OpenViking first**, or **Your OpenViking API key is invalid** (401) | Choose **Open connection settings** and enter your current OpenViking API key. |
| **The gateway does not support Trusted mode** (`trusted_mode_unsupported`) | Switch the server to `api_key` authentication. |
| **The gateway cannot reach OpenViking** (`openviking_unreachable`) | Standalone gateways only: check `openviking_url` (in `gateway.conf` or `OPENVIKING_GATEWAY_OPENVIKING_URL`) and the network. |
| **Couldn't load the OpenViking Gateway** | Any other failure. The card shows the gateway's message; choose **Retry**, and match the message against the sections on this page. |

Read-only **Upstreams** and **Profiles** tabs are not a setup card: the deployer set a [gateway management key](../guides/22-gateway-operations.md#gateway-management-key) (`management_key_required`), and an OpenViking root or admin API key does not unlock editing. Choose **Enter gateway management key**; see [Unlock editing](06-manage-in-studio.md#unlock-editing). After the key is changed on the server (`management_key_invalid`), Studio locks again, and if nobody has the key, see [Lost the management key](../guides/22-gateway-operations.md#lost-the-management-key).

## Issuing a key fails {#issuing-a-key-fails}

Before it issues a [gateway key](09-gateway-keys.md#issue-a-key), the gateway checks the bound OpenViking API key with OpenViking. When that check fails, the **Issue a gateway key** dialog shows why; the code in parentheses is the `reason` the management API returned.

| Message in Studio | Cause and fix |
| --- | --- |
| The server only stores a hash of this user's API key and cannot read it. Paste their user API key instead. (`user_key_unreadable`) | OpenViking Server stores only key hashes (`encryption.api_key_hashing.enabled`). Choose **Paste a user API key** and enter that user's own key. |
| This is a root API key. Use the user API key of a user in this account. (`root_key_not_allowed`) | A root API key cannot be bound to a gateway key. Use the API key of a user or account admin instead. Local dev mode's `default/default` is exempt. |
| A root API key has no user of its own. Choose the user this key is for. (`user_required`) | You are signed in to Studio as root and chose neither a user nor a pasted key. Choose the user, or paste their key. |
| This OpenViking API key belongs to another account. (`other_account`) | The key belongs to a different account from the one you are managing in Studio. |
| OpenViking rejected this key. (`openviking_http_401`, `openviking_http_403`) | The key is incomplete or was regenerated, or the user was deleted. Use the user's current key. |
| OpenViking couldn't tell which user this key belongs to (`openviking_identity_missing`) | OpenViking returned no user for this key. Use the key of a user in this account. |
| The gateway couldn't reach OpenViking to check this key. (`openviking_unavailable`, `openviking_timeout`) | The gateway cannot reach OpenViking; for a standalone gateway, check `openviking_url`. |
| OpenViking is older than the gateway requires. (`openviking_version_mismatch`) | Standalone gateways only: OpenViking Server is older than `min_server_version` (0.4.16). Upgrade it. A server installed from source without version tags may report a development version such as `0.1.dev123`; install a release, or set `min_server_version` to match. |
| You don't have permission to do this. (`forbidden`) | The pasted OpenViking API key belongs to a different user than the one selected in the dialog, for example an admin pasted their own key instead of the chosen user's. Paste the selected user's own key, or select the user the key belongs to. A regular user gets the same error when the pasted key is not their own. |
| This user is no longer in this account. Choose another user. (`unknown_user`) | The selected user was removed from the account while the dialog was open. `unknown_policy` and `unknown_upstream` mean the same for the selected context profile or upstream. Refresh and choose again. |

## Clients get an error from the gateway {#clients-get-an-error-from-the-gateway}

The gateway checks each client request before forwarding it: the gateway key, the model and a matching upstream. An error the gateway produces itself has a JSON body `{"reason": ..., "detail": ..., "request_id": ...}`; the full list of reasons is in [Model route errors](../api/25-gateway.md#model-route-errors). The request is logged with the same reason in `error_reason`, except that the default store sink cannot keep a request whose key was never confirmed (see [Requests](10-overview-and-requests.md#requests)). For an error the provider itself returned, see [The upstream returned an error](#the-upstream-returned-an-error).

| Status and reason | Cause and fix |
| --- | --- |
| 401 `gateway_key_invalid` | The client sent no gateway key, a revoked key or its provider API key, or the gateway deleted the key after the user's OpenViking API key was regenerated. Check which key the client actually sends, and [issue a new one](09-gateway-keys.md#issue-a-key) if needed. |
| 401 `openviking_key_invalid` | OpenViking rejected the OpenViking API key the client sent, for example because it was regenerated. Use the user's current OpenViking API key. |
| 402 `openviking_not_ordered`, 403 `openviking_forbidden` | OpenViking refused the OpenViking key (or the one behind the gateway key) when the gateway checked it, for example because the service is not ordered; the request is not forwarded. Restore the subscription or the key's permission. |
| 429 `openviking_rate_limited`, 503 `openviking_unavailable`, 503 `openviking_timeout`, `openviking_error` | The first request with an OpenViking key could not be confirmed: OpenViking rate limited the check, could not be reached, did not answer within 5 seconds, or answered with an error. Retry later and check OpenViking. A key the gateway already knows is not refused this way; its request goes on with `revocation_check_unavailable`. |
| 403 `oauth_credential_unsupported` | The client sent a Claude subscription token, for example Claude Code signed in with a subscription and no `ANTHROPIC_AUTH_TOKEN` set; or the upstream stores a subscription token. Use an API key. |
| 403 `model_not_allowed` | The model is not in the key's [allowed models](09-gateway-keys.md#allowed-models). Issue a key that allows it. |
| 400 `model_missing`, 415 `compressed_body_unsupported` | Only for keys that allow specific models: the request body must name a model and must not be content-encoded. |
| 404 `upstream_endpoint_mismatch` | **Append protocol endpoint path** is off on the key's upstream ([Send to an exact endpoint](07-upstreams.md#exact-endpoint)), so it accepts only that protocol's endpoint, and the client called another path, such as `/v1/models`. |
| 404 `no_matching_upstream` | No enabled upstream bound to the key uses the API the client called and serves the requested model. Check the upstreams' protocol, models and aliases, whether they are enabled, and which upstreams the key is bound to. |
| 400 `peer_invalid` | The request's peer is not a valid peer ID, for example it contains `/` or a space. See [Tell memory apart by peer](03-connect-clients.md#peer). |
| 400 `context_override_invalid`, 403 `context_override_not_allowed` | `X-OpenViking-Context` is not a valid profile override, or the key's context profile does not accept overrides. The `detail` says which. |
| 403 `upstream_url_not_allowed` | `X-OpenViking-Upstream-URL` does not equal the Base URL of any upstream bound to the key. See [Pick the upstream per request](07-upstreams.md#upstream-url-header). |
| 401 `upstream_key_missing` | The upstream expects each client to send its own key in `X-OpenViking-Upstream-Key`, or an upstream where the gateway holds the key has none stored. See [Credentials](07-upstreams.md#credentials). |
| 403 `coding_plan_disabled` | The upstream is marked as a [Coding Plan key](07-upstreams.md#coding-plan-keys). Use a model API key, or deliberately check **Allow this plan through the gateway**. |
| 403 `policy_unavailable` | The key's context profile no longer exists. Issue a new key with an existing context profile. |
| 404 `response_unknown`, 403 `response_upstream_disallowed` | The Responses response being looked up was not created with this key, or the upstream that created it is now turned off or no longer bound to the key. |
| 413 `body_too_large` | The request body is larger than `max_body_bytes` (see [the request size limit](05-long-conversations.md#side-effects)), or than your proxy's own limit. |
| 426 | A WebSocket connection to the Responses API. Expected: Codex falls back to HTTP. |
| 503 `gateway_overloaded` | The gateway process already serves `max_inflight_requests` model requests. Nothing was sent to the provider; retry after `Retry-After`, or add replicas. See [Scaling](../guides/22-gateway-operations.md#scaling). |
| 503 `store_unavailable` | The gateway's state store failed while it authenticated or routed the request. Nothing was sent to the provider; retry after `Retry-After`. See [Redis unavailable](#redis-unavailable). |
| 500 `internal_error` | The gateway failed unexpectedly. The gateway's log has the traceback; the request record carries the response's `request_id`. |
| 504 `upstream_timeout` | The provider did not start its reply within `upstream_timeout_seconds`, or a non-streamed reply stopped arriving for that long. Check the provider's status, or raise `upstream_timeout_seconds` for models that think long before they answer. |
| 502 `upstream_unreachable` | The gateway could not connect to the provider: its address does not resolve, nothing accepts the connection, or TLS fails. Check the upstream's Base URL and choose **Test** on the upstream ([Test and delete](07-upstreams.md#test-and-delete)). |
| 502 `upstream_disconnected` | The provider, or a proxy on the way to it, closed the connection before its reply was complete. Retry; if it persists, check the provider and any proxy in between. |

"Hidden tool request timed out" (504 `tool_loop_timeout`) and the other `tool_*` reasons happen after the provider call; see [Failure handling](04-openviking-tools.md#failure-handling).

A streamed reply that has started is not cut off for its length, only when the provider sends nothing for longer than `upstream_timeout_seconds`, drops the connection, or, for a request that uses OpenViking tools, when the tool **Total time** runs out. The HTTP status is already 200 then, so the gateway ends the stream with one error event of the API in use: an `error` whose type is the reason (such as `upstream_timeout`) in Chat Completions and Anthropic Messages, and `response.failed` in Responses; its message starts with the reason. A request that uses OpenViking tools gets the tool loop's error event instead, with "Hidden tool request timed out" when **Total time** ran out. When the provider stops in the middle of an event, or after the reply's last event, no event can be added and the connection is cut. The request record keeps the reason in `stream_error`. An error event that the provider itself sends inside a stream is relayed unchanged, and the record notes it as `stream_error: upstream_event` with the provider's error code.

## The upstream returned an error {#the-upstream-returned-an-error}

An upstream error is one the provider itself returned. Its row on the **Requests** tab has an HTTP status of 400 or above and no gateway issue, and the details read **The upstream answered with HTTP …, and the client received this error.** The gateway passed the provider's error to the client unchanged.

Common causes are a wrong or expired API key, a model name the provider does not know, a rate limit or quota, a conversation longer than the model's context window, or a provider outage. Choose **Test** on the [upstream](07-upstreams.md#test-and-delete) and compare with the error the client received. For context-length errors, see [Long conversations hit the context limit](#long-conversations-hit-the-context-limit).

## OpenViking is degraded {#openviking-is-degraded}

The **OpenViking** card on the Overview tab shows the gateway's latest OpenViking health check. **Connected** is healthy; the other states are:

- **Starting**: the gateway has not finished its first check yet. An embedded gateway checks every second until OpenViking is up. Refresh in a minute.
- **Degraded**: the last check failed. The card names the reason:

| Reason | What to do |
| --- | --- |
| OpenViking is unreachable or didn't answer in time (`openviking_unavailable`) | OpenViking is not running or cannot be reached. For a standalone gateway, check `openviking_url` and the network. |
| OpenViking didn't answer in time (`openviking_timeout`) | OpenViking accepted the connection but did not answer before the timeout. Check its load and its server log. |
| OpenViking returned HTTP … (`openviking_http_<status>`) or OpenViking reported error … (`openviking_<CODE>`) | OpenViking answered with an error. Check the OpenViking server log. |
| `trusted_mode_unsupported` | OpenViking runs in trusted mode. Switch it to `api_key` authentication. |

While OpenViking is degraded, requests still reach the model without memory, and the turns that end meanwhile fail to save; the next turn that ends after OpenViking is back writes them. They skip recall, the profile and the key check of a key the gateway already knows at once, with the card's reason, instead of waiting for OpenViking. The gateway checks again at growing intervals, up to `health_interval_seconds` (60 by default).

The health check is coarse: between two checks, every request still waits for each OpenViking call's own timeout (5 seconds for the key check, the profile's **Time limit** for recall). Two settings bound that wait when OpenViking hangs rather than refusing connections:

- `openviking_budget_seconds` gives each request one deadline, counted from its arrival, for the key check, recall, the profile and the tool list. When it runs out, the request goes on without what is left, flagged as below. A key the gateway has not seen yet cannot be confirmed without OpenViking, so its first request gets 503 `openviking_timeout` once the budget runs out.
- `openviking_breaker_failures` stops calling OpenViking for an account after that many consecutive calls in a row went unanswered (timeouts, connection errors, HTTP 5xx or 429). For `openviking_breaker_seconds` (30 by default), its requests skip the key check, recall, profile and tools at once and go to the model without memory, with the reason `openviking_circuit_open`; then one trial call decides whether calls resume. Refusals (401, 402, 403) and not-found answers never count. Saving a finished turn counts like any other call, and while the circuit is open it fails with `openviking_circuit_open`. The [status line](../guides/22-gateway-operations.md#status-line) reports open circuits as `openviking.open_circuits`.

Both are off by default; see [Configuration](13-configuration.md#gateway-fields).

## No memory is added {#no-memory-is-added}

[Recall](08-context-profiles.md#recall-memory) searches OpenViking on each new user message and adds what it finds; when it adds nothing, the request log says why. First find the request on the **Requests** tab. If it is not there, the client did not go through the gateway, or the gateway rejected the request (see [Clients get an error from the gateway](#clients-get-an-error-from-the-gateway)). If it is there, expand it and read **Recall** in the details:

| Recall result | Meaning | What to do |
| --- | --- | --- |
| **Nothing relevant found** (`empty`) | Normal for a new user. Memories exist only after a conversation is committed and OpenViking has extracted them. | Keep using it; see [Conversations do not appear in OpenViking](#conversations-do-not-appear-in-openviking) if memories never appear. |
| **Recall is off or the budget is used up** (`disabled`) | The context profile has recall off, the context window has less than 64 tokens of recall budget left, or the message is shorter than 3 characters. | Turn recall on in the context profile if it is off. |
| **Too little budget left to add memory** (`budget`) | After the memory wrapper, this message's recall budget left fewer than 64 tokens for results, so no search ran. The conversation has nearly used up its **Budget per context window** (it starts over in the next context window, for example after compaction), or **Budget per message** is set close to its 64-token minimum. | Raise **Budget per message** if it is near the minimum. |
| **OpenViking is unreachable or didn't answer in time** (`openviking_unavailable`), **OpenViking didn't answer in time** (`openviking_timeout`), **Recall timed out** (`recall_timeout`) | The search did not finish. | Check the OpenViking card (see [OpenViking is degraded](#openviking-is-degraded)). If OpenViking is running but slow, raise the **Time limit**. |
| **The time allowed for OpenViking calls ran out** (`openviking_budget_exhausted`) | Earlier OpenViking calls of this request used up `openviking_budget_seconds`, so the search did not run or was cut short. | Check why OpenViking is slow; raise `openviking_budget_seconds` if it is set too tight. |
| **OpenViking stopped answering for this account** (`openviking_circuit_open`) | Recent calls for this account went unanswered, so the gateway skipped OpenViking for a while. | Check the OpenViking card and server; calls resume on their own once a trial call succeeds. |
| **OpenViking rejected the key** (`openviking_http_401`) | The user's OpenViking key is no longer valid. | Issue a new gateway key with the current key; see [When the user's OpenViking API key changes](09-gateway-keys.md#when-the-openviking-key-changes). |
| **OpenViking returned HTTP …** (`openviking_http_<status>`) or **OpenViking reported error …** (`openviking_<CODE>`) | The search failed inside OpenViking. An OpenViking Server older than `min_server_version` can also fail this way. | Check the OpenViking server log. |
| **OpenViking plugin in use** (`plugin_present`) | The gateway detected an OpenViking plugin and stepped aside for this conversation. | Expected; see [When both are in use](../guides/15-gateway.md#when-both-are-in-use). |
| The **Type** is not **New message** | Tool steps, housekeeping and sub-agent requests never search; they replay the memory earlier messages received. | Nothing to do; see [Request types](10-overview-and-requests.md#request-types). |

A message whose search failed never gets memory later; the next message searches again.

## Conversations do not appear in OpenViking {#conversations-do-not-appear-in-openviking}

[Saving](08-context-profiles.md#save-conversations) writes each finished turn to an OpenViking session named `gateway-…`, owned by the OpenViking user behind the gateway key, as soon as its reply ends. When a turn you expect is missing, match what you see:

| What you see | Why | What to do |
| --- | --- | --- |
| The latest turn is missing | The turn has not ended: its reply handed the turn to the client's tools, so the turn goes on with the client's next request, or the reply did not finish, for example because the stream failed or the client went away. | A turn is saved when it ends; a reply that did not finish is written with the history when the next turn ends. |
| The **Saving** column shows **Off** | The context profile does not save conversations. | Turn saving on in the context profile; it reaches only new conversations ([When changes take effect](06-manage-in-studio.md#when-changes-take-effect)). |
| The **Saving** column shows **Failed** (`failed`) | Writing the turn to OpenViking failed or took longer than 30 seconds; the expanded row shows the reason ([Saving status](10-overview-and-requests.md#saving-status)). Nothing retries it. | Fix the cause, for example make OpenViking reachable again. With `openviking_http_402` or `openviking_http_429`, check the OpenViking account's order or rate limit. With `openviking_http_401`, issue a new gateway key with the user's current OpenViking key ([When the user's OpenViking API key changes](09-gateway-keys.md#when-the-openviking-key-changes)). With `save_timeout` or `openviking_timeout`, check OpenViking's load. Then the conversation's next turn that ends writes what is missing, as long as the client resends the history unchanged; without a next turn, the failed turn is not saved. |
| Every turn fails with `openviking_http_400` | After a `capture_memory_types` change, it names a memory type that OpenViking does not know or has disabled, so OpenViking refuses to create the session. | Correct the names in the profile and start a new conversation; a running conversation keeps its settings ([Save conversations](13-configuration.md#profile-saving)). |
| **OpenViking plugin in use** (`plugin_present`) | The gateway saves nothing for a conversation in which it detected an OpenViking plugin. | Expected; use either the plugin or the gateway for a given client. |
| The same user message appears twice, or a reply has two versions | The client regenerated or edited a reply, or compacted its history, and saving went on in the same session from the turn's latest user message; the version written before is kept. A save whose response was lost is also written again by the next turn. | Expected; see [When conversations are saved](01-how-it-works.md#when-conversations-are-saved). |
| Only part of the conversation is in the session | After **Resync conversation…** (`manual_reset`), or after the gateway lost the conversation's saving state, the next finished turn writes the whole history to a new `gateway-…` session; earlier turns stay in the older session. A conversation may also have moved to a new session under an earlier gateway version (for example with the reason `history_changed`). | Look in the older session too; see [Saving status](10-overview-and-requests.md#saving-status). |
| No `gateway-…` sessions at all | You are looking as a different OpenViking user from the one behind the gateway key. | Sign in as that user. |
| Sessions appear, but no memories come from them | OpenViking extracts memories only after the session is committed, and then in the background. By default the gateway commits: at once when a turn ends with **Commit after** or more uncommitted tokens; otherwise **Commit when idle for** (600 seconds by default) after the last finished turn, a minute or two late when the gateway restarted in between. When no commit comes, the commit may keep failing (a `could not commit` warning with the reason in the gateway log; the gateway tries again every minute, and drops a commit that OpenViking refuses with 401, 403 or 404), or the key used to save the conversation was revoked or expired before the idle commit was made, so the commit finds no stored key record and is dropped. With `openviking_auto_commit: true`, OpenViking commits; the idle commit runs only when the server has `memory.session_auto_commit.enabled: true`, never sooner than 300 seconds and, with the server's `local` idle backend, up to `check_interval_seconds` (600 by default) late. | Wait for the commit and extraction to finish. When the commit fails, fix the cause shown in the log; when the key was revoked or expired, the conversation's next finished turn starts the idle commit again. With `openviking_auto_commit: true`, set `memory.session_auto_commit.enabled: true` in the server's `ov.conf` and restart it. See [Save conversations](08-context-profiles.md#save-conversations). |

## The first-call cache hit rate dropped {#the-first-call-cache-hit-rate-dropped}

The **First-call cache hit rate** on the Overview tab is the share of input tokens the provider served from its [prompt cache](01-how-it-works.md#replay) on the first model call of each new user message. Healthy is close to what the provider reaches without the gateway, so compare with that first. Then look at the issues flagged on recent requests and at the Overview notes:

| What you see | Cause | What to do |
| --- | --- | --- |
| **Memory record missing** (`missing_injection_record`) | The gateway's storage was lost or restored from an old backup, or the record expired, so memory added earlier could not be replayed. | Nothing; ongoing conversations recover after one miss. |
| **Upstream switched** (`upstream_changed`) | The conversation's upstream was turned off, is not bound to the key the client now uses, does not match the request's `X-OpenViking-Upstream-URL`, or no longer serves the model, so the conversation moved to another upstream. | Turn the upstream back on or give the client a key that includes it; see [Routing](07-upstreams.md#routing). |
| **Cache parameters changed** (`ark_cache_parameters_changed`) | On Ark or ModelArk, the client changed the model, thinking mode, sampling parameters, system prompt or tools within a conversation. | Keep them the same within a conversation. |
| No issue flagged | The client itself may change earlier messages or its system prompt every turn, for example by inserting the current time, which the gateway cannot fix. Each compaction, and each new context window the model starts, also costs one miss; that is expected. | Check the client's system prompt for content that changes every request. |
| **… long conversations keep missing the prompt cache**, or a hit rate that stays at 0%, and the provider's or relay's cache records show hits | The upstream isn't passing cache usage back to the gateway, for example a relay that drops the cache fields when it converts protocols. The gateway reads the common fields, such as `cache_read_input_tokens`, `prompt_tokens_details.cached_tokens`, `input_tokens_details.cached_tokens` and `prompt_cache_hit_tokens`. | Make the relay pass cache usage through. |
| The same, or **… hasn't reported any cache hits lately**, and the provider's records show no hits either | Caching is not happening at all. | Check that the provider supports prompt caching, that an Anthropic client sets `cache_control`, and that the relay isn't rotating between accounts or channels. |

## Long conversations hit the context limit {#long-conversations-hit-the-context-limit}

[Compaction](05-long-conversations.md#long-conversations) replaces the earlier history of a long conversation with a summary before it fills the model's context window. When the provider still rejects a long conversation as too long, it is usually one of three causes:

| Cause | Fix |
| --- | --- |
| The gateway does not know the model's window: the upstream's **Context windows** has no entry for the model and the profile has no **Default context window**, so the gateway falls back to its 1,000,000-token default and compacts too late for a smaller model. | Add the model's window; see [Set the model's context window](05-long-conversations.md#context-window). |
| The conversation's context profile has compaction off. | Turn it on; profile changes reach only new conversations ([When changes take effect](06-manage-in-studio.md#when-changes-take-effect)). |
| Compaction failed. The request details show **Compaction failed** with one of the reasons below. | See the reason table. |

| Reason | Cause and fix |
| --- | --- |
| `summary_incomplete` | The summary reply did not finish normally, usually because it hit the length limit. Raise **Summary length**. |
| `summary_http_<status>` | The summary request was rejected, by the upstream or by the gateway before sending (no upstream key, or a blocked Coding Plan). Check the upstream's key, the model and the Coding Plan setting. A 400 has already been retried once without the extra output headroom. |
| `summary_unavailable` | The upstream could not be reached, timed out (`upstream_timeout_seconds`) or returned something other than JSON. |
| `summary_tool_call`, `summary_empty` | The model called a tool, or returned no text, instead of writing a summary. |
| `cut_not_found` | The client's latest messages did not line up with the stored history, so the gateway could not choose a cut point. Usually transient. |
| `summary_failed` | An unexpected internal error. Check the gateway log for "OpenViking Gateway summary failed". |
| `still_over_threshold` | The summary was written and kept, but usage is still at or above **Compact at**: the system prompt, tools and summary alone fill most of the window. Lower **Summary length**, or check the model's context window setting. |

For every reason except `still_over_threshold`, the request was sent with the full history. For every reason, the gateway waits 60 seconds before trying to compact that conversation again.

## File imports or downloads fail {#file-imports-or-downloads-fail}

[Importing files](04-openviking-tools.md#import-files-and-skills) gives the client upload and download links on the gateway's [public address](../guides/22-gateway-operations.md#public-address). Match the error you see:

| Error | Fix |
| --- | --- |
| "Set gateway.public_url for client uploads and downloads" | Set `public_url` to an address the client can reach, or set `mcp_file_links` to `direct`. |
| The upload or download link is unreachable or returns 404 | Make sure `public_url` is not an old `:1935` address, does not already end in `/gateway`, and that your proxy forwards `/gateway/uploads` and `/gateway/downloads` like the other paths. With `mcp_file_links` set to `direct`, check the server's `OPENVIKING_PUBLIC_BASE_URL` instead. |
| 413 "Upload exceeds configured body limit" | The file is larger than `max_body_bytes` or the proxy's limit. |
| 502 "OpenViking upload is unavailable" or "OpenViking download is unavailable" | The gateway cannot reach OpenViking Server. |
| The model has no import tool | The OpenViking service does not provide it, the profile has it unchecked, or this request cannot use gateway tools. Check the tool list and the request details. |

## Issues on degraded requests {#issues-on-degraded-requests}

A degraded request is one the gateway could not fully handle; the Requests and Overview tabs flag it. Studio shows the issue label; the code in parentheses is the matching value in the management API:

| Issue | What happened | What to do |
| --- | --- | --- |
| Memory unavailable (`memory_store_failure`) | The gateway could not read or write its storage, or the user's gateway data was just deleted. The request went to the model without memory; in Claude conversations, earlier thinking was removed. | Check disk space, permissions and the gateway log. Expected right after [deleting a user's gateway data](09-gateway-keys.md#delete-a-users-gateway-data). |
| Preparation timed out (`prepare_timeout`) | Adding memory took longer than `prepare_timeout_seconds`, for example because OpenViking or the state store answered slowly. The request went to the model without memory; in Claude conversations, earlier thinking was removed. | Check OpenViking and the state store. Studio shows this issue by its code. |
| Forwarded unchanged (`unsafe_json`) | The request had numbers that cannot be re-encoded exactly, or duplicate keys, so the gateway forwarded it byte for byte without memory. | Usually specific to one client; nothing to change in the gateway. |
| Upstream switched (`upstream_changed`) | The conversation's upstream could no longer serve it, so it moved to another one, with one cache miss and earlier Claude thinking removed. | Turn the upstream back on, or give the client a key that includes it; or accept the switch. |
| Memory record missing (`missing_injection_record`) | In a Claude conversation, memory added earlier is no longer on record, so earlier thinking was removed once. | Expected after storage loss or in very old conversations. If it keeps happening, start a new conversation. |
| OpenViking plugin in use (`plugin_present`) | An OpenViking plugin was detected, and this conversation no longer uses gateway memory. | Expected when a plugin is in use. Use either the plugin or the gateway for a given client. |
| Cache parameters changed (`ark_cache_parameters_changed`) | On Ark or ModelArk, parameters that affect caching differ from the conversation's first request, so the prompt cache probably missed. | Keep the model, thinking mode, sampling parameters, system prompt and tools the same within a conversation. |
| Tool history unavailable (`hidden_tool_history_unavailable`) | A Claude conversation used OpenViking tools, but this request cannot use them, so earlier thinking was removed. | Check the tool skip reason in the request details, restore the original settings, or start a new conversation. |
| OpenViking tools failed (`hidden_tool_loop_failed`) | The model could not finish its answer while using OpenViking tools, and the client received an error. | See [Failure handling](04-openviking-tools.md#failure-handling). |
| No usable model reply (`hidden_reply_without_anchor`) | The model used OpenViking tools during this request but returned no content to continue the conversation from. The client still got the reply, but the next request cannot reuse those tool results. | Retry the question or start a new conversation. |
| Reply not saved (`capture_parse_failure`) | A streamed reply could not be parsed, so this request did not save its turn. The client was not affected. | Nothing to do. When the client sends the reply back in the next turn, it is saved with that turn. |
| Key not verified (`revocation_check_unavailable`) | OpenViking could not confirm the gateway key, so the request was served without checking whether the key was revoked. | Check that OpenViking is reachable and healthy. |

## Redis unavailable {#redis-unavailable}

If startup fails at Redis PING, check `storage.redis.endpoints`, TLS trust, and the password environment variable. The gateway requires `openviking[expiry]` outside the official image. While the state store (Redis or SQLite) fails at runtime, requests that need it to authenticate or route get 503 `store_unavailable` with `Retry-After: 1` and `X-OpenViking-Upstream-Attempted: 0`, and requests past that point go to the model without memory (`memory_store_failure`); inspect stderr. A key that OpenViking rejects still gets its 401, 402 or 403. Each Redis command, including the wait for a free connection, has at most `connect_timeout_ms + command_timeout_ms`, so a Redis that stopped answering fails requests within that time instead of queueing them. `/health` stays healthy during a runtime Redis failure and is not a Redis connectivity check. See [Storage providers](13-configuration.md#storage) for the ACL and timeout settings, and [Redis memory policy](../guides/22-gateway-operations.md#redis-memory-policy) for persistence.

## Next steps {#next-steps}

- [Gateway overview and request log](10-overview-and-requests.md#requests): read every column, label and saving state in the request log.
- [Monitoring](../guides/22-gateway-operations.md#monitoring): check liveness with `/health` and read the gateway's state through the management API before users notice a problem.
- [Configuration reference](13-configuration.md#gateway-fields): look up `max_body_bytes`, `public_url`, `min_server_version` and the other fields named on this page.
