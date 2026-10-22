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

The log holds metadata only, never message content. A record is written when a request to a model provider finishes, and when a conversation's saving status changes (`kind` is `capture`). Requests the gateway rejects before reaching a provider are not logged. Every field except `time` is optional and is left out when it does not apply.

| Field | Meaning |
| --- | --- |
| `time` | When the record was written, in epoch seconds |
| `request_id` | The gateway's ID for the request |
| `kind` | `user`, `continuation`, `auxiliary`, `subagent`, `count`, `passthrough` or `capture`; see [Requests](../gateway/10-overview-and-requests.md#requests) |
| `model` | The model name the client sent |
| `status` | The provider's HTTP status |
| `upstream_id`, `credential_id` | The upstream that served the request, and the gateway key that sent it |
| `session`, `protocol` | The conversation and its protocol; use them to [reset saving](#gateway-keys) |
| `degradation` | Issue code; see [Issues on degraded requests](../gateway/12-troubleshooting.md#issues-on-degraded-requests) |
| `recall_reason`, `recall_count`, `recall_ms` | Memory search result code, entries added and search time; see [No memory is added](../gateway/12-troubleshooting.md#no-memory-is-added) |
| `replay_hits` | Earlier messages whose memory was replayed unchanged |
| `capture_status`, `capture_reason`, `capture_retry_at` | Saving status (`active`, `disabled`, `retrying` or `paused`), its reason, and the next retry in epoch seconds; see [Saving status](../gateway/10-overview-and-requests.md#saving-status) |
| `tool_skip_reason` | Why the request got no OpenViking tools; see [When tools are not offered](../gateway/04-openviking-tools.md#when-tools-are-not-offered) |
| `tool_stop_reason` | `token_budget` when the tool token budget refused further calls; see [Limits](../gateway/04-openviking-tools.md#limits) |
| `compaction_failed` | Why compaction failed (with `still_over_threshold` the summary was kept); see [Long conversations hit the context limit](../gateway/12-troubleshooting.md#long-conversations-hit-the-context-limit) |
| `context_tokens`, `context_window` | Estimated context size, and the window it was compared with |
| `input_tokens`, `output_tokens`, `cached_tokens`, `cache_write_tokens` | Token usage summed over every provider call of the request, OpenViking tool rounds included. `input_tokens` includes cached and cache-write tokens. |
| `duration_ms` | Time from sending the request to the provider until the reply finished |

Fields that start with `first_upstream_` and `hidden_upstream_` split the token usage between the first provider call and the OpenViking tool rounds; the first-call cache hit rate uses the first group.

`/overview` aggregates these records. Besides usage and cache figures, it returns `degradations` (issue code → count), `capture_issues` (conversations retrying or paused), `cache_alerts`, `openviking` (OpenViking's `status`, plus `reason` when degraded) and `log_retention_days`.

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
| POST | `/gateway/admin/keys/{identifier}/capture/reset` | Start saving a conversation again |
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

Save the `key` from the response right away; the gateway keeps only its hash. `GET` returns the same fields without `key`. Gateway keys cannot be changed and have no `PUT`. To change the context profile, upstreams, models or user, issue a new key and revoke the old one.

When the user's OpenViking API key is regenerated or the user is deleted, the gateway keys bound to it stop working and are deleted; the user's data stays. See [Gateway keys](../gateway/09-gateway-keys.md#gateway-keys).

**Resetting saving.** The body is `{"session":"<session header value or the session from the log>","protocol":"chat"}`, where `protocol` is `chat`, `anthropic` or `responses`. On success the response is `{"status":"ready","message":"…"}`, and on the conversation's next request the gateway writes the full history to a new OpenViking session. If the conversation is not found, the response is 404 `not_found`.

**Deleting data.** Both delete endpoints remove conversation content the gateway has not yet saved, and logs. Sessions and memories already stored in OpenViking are not affected. Retention and the other ways data is deleted are described in [Security and data](../gateway/11-security-and-data.md).

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
| 503 | `openviking_unavailable`, `openviking_unreachable` | The gateway cannot reach OpenViking. Check that OpenViking is running; for a standalone gateway, also check `openviking_url` (in `gateway.conf` or `OPENVIKING_GATEWAY_OPENVIKING_URL`). |
| 503 | `openviking_version_mismatch` | Standalone gateways only: OpenViking Server is older than required. `required_version` in the response gives the minimum version. |

Other HTTP errors from OpenViking are reported as `openviking_http_<status>`. The gateway's own generic HTTP errors are reported as `gateway_http_<status>`, for example 405 `gateway_http_405` for an unsupported method.

A 401 that a model client receives is not a management API error. It means the gateway key is missing, wrong or revoked, or the OpenViking key bound to it is no longer valid; see [Clients get an error from the gateway](../gateway/12-troubleshooting.md#clients-get-an-error-from-the-gateway).
