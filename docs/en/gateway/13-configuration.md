---
description: Look up every OpenViking Gateway configuration field, environment variable, context profile setting and upstream setting.
---

# OpenViking Gateway configuration reference

This page is for operators editing `ov.conf`, `gateway.conf` or environment variables, and for anyone mapping a Studio label to its management API name. Every table gives the name, default and accepted values; what a setting does and when to change it are on the feature page linked from each section.

## Where the settings live {#configuration-reference}

An embedded and a standalone gateway share most fields; they differ in which file holds them and how you create it. The **Form** column under [Gateway fields](#gateway-fields) marks the fields that only one form uses.

| | Embedded | Standalone |
| --- | --- | --- |
| File | The `gateway` section of `ov.conf` | The root of `gateway.conf`, with no `gateway` wrapper |
| Created by | `openviking-server init` | `openviking-gateway init` |
| Which file is read | The server's own `ov.conf` | `--config` or `OPENVIKING_GATEWAY_CONFIG_FILE`, which must point to an existing file; otherwise the first of `~/.openviking/gateway.conf` and `/etc/openviking/gateway.conf` that exists |
| Host, port and workers | Come from the server | The `host`, `port` and `workers` fields |

`OPENVIKING_CONFIG_FILE` only selects the server's configuration; it never points a standalone gateway at a file.

## Gateway fields {#gateway-fields}

| Field | Form | Default | Accepted values | Meaning |
| --- | --- | --- | --- | --- |
| `enabled` | Embedded | `false` | | Turns the gateway on inside the server |
| `openviking_url` | Standalone | — (required) | HTTP(S) URL | Address of the OpenViking Server |
| `host` | Standalone | `127.0.0.1` | | Listen address |
| `port` | Standalone | `1935` | 1–65,535 | Listen port |
| `workers` | Standalone | `1` | 1–64 | Worker processes |
| `keep_alive_seconds` | Standalone | `75` | More than 0 | Seconds to keep an idle HTTP connection open between requests; set it above the load balancer's backend idle timeout |
| `public_url` | Both | empty | HTTP(S) URL, without `/gateway` | The external address clients use; the gateway appends `/gateway` itself. When empty, an embedded gateway uses the server's public address; with neither, setup guides use the request's origin, and MCP file uploads and downloads do not work unless `mcp_file_links` is `direct`. See [Public address](../guides/22-gateway-operations.md#public-address) |
| `mcp_file_links` | Both | `proxy` | `proxy`, `direct` | How links from the MCP upload and download tools reach the client. `proxy` rewrites them to `public_url` plus `/gateway/uploads` or `/gateway/downloads` and needs `public_url`. `direct` passes OpenViking's links through unchanged and needs the server's `OPENVIKING_PUBLIC_BASE_URL` (or `server.public_base_url`) to be reachable by the client |
| `storage_path` | Both | `~/.openviking/gateway` | | Local storage directory |
| `storage` | Both | Both ports `sqlite` | See [Storage providers](#storage) | Per-port providers and one shared Redis connection |
| `encryption` | Standalone | not set (plain text) | Same fields as the `encryption` section of `ov.conf` | Encrypts stored data; see [Encryption](../guides/22-gateway-operations.md#encryption). Embedded gateways use the server's section |
| `management_api` | Both | `true` | `true`, `false` | Registers the gateway administration routes; disabling it also removes the standalone OpenViking admin proxy and Studio. Model routes and `/health` remain available |
| `management_key` | Both | empty | At least 32 characters | Optional [gateway management key](../guides/22-gateway-operations.md#gateway-management-key) |
| `openviking_keys` | Both | `false` | `true`, `false` | Lets clients use a user's OpenViking API key in place of a gateway key, with the account's default context profile and every enabled upstream; see [Use an OpenViking API key directly](09-gateway-keys.md#openviking-keys) |
| `tenancy` | Both | `account` | `account`, `key` | Partitions OpenViking-key requests by account or by key; `key` requires `management_api: false` |
| `default_upstreams` | Both | `{}` | Object mapping stable IDs to [upstream settings](#upstream-settings) | Upstreams for OpenViking keys when the account stores none; see [Deployment default upstreams](07-upstreams.md#deployment-default-upstreams) |
| `default_profile` | Both | Built-in profile defaults | [Context profile settings](#context-profile-settings) | Used when the account has no profile with the ID `default`; also the initial profile seeded in embedded dev mode |
| `min_server_version` | Both | `0.4.16` | Version string | Lowest OpenViking Server version a standalone gateway accepts |
| `session_ttl_seconds` | Both | `2592000` (30 days) | 3600 or more | Seconds of inactivity before a conversation expires; also the fixed lifetime of cached OpenViking keys and their user records. |
| `response_ttl_seconds` | Both | `2592000` (30 days) | 60 or more | Seconds to remember which upstream created a Responses response |
| `request_log_sink` | Both | `store` | `store`, `stdout` | Write request and background saving logs to storage or one flushed JSON line per record on stdout; see [Monitoring](../guides/22-gateway-operations.md#monitoring) |
| `log_retention_days` | Both | `30` | 1 or more | Days to keep request logs in storage; stdout retention is managed by the log collector |
| `max_body_bytes` | Both | `33554432` (32 MiB) | 1,024 or more | Size limit for requests and uploads, in bytes |
| `upstream_timeout_seconds` | Both | `600` | More than 0 | Timeout for provider requests |
| `health_interval_seconds` | Both | `60` | 1 or more | Seconds between OpenViking status checks |
| `capture_concurrency` | Both | `1` | 1 or more | Concurrent conversation deliveries per process; shutdown stops new claims and gives in-flight deliveries up to 100 seconds to finish |
| `client_rules` | Both | `{}` (packaged rules only) | See [Client rules](#client-rules) | Text added to or removed from what the gateway recognizes as housekeeping requests and client-inserted messages |

For example, a standalone `gateway.conf` can route OpenViking keys before any account is configured:

```json
{
  "openviking_url": "http://openviking:1933",
  "openviking_keys": true,
  "default_upstreams": {
    "ark-chat": {
      "name": "Ark Chat",
      "protocol": "chat",
      "base_url": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "$ARK_API_KEY",
      "models": ["your-model-id"]
    }
  },
  "default_profile": {"max_tokens": 2400}
}
```

## Storage providers {#storage}

The default is local SQLite, with no external service. `storage.kernel` and `storage.management` each select `sqlite` or `redis`; Redis ports share one client and the settings in `storage.redis`. Install `pip install "openviking[expiry]"` for Redis; the official image already includes it. Standalone Redis and Redis Cluster are tested. Sentinel configuration is accepted but is not covered by gateway CI.

```json
{
  "storage": {
    "kernel": "redis",
    "management": "redis",
    "key_prefix": "ovgw:",
    "redis": {
      "mode": "standalone",
      "endpoints": ["rediss://redis.example.com:6379"],
      "username": "gateway",
      "password_env": "OPENVIKING_GATEWAY_REDIS_PASSWORD",
      "pool_size": 32,
      "command_timeout_ms": 5000
    }
  },
  "session_ttl_seconds": 604800
}
```

`OPENVIKING_GATEWAY_STORAGE` accepts the same `storage` object as JSON. Inject the password through the named environment variable, for example from a Kubernetes Secret. `key_prefix` defaults to `ovgw:` and cannot contain `{`, `}`, `*`, `?`, `[`, `]` or `\`; every key starts with that prefix plus `v1:`. Give separate deployments different prefixes.

`storage.redis` reuses OpenViking's `RedisCacheConfig`: `mode`, `endpoints`, `username`, `password_env`, legacy `password`, `db`, `pool_size`, `connect_timeout_ms`, `command_timeout_ms`, `default_ttl_seconds`, `tls_insecure_skip_verify`, and the Sentinel fields `master_name`, `sentinel_username`, `sentinel_password_env`, `sentinel_password`. The gateway overrides the command timeout default from 20 to 5000 ms for large conversation values; `default_ttl_seconds` does not set gateway retention. URLs contain only a scheme, host and port; use the dedicated credentials and `db` fields. For Cluster use `mode: "cluster"`, multiple seed endpoints and `db: 0`. TLS follows the `rediss://` scheme. For a private CA, mount its certificate and set `SSL_CERT_FILE` to its path (with Helm, use `extraVolumes` and `extraVolumeMounts`).

Give the gateway its own Redis instance with `volatile-lru` eviction and AOF; the `redis.conf` settings and what to monitor are in [Redis memory policy](../guides/22-gateway-operations.md#redis-memory-policy).

Create an ACL user with the following command, replacing the password and prefix. Run it on **every Cluster node**, because ACLs are not replicated between nodes:

```bash
redis-cli ACL SETUSER gateway reset on '>replace-with-secret' '~ovgw:*' \
  +@read +@write +@scripting +@connection +@transaction +ping +time +scan +unlink
```

For Cluster also grant `'+cluster|slots' '+cluster|shards'`. `+time` is required by Lua scripts. These permissions and prefix isolation are exercised in CI. Application commands do not retry after connection errors because a failed write may already have reached Redis.

## Environment variables {#environment-variables}

Every gateway field can be set as an environment variable named `OPENVIKING_GATEWAY_` plus the field name in upper case, in both forms:

```bash
OPENVIKING_GATEWAY_PUBLIC_URL=https://ov.example.com
OPENVIKING_GATEWAY_MANAGEMENT_KEY='<management-key>'
```

- **Environment variables override the file.** A non-empty variable wins over the value in the file; an empty variable counts as unset.
- Values that start with `tcp://` or `udp://` are ignored, so the variables Kubernetes sets for a Service named `openviking-gateway` (such as `OPENVIKING_GATEWAY_PORT=tcp://…`) do not override the file.
- A value in the file can also be a `"$VAR"` reference, resolved from the environment.
- The `encryption` block can only be set in the file.
- `client_rules`, `storage`, `default_upstreams` and `default_profile` take whole sections as JSON, for example `OPENVIKING_GATEWAY_CLIENT_RULES='{"auxiliary_patterns":{"add":["You are the Acme title writer"]}}'` or `OPENVIKING_GATEWAY_DEFAULT_PROFILE='{"max_tokens":2400}'`.
- Containers usually need no file: set the fields they need as variables.

## Validation {#validation}

| What | Rule |
| --- | --- |
| `openviking_url` and `public_url` | HTTP(S), with no credentials, query or fragment |
| Upstream `base_url` | Its own rules; see [Endpoint](07-upstreams.md#endpoint) |
| `management_key` | At least 32 characters once `$VAR` references are resolved; an unresolved `$VAR` fails |
| `tenancy` | `key` requires `management_api: false`; it has no effect when `openviking_keys` is off |
| `client_rules` | Every added pattern must be a valid Python regular expression; added tag names start with an ASCII letter and use only ASCII letters, digits, `_`, `.`, `:` and `-` |
| Unknown fields | Fail validation |
| Removed fields | Ignored, with a startup warning: `encryption_key_env`, `session_ttl_days` (replaced by `session_ttl_seconds`), and in `ov.conf` also the v0.5 fields listed in [Migration](../guides/22-gateway-operations.md#migration) |

## Client rules {#client-rules}

The gateway tells [housekeeping requests](10-overview-and-requests.md#request-types) and the messages clients insert on the user's behalf apart from real user messages by fixed text, listed in the packaged [`client-rules.json`](https://github.com/volcengine/OpenViking/blob/main/openviking_gateway/client-rules.json). When a client's title or summary requests show up as **New message** in the request log, or a real message is treated as housekeeping, add or remove entries in `client_rules`:

```json
{
  "gateway": {
    "enabled": true,
    "client_rules": {
      "auxiliary_patterns": {
        "add": ["You are the Acme title writer"],
        "remove": ["Create a short.{0,40}title"]
      },
      "noise_tags": { "add": ["acme-context"] }
    }
  }
}
```

In `gateway.conf`, `client_rules` sits at the root, without the `gateway` wrapper.

| List | Entries | What a match does |
| --- | --- | --- |
| `auxiliary_patterns` | Regular expressions, ignoring case | Each is matched separately against the Messages `system` field, the Responses `instructions` and the latest user message. A match makes the request **Housekeeping**. System and developer messages inside Chat Completions `messages` or Responses `input` are not checked, so for those clients match text in the user message. |
| `noise_patterns` | Regular expressions, case-sensitive | Ignored when the gateway reads the conversation: left out of request classification, the memory search and saved messages and tool results, but still sent to the model unchanged. A user message left empty is not a new user message, so the request continues the turn. |
| `noise_tags` | Tag names | `<tag …>…</tag>` blocks, in any letter case, handled the same way as `noise_patterns`. |
| `plugin_tags` | Tag names | `<tag` in the system prompt or a client message marks an OpenViking memory plugin in the client. For the rest of that conversation the gateway only forwards requests: no memory, no OpenViking tools, nothing saved (see [When both are in use](../guides/15-gateway.md#when-both-are-in-use)). Add only a tag the plugin itself writes. |

- **`add` appends to the packaged list and `remove` takes entries out of it.** Write a removal exactly as it appears in `client-rules.json`. A removal that matches nothing, for example after an upgrade changed the entry, is ignored with a startup warning. The packaged list cannot be replaced as a whole, so entries added in later releases keep working.
- **Match text only the client writes.** Use a full sentence from the client's own prompt rather than a short phrase. A user message that matches makes its whole turn housekeeping, so none of it is searched for or saved; a pattern that also matches the main conversation's system prompt does this to every request. Anchor a noise pattern to the whole message (`"\\A…\\Z"` in JSON) so it never cuts text out of a real message.
- **Changes take effect at startup.** Restart the server or the standalone gateway after editing.

## Context profile settings {#context-profile-settings}

In Studio: **Profiles** → a profile. The tables follow the editor's sections, with the Studio label first and then the name in the management API, which calls context profiles `policies`. **Name** (`name`, default `Default`) is the profile's display name; what each section does is explained in [Context profiles](08-context-profiles.md#context-profiles). **Let clients override settings** (`client_overrides`, default `true`) lets clients change every other setting for a new conversation with the `X-OpenViking-Context` header; see [Let clients override settings](08-context-profiles.md#client-overrides).

### Recall memory {#profile-recall}

Explained in [Recall memory](08-context-profiles.md#recall-memory).

| Studio label | API name | Default | Range | Meaning |
| --- | --- | --- | --- | --- |
| Recall memory | `recall` | `true` | | Search memory for each new user message. |
| User profile at conversation start | `profile` | `true` | | Provide the user profile when a conversation starts, independently of recall. |
| Opening context budget | `profile_max_tokens` | `4000` | 0–32,000 | Separate budget for the profile and catalogs; 0 leaves them out. Catalogs need the OpenViking `read` tool (`openviking_read`). |
| Show recall summary | `show_recall` | `false` | | Start the reply with a summary of what OpenViking added, or why recall failed. The model never sees it. |
| Sources | `context_types` | `memory`, `resource`, `skill` | At least one while recall is on (checked by Studio only) | What to search: memories, resources, skills. |
| Budget per message | `max_tokens` | `1600` | 64–32,000 | Most tokens added to one message. |
| Budget per context window | `session_max_tokens` | `30000` | 0 or more | Most tokens added within one context window; resets after each compaction. 0 turns recall off. |
| Relevance threshold | `score_threshold` | `0.35` | 0–1 | Lowest relevance score accepted. |
| Time limit | `recall_timeout` | `2` (seconds) | More than 0, up to 30 | How long a search may take before the message goes on without memory. |
| Query length | `query_max_chars` | `8000` | 3–32,000 | Characters of the message used as the search query. |
| Limit by category | `quotas` | `{}` (off) | Keys: `events`, `entities`, `preferences`, `experiences`, `resources`, `skills`; when set, at least one above 0 (checked by Studio only) | Most entries per category; only categories above 0 are searched. |

The management API saves `context_types` and the `quotas` keys without checking them. An unknown `quotas` key makes every recall fail, because OpenViking rejects it.

### Save conversations {#profile-saving}

Explained in [Save conversations](08-context-profiles.md#save-conversations).

| Studio label | API name | Default | Range | Meaning |
| --- | --- | --- | --- | --- |
| Save conversations | `capture` | `true` | | Save finished turns to OpenViking. |
| Save the latest reply after | `idle_seconds` | `600` (seconds) | 1 or more | Quiet time before the last turn is saved and the session committed. |
| Commit after | `commit_tokens` | `20000` | 1 or more | Tokens waiting in the session that trigger a commit. |
| Keep recent messages | `keep_recent_messages` | `10` | 0–1,000 | Messages left in the session after a commit. |

### Long conversations {#profile-long-conversations}

Explained in [Long conversations in the gateway](05-long-conversations.md#long-conversations).

| Studio label | API name | Default | Range | Meaning |
| --- | --- | --- | --- | --- |
| Compaction | `compaction` | `true` | | Replace the earlier part of a conversation with a summary near the context window. |
| Compact at | `compaction_threshold` | `0.9` | 0.5–0.98 | Share of the context window that triggers compaction. |
| Summary length | `summary_max_tokens` | `8000` | 1,000–32,000 | Most tokens in one summary. |
| Default context window | `context_window` | Not set (1,000,000 assumed) | 1,024 or more | Window used when the upstream does not list the model; see [Set the model's context window](05-long-conversations.md#context-window). |
| Agent-managed context windows | `agent_windows` | `false` | | Experimental. Let the model start new context windows itself; needs OpenViking tools. |
| Soft reminder at | `window_soft_ratio` | `0.7` | 0.3–0.95, below `window_hard_ratio` | Share of the window at which the model is reminded to start a new window soon. |
| Hard reminder at | `window_hard_ratio` | `0.85` | 0.4–0.97 | Share of the window at which the model is told to start a new window now. |

### OpenViking tools {#profile-tools}

Explained in [Choose tools](04-openviking-tools.md#choose-tools) and [Limits](04-openviking-tools.md#limits).

| Studio label | API name | Default | Range | Meaning |
| --- | --- | --- | --- | --- |
| OpenViking tools | `gateway_tools` | `true` | | Offer OpenViking tools for Chat Completions, full-history Responses and Anthropic Messages. |
| Tools (unchecked boxes) | `disabled_tools` | The 8 tools that change data | OpenViking tool names, without `openviking_` | Tools new conversations do not get; every other available tool is offered, including ones added later. |
| Show tool calls | `show_tool_calls` | `true` | | Add a one-line notice to the reply for each OpenViking tool call. |
| Rounds per request | `tool_max_rounds` | `null` (no limit) | 1 or more | Tool rounds after which further OpenViking calls are refused. |
| Time limit per call | `tool_timeout_seconds` | `30` (seconds) | More than 0, up to 120 | A slower call returns an error to the model. |
| Result size | `tool_result_bytes` | `65536` (bytes) | 1,024–1,048,576 | Longer results are cut off before the model sees them. |
| Total time | `tool_total_seconds` | `null` (no limit) | More than 0 | Seconds after which a request still using tools fails. |
| Token budget | `tool_total_tokens` | `null` (no limit) | 1,024 or more | Estimated tokens tool calls and results may add to one request before further OpenViking calls are refused. |

API requests that omit `disabled_tools` get the default list; an empty list with `gateway_tools` on offers every available tool.

## Upstream settings {#upstream-settings}

In Studio: **Upstreams** → an upstream. The tables follow the editor's four sections; what each field does is explained in [Upstreams](07-upstreams.md#upstreams).

### Endpoint {#upstream-endpoint}

| Studio label | API name | Default | Accepted values | Meaning |
| --- | --- | --- | --- | --- |
| Name | `name` | — (required) | | Display name. |
| Provider | `vendor` | `generic` | `generic`, `anthropic`, `openai`, `deepseek`, `ark` (Volcano Engine Ark), `byteplus` (BytePlus ModelArk) | Studio offers only the protocols a provider supports; the API accepts any pair. |
| Protocol | `protocol` | — (required) | `anthropic` (Anthropic Messages), `chat` (Chat Completions), `responses` (Responses) | The API this upstream speaks. |
| Base URL | `base_url` | — (required) | HTTP(S) URL | Provider address; defaults are in [What an upstream is](07-upstreams.md#upstreams) and path rules in [Endpoint](07-upstreams.md#endpoint). |
| Append protocol endpoint path | `append_endpoint_path` | `true` | | Appends the selected protocol's path; when false, uses `base_url` as the complete request URL. |

### Credentials {#upstream-credentials}

| Studio label | API name | Default | Accepted values | Meaning |
| --- | --- | --- | --- | --- |
| Who provides the API key | `auth_mode` | `managed` | `managed` (the gateway holds the API key), `passthrough` (each client sends its own key) | See [Credentials](07-upstreams.md#credentials). |
| Send the key as | `key_header` | `authorization` | `authorization` (Bearer), `api-key`, `x-api-key` | Ignored when `base_url` contains `{api_key}`. |
| API key | `api_key` | empty | | Write-only; see [Credentials](07-upstreams.md#credentials). |
| Extra headers | `headers` | `{}` | Header name → value | Write-only; see [Credentials](07-upstreams.md#credentials). |
| This key is a Coding Plan or subscription key | `coding_plan` | `false` | | Rejects requests to this upstream unless allowed. |
| Allow this plan through the gateway | `allow_coding_plan` | `false` | | Allows use of a key marked as Coding Plan. |

### Models {#upstream-models}

| Studio label | API name | Default | Accepted values | Meaning |
| --- | --- | --- | --- | --- |
| Models | `models` | `[]` | Model names | Model names served; empty accepts any name. |
| Model aliases | `aliases` | `{}` | Name clients use → model sent to the upstream | |
| Context windows | `context_windows` | `{}` | Model → tokens, each at least 1,024 | Each model's context window; see [Set the model's context window](05-long-conversations.md#context-window). |

### Routing {#upstream-routing}

| Studio label | API name | Default | Accepted values | Meaning |
| --- | --- | --- | --- | --- |
| Priority | `priority` | `0` | Integer | Higher values win for new conversations. |
| Enabled | `enabled` | `true` | | A turned-off upstream receives no requests. |
| Allow OpenViking tools | `allow_gateway_tools` | `true` | | Whether OpenViking tools can be offered through this upstream. |
| Restore reasoning the client drops | `replay_reasoning` | `null` (follows the provider) | `null`, `true`, `false` | Puts reasoning the client dropped back on earlier replies. `null` means on for `deepseek`, `ark` and `byteplus` and off for other providers; `true` or `false` overrides that. Studio saves `null` while the switch matches the provider default. |
| Minimum cacheable prompt | `cache_min_tokens` | `1024` | 0 or more | Ark and ModelArk only; affects how cache eligibility is reported. |

## Next steps {#next-steps}

- [Standalone deployment](../guides/22-gateway-operations.md#standalone-deployment): create `gateway.conf` and run the gateway on its own.
- [Enable it in the server](../guides/22-gateway-operations.md#enable-it-in-the-server): turn on the embedded gateway in `ov.conf`.
- [Context profiles](08-context-profiles.md) and [Upstreams](07-upstreams.md): what these settings do, in Studio terms.
- [Management API](../api/25-gateway.md): gateway key fields, every management operation, the access rules and the error reasons.
