# OpenViking Gateway Management API

The management API lets you check permissions, configure upstreams and context profiles, issue gateway keys, and read usage and logs. All paths start with `/gateway/admin/` and are served by the gateway itself: when the gateway runs inside OpenViking Server, use the server's port (1933 by default); when it runs standalone, use the gateway's own port (1935 by default). The examples set `SERVER_URL` to that address without any path, such as `http://127.0.0.1:1933`.

Model clients do not call these endpoints. They use a gateway key on `/gateway/v1/*`, `/gateway/api/v3/*` or `/gateway/api/compatible/v1/*`; see [OpenViking Gateway](../gateway/03-connect-clients.md#connect-clients).

## Credentials and account scope {#credentials-and-account-scope}

Every management request carries an OpenViking API key in `X-API-Key` or `Authorization: Bearer`. The gateway asks OpenViking which account, user and role the key belongs to, and caches the answer for up to 30 seconds. Local dev mode needs no key; the caller is always the root identity `default/default`. The gateway does not support trusted authentication.

Upstreams and context profiles (called `policies` in the API) belong to an account and are shared by its users. A request acts on the caller's account by default. Root can name another account with the `account_id` query parameter; admins and users can only reach their own account.

The deployer can also set a gateway management key (see [Gateway management key](../guides/22-gateway-operations.md#gateway-management-key)). Once it is set, requests that change upstreams or context profiles must carry it in `X-OpenViking-Gateway-Management-Key`, including requests from OpenViking admins and root. A request that carries a wrong management key gets 401 `management_key_invalid` right away; it is not quietly treated as read-only.

| Operation | No management key set | Management key set |
| --- | --- | --- |
| Read upstreams and context profiles | Any user in the account | Any user in the account; the management key alone also works, for any account |
| Create, change or delete upstreams and context profiles; test upstreams | Any user in the account | Only requests with the correct management key |
| Import OpenViking's model settings | Root in an embedded deployment (including local dev) | Only requests with the correct management key, and only in an embedded deployment |
| Issue, read and revoke gateway keys; reset saving | Users: their own keys; admins: their account; root: the selected account | Same; the management key adds no rights |
| Read logs and the overview | Users: their own records; admins and root: the whole account | Same; always needs an OpenViking identity |
| Delete a user's gateway data | An admin of that account, or root | Same |
| Delete an account's gateway data | Root | Same |

The management key works on its own, without an OpenViking API key, which suits automation scripts. With it alone you can read and write upstreams and context profiles and call `/access`, choosing the account with `account_id` (`default` when omitted). Issuing keys and reading logs and the overview always need an OpenViking identity, and sending both credentials does not give those operations any extra rights. Setting, changing or removing the management key does not affect gateway keys already issued.

Read-only views of upstreams leave out the API key, header values and header names; they keep only `has_api_key` and `auth_mode`.

## Access and discovery {#access-and-discovery}

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/gateway/admin/access` | The caller's permissions: `managed`, `can_write_objects`, `can_import_vlm`, `can_issue_for_others`, `role`, `account_id`, `user_id`, `warnings` |
| GET | `/gateway/admin/overview` | A summary of the latest 10,000 log records the caller can see: usage, cache hits, recall, saving problems, OpenViking status and log retention |
| GET | `/gateway/admin/logs` | A JSON array of the newest log records the caller can see, newest first; `limit` defaults to 200, range 1–1000. See [Log records](#log-records). |
| GET | `/gateway/admin/guides` | The client address `base_url`, plus `public_url_configured`; `base_url` is `public_url` followed by `/gateway`, or the request's origin followed by `/gateway` when `public_url` is empty |
| GET | `/gateway/admin/tools` | The OpenViking tools available to the caller, with names, descriptions and annotations, read with the caller's own OpenViking key (root: with the OpenViking key of a gateway key issued in this account, or the last catalog read, since OpenViking refuses root on `/mcp`) |

Studio decides which buttons to show from `/access` alone, and your own management tools should do the same. When a standalone gateway connects to the public OpenViking service without a management key, `warnings` includes `public_openviking_without_management_key`. It is only a warning and does not restrict anything.

```bash
curl "$SERVER_URL/gateway/admin/access" \
  -H "X-API-Key: $OPENVIKING_API_KEY"
```

```json
{
  "managed": true,
  "can_write_objects": false,
  "can_import_vlm": false,
  "can_issue_for_others": true,
  "role": "admin",
  "account_id": "team",
  "user_id": "alice",
  "warnings": []
}
```

If OpenViking rejects the API key, the response is 401 `openviking_http_401`.

### Log records {#log-records}

The log holds metadata only, never message content, keys or error messages. Every request to a model route (except `.../models`) writes exactly one record, whichever way it ends: answered, refused by the gateway, failed at the provider, cut off mid-stream, or abandoned by the client. A request that saves its turn writes the record after the save. With the default `request_log_sink: store`, a request that failed before its key was confirmed has no account and is not stored; the `stdout` and `file` sinks write it with `account` and `user_id` set to `null`. Fields are left out when they do not apply.

| Field | Meaning |
| --- | --- |
| `time` | When the record was written, in epoch seconds |
| `event`, `schema` | `request` and `2` for a request record |
| `request_id` | The gateway's ID for the request; error bodies on model routes carry it too |
| `received_at`, `method`, `endpoint` | When the gateway received the request (epoch seconds), its HTTP method, and `messages`, `count_tokens`, `chat`, `responses`, `responses_item` or `other` |
| `kind` | `user`, `continuation`, `auxiliary`, `subagent`, `count` or `passthrough`; see [Requests](../gateway/10-overview-and-requests.md#requests) |
| `model`, `stream` | The model name the client sent, and whether it asked for a stream |
| `status` | The HTTP status the client received: the provider's when the gateway relayed it, the gateway's own for a gateway error, `499` when the client left before any response |
| `upstream_attempted`, `upstream_status` | Whether the gateway sent a request to the provider (a compaction summary call included), and the provider's HTTP status when it answered |
| `error_reason`, `error_origin`, `error_class` | For any failure, a stream that failed after `200` included: the [reason](#model-route-errors), where it came from (`gateway`, `openviking`, `upstream` or `client`), and a coarse class (`api_key_invalid`, `quota_or_order`, `openviking_unavailable`, `config_error`, `gateway_internal`, `timeout`, `model_error` or `client_cancelled`). A provider error the gateway relayed is `upstream_error` |
| `upstream_error_code` | The `code` (else `type`) of the provider's error body, or of an error event inside its stream, when it is a short identifier; never its message |
| `stream_error`, `client_disconnected` | Why a response body that had started did not end normally (`upstream_timeout`, `upstream_disconnected`, `upstream_event` for an error event the provider sent, `client_disconnected`, a `tool_*` reason or `internal_error`), and `true` when the client went away |
| `upstream_id`, `credential_id`, `openviking_account` | The upstream that served the request, the gateway key that sent it, and the OpenViking account behind that key |
| `session`, `protocol` | The conversation and its protocol; use them to [reset saving](#gateway-keys) |
| `peer` | The request's [peer](../gateway/03-connect-clients.md#peer), from `X-OpenViking-Actor-Peer` or the key's default |
| `degradation` | Issue code; see [Issues on degraded requests](../gateway/12-troubleshooting.md#issues-on-degraded-requests) |
| `degradations` | Every issue of the request, in order. It can also hold `recall_failed`: the memory search got no answer from OpenViking (`recall_reason` says why), which never becomes `degradation`; and `prepare_timeout`: adding memory took longer than `prepare_timeout_seconds` |
| `revocation_reason` | Why OpenViking could not confirm the key, with `revocation_check_unavailable` |
| `session_source` | How the conversation was found: `header` (`X-OpenViking-Session`), `reply_match` (the history continues one conversation's reply) or `new` |
| `recall_reason`, `recall_count`, `recall_ms` | Memory search result code, entries added and search time; see [No memory is added](../gateway/12-troubleshooting.md#no-memory-is-added) |
| `replay_hits` | Earlier messages whose memory was replayed unchanged |
| `capture_status`, `capture_reason` | Saving status: `active`, `disabled`, or `failed` when this request ended a turn and writing it to OpenViking failed. The reason is the failure's (an OpenViking reason such as `openviking_http_503`, `openviking_unavailable` or `openviking_circuit_open`, or `save_timeout` or `save_failed`), or else the reason stored with the conversation, such as `manual_reset`; see [Saving status](../gateway/10-overview-and-requests.md#saving-status) |
| `capture_messages`, `openviking_session` | On a request that saved its turn: how many messages it wrote, and the OpenViking session it wrote them to |
| `tool_skip_reason` | Why the request got no OpenViking tools; see [When tools are not offered](../gateway/04-openviking-tools.md#when-tools-are-not-offered) |
| `tool_stop_reason` | `token_budget` when the tool token budget refused further calls; see [Limits](../gateway/04-openviking-tools.md#limits) |
| `compaction_failed` | Why compaction failed (with `still_over_threshold` the summary was kept); see [Long conversations hit the context limit](../gateway/12-troubleshooting.md#long-conversations-hit-the-context-limit) |
| `context_tokens`, `context_window` | Estimated context size, and the window it was compared with |
| `input_tokens`, `output_tokens`, `cached_tokens`, `cache_write_tokens` | Token usage summed over every provider call of the request, OpenViking tool rounds included. `input_tokens` includes cached and cache-write tokens. |
| `duration_ms` | Time from sending the request to the provider until the reply finished |
| `read_ms`, `auth_ms`, `route_ms`, `prepare_ms` | Time spent reading the body, checking the key (with OpenViking), choosing the upstream, and preparing memory (recall and compaction included) |
| `added_ms`, `upstream_ttfb_ms`, `first_chunk_ms`, `total_ms` | Time from receipt until the provider request started, from that start until the provider's response headers, from receipt until the first streamed chunk reached the client, and from receipt until the record was written |
| `compaction_ms` | Time spent on compaction, also when it failed |
| `bookkeeping_failed` | Steps that failed after the reply was sent: `completed` (recording the reply for later requests) or `responses` (remembering a Responses ID). A failed save is reported in `capture_status` instead |

Fields that start with `first_upstream_` and `hidden_upstream_` split the token usage between the first provider call and the OpenViking tool rounds; the first-call cache hit rate uses the first group.

`/overview` aggregates these records. Besides usage and cache figures, it returns `degradations` (issue code → count), `capture_issues` (`{"failed": n}`: conversations whose latest record says a finished turn was not saved), `cache_alerts`, `openviking` (OpenViking's `status`, plus `reason` when degraded) and `log_retention_days`.

### Status line {#status-line}

With `snapshot_interval_seconds` above zero, each gateway process writes one JSON line at that interval; see [Status line](../guides/22-gateway-operations.md#status-line) for where it goes. With the `store` sink the line is logged at INFO by the `openviking_gateway.snapshot` logger, and goes to stderr when the host has configured no log handler. Counts cover the interval since the previous line of the same process. It never carries an account, user, session, model or request ID.

```json
{"event": "snapshot", "schema": 1, "time": 1794787200.0, "interval_s": 60.0,
 "uptime_s": 8640.2, "version": "0.5.6",
 "inflight_requests": 12, "inflight_streams": 9,
 "requests": 480, "errors": {"upstream_timeout": 2, "store_unavailable": 1},
 "degradations": {"recall_failed": 4},
 "stream_errors": {"client_disconnected": 3},
 "rejected_overload": 0,
 "records_unlogged": 0, "records_failed": 0, "records_dropped": 0,
 "store_errors": 1,
 "openviking": {"state": "ok", "reason": "", "open_circuits": 0},
 "capture": {"delivered": 18, "failed": 0}}
```

| Field | Meaning |
| --- | --- |
| `time`, `interval_s`, `uptime_s`, `version` | When the line was written (epoch seconds), seconds since the previous line, seconds since the process started, and the installed OpenViking version |
| `inflight_requests`, `inflight_streams` | Model requests between receipt and the end of their response, and those of them whose response is streaming, when the line was written |
| `requests` | Request records written in the interval |
| `errors`, `degradations`, `stream_errors` | Those records counted by `error_reason`, by each entry of `degradations`, and by `stream_error` |
| `rejected_overload` | Requests refused with `gateway_overloaded` because the process was at `max_inflight_requests` |
| `records_unlogged` | Records the `store` sink did not keep because the request had no account |
| `records_failed`, `records_dropped` | Records the sink failed to write, and records the `file` sink dropped because its queue was full |
| `store_errors` | Requests that failed or lost their memory on a state store error (`store_unavailable`, or the issue `memory_store_failure`) |
| `openviking.state`, `openviking.reason` | The gateway's last OpenViking status check: `ok`, `degraded` or `starting`, and the reason when degraded |
| `openviking.open_circuits` | OpenViking accounts whose calls are skipped right now because they stopped answering; see [OpenViking is degraded](../gateway/12-troubleshooting.md#openviking-is-degraded) |
| `capture.delivered` | Finished turns this process wrote to OpenViking in the interval |
| `capture.failed` | Finished turns whose write failed in this process in the interval. Both counts are per process and can be summed across processes |

## Upstreams and context profiles {#upstreams-and-context-profiles}

| Method | Path | Description |
| --- | --- | --- |
| GET | `/gateway/admin/upstreams` | List upstreams |
| GET | `/gateway/admin/upstreams/{identifier}` | Read one upstream |
| PUT | `/gateway/admin/upstreams/{identifier}` | Create or replace an upstream |
| DELETE | `/gateway/admin/upstreams/{identifier}` | Delete an upstream no key uses |
| POST | `/gateway/admin/upstreams/{identifier}/test` | Test the upstream connection or a model |
| POST | `/gateway/admin/upstreams/import-vlm` | Import OpenViking's model settings |
| GET | `/gateway/admin/policies` | List context profiles |
| GET | `/gateway/admin/policies/{identifier}` | Read one context profile |
| PUT | `/gateway/admin/policies/{identifier}` | Create or replace a context profile |
| DELETE | `/gateway/admin/policies/{identifier}` | Delete a context profile no key uses |

The caller chooses the IDs. `PUT` takes the complete object, and unknown fields fail validation. The meaning, default and range of every field are in the [Configuration reference](../gateway/13-configuration.md#configuration-reference).

An upstream's `api_key` and `headers` are write-only:

- While `base_url` stays the same, a blank or missing `api_key` keeps the stored key, and a missing `headers` keeps all stored headers. A `headers` object replaces the whole set; an entry with an empty value keeps the value stored under that name.
- **Changing `base_url` clears the stored API key and all header values.** Send the credentials the new address needs in the same request, so a stored key is never sent to a different address.
- The editable view returns `has_api_key` and `header_names`, never the values.

```bash
curl -X PUT "$SERVER_URL/gateway/admin/upstreams/openai?account_id=team" \
  -H "X-OpenViking-Gateway-Management-Key: $GATEWAY_MANAGEMENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"OpenAI","protocol":"chat","base_url":"https://api.openai.com/v1","api_key":"<provider-key>"}'
```

Deleting an object that gateway keys still use returns 409 `in_use` with `key_count`. Revoke those keys first. If you only want requests to stop reaching an upstream right away, turn it off instead of deleting it.

### Upstream tests {#upstream-tests}

`POST /gateway/admin/upstreams/{identifier}/test` supports two modes. An empty `{}` body checks the connection with `GET /v1/models`. A `{"model":"<model-name>"}` body sends one small, non-streaming generation request with the stored credentials and headers. The model name is resolved through the upstream's aliases. Model requests use the configured Chat Completions, Responses or Anthropic Messages endpoint, including an exact endpoint URL when path appending is off. When gateway tools are allowed, they include the account's OpenViking tools filtered by the default context profile; a temporary tool-catalog failure falls back to a request without tools. The model test does not set a reasoning effort, so the model uses its default.

The response includes `{ok, kind, status, reason, url}`, where `kind` is `connection` or `model`. Failed tests also include the upstream HTTP response in `response`, with `http_version`, `status`, `reason`, `headers`, `body` and an optional `truncated` flag. The body is limited to 16 KiB; `status` is left out when no response arrived. The timeout is the smaller of the configured upstream timeout and 60 seconds. Running either test requires write access; model tests make a real provider request and may incur a small charge.

Anthropic tests send the key in both `x-api-key` and `Authorization: Bearer` and default `anthropic-version` to `2023-06-01` when the upstream headers do not set it. Chat Completions and Responses tests send only Bearer authentication. A `key_header` other than `authorization`, or `{api_key}` in `base_url`, changes this as described in [Credentials](../gateway/07-upstreams.md#credentials); `url` keeps the `{api_key}` placeholder.

The two tests read the provider's response differently:

| Test | Provider response | `reason` | `ok` |
| --- | --- | --- | --- |
| Connection | The upstream is an exact endpoint (`append_endpoint_path` is `false`), which has no model list. No request is sent; run a model test instead. | `exact_endpoint_untestable` | `false` |
| Model | The Coding Plan upstream has not been approved for gateway use. No request is sent. | `coding_plan_blocked` | `false` |
| Both | No API key to use: none is stored, or the upstream expects each client to send its own. No request is sent. | `upstream_key_missing` | `false` |
| Connection | 2xx with a JSON object that holds a `data` list | `passed` | `true` |
| Connection | Any other 2xx: invalid JSON, a body over 16 KiB, no `data` list, or an `error` field | `upstream_key_rejected` | `false` |
| Connection | 404 or 405: the provider has no model list, so the key is not verified | `models_unavailable` | `true` |
| Connection | 429: the address answered, but the key is not verified | `rate_limited` | `true` |
| Model | 2xx with a response matching the configured model protocol | `passed` | `true` |
| Model | Any other 2xx: invalid JSON, a body over 16 KiB, or a response that does not match the protocol | `invalid_response` | `false` |
| Model | 429 | `rate_limited` | `false` |
| Model | 404 or 405 | `model_request_rejected` | `false` |
| Both | 400 or 422 | `model_request_rejected` | `false` |
| Both | 401 or 403 | `upstream_key_rejected` | `false` |
| Both | Network error, timeout, redirect, 5xx or any other status | `upstream_unavailable` | `false` |

A connection test that returns `passed` confirms reachability and the stored credentials. `models_unavailable` and `rate_limited` confirm only that the address is reachable; Studio shows them as **Connected** with a neutral badge. Run a model test to check the key. Coding Plan approval applies only to model tests, so a connection test of an unapproved Coding Plan upstream is still sent. A passed model test confirms that the selected model completed this request. It does not verify other models or authorize Coding Plan use.

### Import VLM settings {#import-vlm-settings}

`POST /gateway/admin/upstreams/import-vlm` turns OpenViking Server's VLM credentials into upstreams in the selected account. It is available only in embedded deployments; a standalone gateway returns 404 `vlm_import_unavailable`.

Each imported upstream gets the fixed ID `vlm-<credential id>`, so importing again does not create duplicates. A repeat import refreshes the fields that come from the server (`base_url`, `api_key`, `headers`, `protocol`, `vendor`, `coding_plan`, `append_endpoint_path`, `key_header`) and keeps every other field as it was edited in the gateway, including `name`, `enabled`, `priority`, `models`, `aliases`, `context_windows` and `allow_coding_plan`. Upstreams whose path has a `coding` or `plan` segment are marked as Coding Plan and listed under `needs_confirmation` until `allow_coding_plan` is set.

```json
{
  "imported": [{"id": "vlm-default", "name": "default"}],
  "needs_confirmation": [{"id": "vlm-coding", "name": "coding"}],
  "skipped": [{"id": "vlm-codex", "reason": "openai_codex"}]
}
```

| Skip `reason` | Meaning |
| --- | --- |
| `openai_codex` | Codex OAuth tokens refresh periodically, so the gateway cannot use them long term. |
| `litellm_no_api_base` | The LiteLLM credential has no `api_base`. Add it in the OpenViking configuration and import again. |
| `litellm_unsupported_route` | The LiteLLM route does not use the OpenAI Chat Completions API, such as `gemini/`, `bedrock/` or `vertex_ai/`. Add that provider as an upstream by hand. |
| `invalid_configuration` | The credential does not make a valid upstream, for example because the base URL is invalid. |

## Gateway keys {#gateway-keys}

| Method | Path | Description |
| --- | --- | --- |
| GET | `/gateway/admin/keys` | List the keys the caller can see, without the secret |
| GET | `/gateway/admin/keys/{identifier}` | Read one visible key |
| POST | `/gateway/admin/keys` | Issue a key; `key` in the response is the only time the full secret is returned |
| DELETE | `/gateway/admin/keys/{identifier}` | Revoke a key; user data is kept |
| POST | `/gateway/admin/keys/{identifier}/capture/reset` | Save a conversation again, to a new OpenViking session |
| DELETE | `/gateway/admin/users/{user_id}/data` | Admin or root: revoke all of the user's keys and delete their conversation state and logs |
| DELETE | `/gateway/admin/account/data` | Root: delete all of the account's gateway objects, keys, conversation state and logs |

Request body for issuing a key:

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Display name. |
| `policy_id` | Yes | The context profile to use. |
| `upstream_ids` | Yes | The upstreams the key may use; at least one. |
| `models` | No | Allowed model names (as clients send them, including aliases); an empty list allows all. |
| `user_id` | No | The OpenViking user the key belongs to; defaults to the caller. Root is not an account user, so root must name one (or supply `openviking_key`). Users can only name themselves. |
| `openviking_key` | No | That user's OpenViking API key. Usually not needed; see below. |
| `default_peer_id` | No | The peer requests use when they send no `X-OpenViking-Actor-Peer` header; see [Tell memory apart by peer](../gateway/03-connect-clients.md#peer). It follows the rules of a message's `peer_id`; a blank value means none. |

The gateway needs the target user's OpenViking API key, because it searches and saves memory as that user:

- When callers (users or admins) issue a key for themselves, the gateway uses the caller's own key.
- When an admin or root issues a key for someone else, the gateway reads that user's key from OpenViking with the caller's credentials. If OpenViking stores only key hashes, it cannot, and the response is 409 `user_key_unreadable`; supply the user's key in `openviking_key`.
- The gateway checks the key with OpenViking: it must belong to the selected account and, when `user_id` is given, to that user.
- A root API key cannot be bound to a gateway key, except `default/default` in local dev.

```bash
curl -X POST "$SERVER_URL/gateway/admin/keys?account_id=team" \
  -H "X-API-Key: $OPENVIKING_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Alice laptop","user_id":"alice","policy_id":"default","upstream_ids":["openai"]}'
```

```json
{
  "id": "3f9c…",
  "name": "Alice laptop",
  "user_id": "alice",
  "policy_id": "default",
  "upstream_ids": ["openai"],
  "models": [],
  "prefix": "ovgw_Xk2pQ7a",
  "created_at": 1760000000.0,
  "revision": 1,
  "key": "ovgw_Xk2pQ7a…"
}
```

Save the `key` from the response right away; the gateway keeps only its hash. `GET` returns the same fields without `key`. Gateway keys cannot be changed and have no `PUT`. To change the context profile, upstreams, models, default peer or user, issue a new key and revoke the old one.

When the user's OpenViking API key is regenerated or the user is deleted, the gateway keys bound to it stop working and are deleted; the user's data stays. See [Gateway keys](../gateway/09-gateway-keys.md#gateway-keys).

**Resetting saving.** The body is `{"session":"<session header value or the session from the log>","protocol":"chat"}`, where `protocol` is `chat`, `anthropic` or `responses`. On success the response is `{"status":"ready","message":"…"}`, and when the conversation's next turn ends, the gateway writes the full history to a new OpenViking session. If the conversation is not found, the response is 404 `not_found`.

**Deleting data.** Both delete endpoints remove the conversation state the gateway keeps, and logs. Sessions and memories already stored in OpenViking are not affected. Retention and the other ways data is deleted are described in [Security and data](../gateway/11-security-and-data.md).

## Errors {#errors}

Error responses from the management API carry a stable top-level `reason` and a short English `detail`. Some carry extra fields, for example:

```json
{"reason": "in_use", "detail": "Gateway keys still use this object; revoke them first.", "key_count": 2}
```

Check `reason`, not the text of `detail`. When the server does not have the gateway enabled, these paths do not exist and return 404; Studio shows this as `not_enabled`.

| HTTP | `reason` | Meaning and fix |
| --- | --- | --- |
| 401 | `management_key_invalid` | The management key is wrong. Ask the deployer for the current one, or see [Lost the management key](../guides/22-gateway-operations.md#lost-the-management-key). |
| 403 | `management_key_required` | A management key is set, and changing upstreams or context profiles requires it. |
| 401 | `openviking_http_401` | OpenViking rejected the API key. Use a currently valid key. |
| 401 | `openviking_identity_missing` | OpenViking returned no user for this key. |
| 403 | `trusted_mode_unsupported` | OpenViking uses trusted authentication. Switch to `api_key` authentication. |
| 403 | `other_account` | The key or the requested account is outside the caller's scope. |
| 403 | `forbidden` | The operation is beyond the caller's role or user scope. |
| 403 | `root_key_not_allowed` | A root API key cannot be bound to a gateway key. Use a user's API key. |
| 409 | `user_key_unreadable` | OpenViking stores only a hash of the user's key. Supply the user's key in `openviking_key`. |
| 409 | `in_use` | `key_count` gateway keys still use this object. |
| 400 | `unknown_user`, `unknown_policy`, `unknown_upstream` | The named user, context profile or upstream does not exist. |
| 400 | `user_required` | Root issued a key without `user_id` or `openviking_key`. Name the user the key is for. |
| 404 | `not_found` | The object does not exist, or the caller cannot see it. |
| 422 | `invalid_configuration` | The request body or Base URL is invalid. The error never repeats submitted credentials. |
| 403 | `vlm_import_forbidden` | The caller may not import; `can_import_vlm` in `/access` is authoritative. |
| 404 | `vlm_import_unavailable` | A standalone gateway cannot read the server's model settings. Add upstreams by hand. |
| 503 | `openviking_unavailable`, `openviking_unreachable`, `openviking_timeout` | The gateway cannot reach OpenViking, or it did not answer in time. Check that OpenViking is running; for a standalone gateway, also check `openviking_url` (in `gateway.conf` or `OPENVIKING_GATEWAY_OPENVIKING_URL`). |
| 503 | `openviking_version_mismatch` | Standalone gateways only: OpenViking Server is older than required. `required_version` in the response gives the minimum version. |

Other HTTP errors from OpenViking are reported as `openviking_http_<status>`. The gateway's own generic HTTP errors are reported as `gateway_http_<status>`, for example 405 `gateway_http_405` for an unsupported method. These names apply to the management API, `/uploads` and `/downloads`; model routes use the reasons below.

### Model route errors {#model-route-errors}

An error the gateway produces on a model route (`/gateway/v1/*`, `/gateway/api/v3/*`, `/gateway/api/compatible/v1/*`) has the body `{"reason": "...", "detail": "...", "request_id": "..."}`; `openviking_version_mismatch` adds `required_version`. A provider's own error is relayed unchanged, with its status and body. The request record carries the same reason in `error_reason`.

| Status | Reason | Origin | Meaning |
| --- | --- | --- | --- |
| 401 | `gateway_key_invalid` | gateway | The gateway key is missing, wrong or revoked. |
| 401 | `openviking_key_invalid` | openviking | OpenViking rejected the OpenViking API key the client sent, or the one behind the gateway key. |
| 401 | `openviking_identity_missing` | openviking | OpenViking returned no identity for the key. |
| 402 | `openviking_not_ordered` | openviking | OpenViking service is not ordered for the key. |
| 403 | `openviking_forbidden` | openviking | OpenViking refused the key. |
| 403 | `root_key_not_allowed`, `trusted_mode_unsupported` | openviking | A root key cannot back a gateway key; the gateway does not support trusted authentication. |
| 429 | `openviking_rate_limited` | openviking | OpenViking rate limited the key check. |
| 503 | `openviking_unavailable` | openviking | OpenViking could not be reached, or failed, while the gateway checked a key for the first time. |
| 503 | `openviking_timeout` | openviking | OpenViking did not answer that check in time. |
| 503 | `openviking_version_mismatch` | openviking | OpenViking Server is older than the gateway requires. |
| varies | `openviking_error` | openviking | Any other OpenViking error during that check, with OpenViking's status. |
| 403 | `oauth_credential_unsupported` | gateway | The client or the upstream uses a Claude subscription token. |
| 413 | `body_too_large` | gateway | The body exceeds `max_body_bytes`. |
| 415 | `compressed_body_unsupported` | gateway | A key with allowed models received a content-encoded body. |
| 400 | `model_missing` | gateway | A key with allowed models received a body without a model. |
| 403 | `model_not_allowed` | gateway | The model is not among the key's allowed models. |
| 400, 403 | `context_override_invalid`, `context_override_not_allowed` | gateway | `X-OpenViking-Context` is invalid, or the key's profile does not accept it. |
| 400 | `peer_invalid` | gateway | `X-OpenViking-Actor-Peer` is not a valid peer ID. |
| 403 | `policy_unavailable` | gateway | The key's context profile no longer exists. |
| 403 | `upstream_url_not_allowed` | gateway | `X-OpenViking-Upstream-URL` names no upstream bound to the key. |
| 404 | `no_matching_upstream` | gateway | No enabled upstream bound to the key serves this protocol and model. |
| 404 | `upstream_endpoint_mismatch` | gateway | The upstream is an exact endpoint for another path. |
| 404, 403 | `response_unknown`, `response_upstream_disallowed` | gateway | The stored Responses response is not this key's, or its upstream is no longer allowed. |
| 401 | `upstream_key_missing` | gateway | No upstream API key is stored or sent in `X-OpenViking-Upstream-Key`. |
| 403 | `coding_plan_disabled` | gateway | The upstream is a Coding Plan key that is not allowed through the gateway. |
| 503 | `store_unavailable` | gateway | The gateway's state store failed. The response carries `Retry-After: 1`. |
| 503 | `gateway_overloaded` | gateway | The process already serves `max_inflight_requests` model requests. Nothing was sent to the provider. The response carries `Retry-After: 1`. |
| 500 | `internal_error` | gateway | The gateway failed unexpectedly. The detail is generic; the gateway's log has the traceback. |
| 504 | `upstream_timeout` | upstream | The provider did not start its reply, or a non-streamed reply stopped arriving, within `upstream_timeout_seconds`. |
| 502 | `upstream_unreachable` | upstream | The gateway could not connect to the provider (DNS, refused connection, TLS). |
| 502 | `upstream_disconnected` | upstream | The provider closed the connection before its reply was complete. |
| 502 | `tool_upstream_invalid`, `tool_upstream_incomplete`, `tool_upstream_stream_error`, `tool_call_invalid` | upstream | During OpenViking tool rounds, the provider's reply was unusable, incomplete or an error, or the model called a tool it may not use. |
| 502 | `tool_loop_limit` | gateway | The OpenViking tool rounds reached a limit. |
| 504 | `tool_loop_timeout` | gateway | The OpenViking tool rounds ran out of time. |

A WebSocket request to a Responses route still gets an empty 426; its record says `websocket_unsupported`. A `gateway_http_<status>` reason on a model route would mean an error the gateway does not classify yet.

A 401 that a model client receives is not a management API error. It means the gateway key is missing, wrong or revoked, or the OpenViking key bound to it is no longer valid; see [Clients get an error from the gateway](../gateway/12-troubleshooting.md#clients-get-an-error-from-the-gateway).
