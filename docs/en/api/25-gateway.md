# Context Gateway Management API

OpenViking Server forwards Context Gateway management calls below `/api/v1/admin/context-gateway`. Studio's Context Gateway page uses these endpoints to manage upstreams, context profiles and gateway keys, and to read request logs. Model traffic does not go through this API; clients send it to the gateway's own port (see [Context Gateway](../guides/15-context-gateway.md)).

**Prerequisites**:

- `context_gateway.enabled` is `true` in `ov.conf`, and the gateway process is reachable at `context_gateway.url`.
- OpenViking Server and the gateway share the admin token (`OPENVIKING_CONTEXT_GATEWAY_ADMIN_TOKEN` by default, at least 32 characters).
- The caller uses an ADMIN key or the root key. USER keys get `403`.

**Code entry points**:

- `openviking/server/routers/context_gateway.py` - OpenViking Server proxy, role check and account scoping
- `context_gateway/app.py` - gateway management routes
- `context_gateway/models.py` - upstream, context profile and key models

## How the proxy works

| Method | Path | Description |
|--------|------|-------------|
| GET / POST / PUT / DELETE | `/api/v1/admin/context-gateway/{path}` | Forwarded to `{context_gateway.url}/admin/{path}` for the caller's account |

Every request to `/api/v1/admin/context-gateway/{path}` is sent to `{context_gateway.url}/admin/{path}` with the same method, query string and body. OpenViking Server replaces the caller's credentials with the admin token and adds `X-OpenViking-Account` set to the caller's account, so every call reads and changes only that account's gateway objects. An account admin cannot reach another account's data, and the caller cannot choose the destination.

Only these first path segments are forwarded: `overview`, `logs`, `guides`, `upstreams`, `policies`, `keys`, `users` and `tools`. Any other path, or a path containing `..`, returns `404`. The gateway's response status and body are returned unchanged.

## API Reference

Paths in the tables are relative to `/api/v1/admin/context-gateway/`. The management API calls context profiles `policies`.

### Overview and logs

| Method | Path | Description |
|--------|------|-------------|
| GET | `overview` | Usage summary of the latest 10,000 log records: request count, output tokens, cache hit ratios for first calls and continuations, degradations, recall statistics and saving problems, plus OpenViking health and `log_retention_days` |
| GET | `logs?limit=200` | Newest request log records; `limit` is 1–1000, default 200 |
| GET | `guides` | Address clients should use: `base_url` and `public_url_configured` |
| GET | `tools` | OpenViking tools a context profile can offer: `name`, `description` and, when present, `annotations` |

`tools` reads the tool list with the OpenViking key bound to one of the account's gateway keys. It returns an empty list while the account has no gateway keys, and an error when none of them can read it.

**HTTP API**

```bash
curl http://localhost:1933/api/v1/admin/context-gateway/overview \
  -H "X-API-Key: your-admin-key"
```

**Response Example**

```json
{
  "requests": 128,
  "last_request_at": 1785000000.0,
  "output_tokens": 45210,
  "cache": {
    "first_call": {"requests": 40, "input_tokens": 320000, "cached_tokens": 250000, "cache_hit_ratio": 0.78},
    "continuation": {"requests": 88, "input_tokens": 910000, "cached_tokens": 860000, "cache_hit_ratio": 0.95}
  },
  "degradations": {},
  "recall_count": 96,
  "recall_requests": 40,
  "recall_ms": 182.5,
  "capture_issues": {"retrying": 0, "paused": 0},
  "sample_limit": 10000,
  "openviking": {"status": "ok", "healthy": true, "version": "0.4.16", "auth_mode": "api_key"},
  "log_retention_days": 30
}
```

### Upstreams and context profiles

| Method | Path | Description |
|--------|------|-------------|
| GET | `upstreams` | List upstreams |
| PUT | `upstreams/{upstream_id}` | Create or replace an upstream |
| DELETE | `upstreams/{upstream_id}` | Delete an upstream |
| POST | `upstreams/{upstream_id}/test` | Check that the gateway can reach the provider's model list |
| GET | `policies` | List context profiles |
| PUT | `policies/{policy_id}` | Create or replace a context profile |
| DELETE | `policies/{policy_id}` | Delete a context profile |

The caller chooses `upstream_id` and `policy_id`. A `PUT` body is the complete object, and unknown fields are rejected. For every field, its default and limits, see the upstream and context profile settings in the [configuration reference](../guides/22-context-gateway-operations.md#configuration-reference).

Upstream `api_key` and `headers` values are write-only. Responses replace them with `has_api_key` and `header_names`. On `PUT`, a blank `api_key` keeps the stored key; omitting `headers` keeps every stored header, and a blank header value keeps the stored value for that name.

Deleting an upstream or context profile that a gateway key still uses returns `409`; revoke or update those keys first.

**HTTP API**

```bash
curl http://localhost:1933/api/v1/admin/context-gateway/upstreams \
  -H "X-API-Key: your-admin-key"
```

**Response Example**

```json
[
  {
    "id": "openai",
    "revision": 2,
    "name": "OpenAI",
    "protocol": "chat",
    "vendor": "openai",
    "base_url": "https://api.openai.com/v1",
    "auth_mode": "managed",
    "has_api_key": true,
    "header_names": [],
    "models": ["gpt-5"],
    "aliases": {},
    "context_windows": {},
    "priority": 0,
    "enabled": true,
    "allow_gateway_tools": true,
    "coding_plan": false,
    "allow_coding_plan": false,
    "cache_min_tokens": 1024
  }
]
```

`upstreams/{upstream_id}/test` returns `{"ok": true, "status": 200}` with the provider's status, or `{"ok": false, "reason": "upstream_unavailable"}` when the provider cannot be reached within 10 seconds.

### Gateway keys

| Method | Path | Description |
|--------|------|-------------|
| GET | `keys` | List gateway keys without their secrets |
| POST | `keys` | Issue a gateway key; the secret appears only in this response |
| DELETE | `keys/{key_id}` | Revoke a gateway key |
| POST | `keys/{key_id}/capture/reset` | Make conversation saving for one session resync from the next request |

`POST keys` body:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Display name |
| `openviking_key` | string | Conditional | OpenViking key of the user the gateway key acts for; send this or `user_id` |
| `user_id` | string | Conditional | User in the caller's account; OpenViking Server reads that user's key. Only account admins can use it |
| `policy_id` | string | Yes | Context profile |
| `upstream_ids` | string[] | Yes | At least one upstream |
| `models` | string[] | No | Allowed models; empty allows every model the upstreams serve |

The OpenViking key must belong to a USER or ADMIN of the caller's account. `user_id` works only when OpenViking Server can read stored user keys; with key hashing enabled, send `openviking_key` instead. Root callers must also send `openviking_key`, because the root key may resolve to a different account than the one Studio shows.

Existing keys cannot be edited; `PUT keys/{key_id}` returns `405`. Issue a replacement and revoke the old key.

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/admin/context-gateway/keys \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"name":"alice laptop","user_id":"alice","policy_id":"default","upstream_ids":["openai"]}'
```

**Response Example**

```json
{
  "id": "3f6c…",
  "revision": 1,
  "name": "alice laptop",
  "policy_id": "default",
  "upstream_ids": ["openai"],
  "models": [],
  "user_id": "alice",
  "prefix": "ovcg_Xk3p9Q",
  "created_at": 1785000000.0,
  "key": "ovcg_Xk3p9Q…"
}
```

`keys/{key_id}/capture/reset` takes `{"session": "<session>", "protocol": "chat"}`, where `protocol` is `anthropic`, `chat` or `responses`. `session` is either the session header value the client sent, such as `X-OpenViking-Session`, or the `session` field of the request's log record; a session without a header can only be named by the latter. It returns `{"status": "ready", "message": "…"}`, or `404` when the key or session does not exist.

### User data

| Method | Path | Description |
|--------|------|-------------|
| DELETE | `users/{user_id}/data` | Revoke all of a user's gateway keys and delete their conversation state in the gateway |

Sessions already saved to OpenViking are not affected.

## Errors

Errors raised by OpenViking Server itself use the standard error envelope:

| Status | Code | Cause |
|--------|------|-------|
| 400 | `INVALID_ARGUMENT` | Root sent `user_id` in `POST keys`; unknown `user_id` |
| 400 | `INVALID_ARGUMENT` | `POST keys` sent both `user_id` and `openviking_key` (original status 422) |
| 403 | `PERMISSION_DENIED` | Caller is not ROOT or ADMIN |
| 404 | `NOT_FOUND` | Path outside the forwarded resources |
| 409 | `CONFLICT` | The chosen user's key cannot be read on the server |
| 503 | `UNAVAILABLE` | Context Gateway is not enabled, the admin token is not configured, or the gateway cannot be reached (original status 502) |

Errors from the gateway pass through with its own status and a body such as `{"detail": "Unknown context policy"}`:

| Status | Cause |
|--------|-------|
| 400 | `POST keys` names an unknown context profile or upstream |
| 403 | The OpenViking key belongs to another account |
| 404 | Unknown resource, key or session |
| 405 | `PUT keys/{key_id}` |
| 409 | Deleting an upstream or context profile that keys still use |
| 422 | Invalid body. The message does not echo submitted values, because they can contain secrets |

When the gateway cannot verify an OpenViking key, the body is `{"error": {"message": "<reason>"}}` with reasons listed in [Issuing a key fails](../guides/22-context-gateway-operations.md#issuing-a-key-fails).

## Related Documentation

- [Context Gateway](../guides/15-context-gateway.md) - how the gateway works and how to connect clients
- [Context Gateway deployment and operations](../guides/22-context-gateway-operations.md) - deployment, Studio management and configuration reference
- [Admin](08-admin.md) - accounts, users and roles
