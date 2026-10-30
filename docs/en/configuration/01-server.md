# Server configuration fields

For initial setup, run `openviking-server init`, then run `openviking-server doctor` after saving the configuration.

The OpenViking server reads `ov.conf`. The default path is:

```text
~/.openviking/ov.conf
```

Use an environment variable or startup option to select another file:

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
openviking-server --config /path/to/ov.conf
```

The server reads the file at startup. Restart the server after changing models, retrieval, storage, or `server` settings, then run `openviking-server doctor`.

Studio's **Server configuration** page offers **Save and restart** for a single-worker `openviking-server` process. The existing ROOT-only `GET /api/v1/admin/configuration?source=file` includes restart capability and instance identity. Only ROOT can request a restart (`POST /api/v1/admin/restart`, body `{"revision":"<saved file revision>"}`). The server validates the current file and its revision, returns HTTP 202, drains active requests, stops its managed Bot, then replaces itself using the original interpreter, arguments, environment and working directory. Studio waits for a new service instance before reporting success. **Save configuration** alone continues to save without restarting.

Multi-worker or embedded ASGI applications report restart as unsupported; restart these through the deployment platform. Startup can still fail because of external dependencies even after schema validation. If the address, port or ROOT credential changes, update Studio's connection settings to reconnect. Account and cluster overrides continue to take precedence over file defaults.

Configurations containing environment references are read-only in the Studio form. Use file mode to edit the original text; quoted/unquoted references, escapes and formatting are saved as submitted. Windows `%VAR%` references and dollar/percent literals (including `\u0024` and `\u0025`) also use file mode to avoid introducing new references during form serialization.

## Configuration Structure

This outline shows common top-level groups, not a runnable configuration. For a first deployment, start with the [minimal example](#minimal-example) and supply your models and credentials. Merge later snippets into the same `ov.conf`.

```json
{
  "embedding": {},
  "vlm": {},
  "query_planner": {},
  "rerank": {},
  "rules": {},
  "retrieval": {},
  "storage": {},
  "server": {},
  "memory": {},
  "parsers": {},
  "encryption": {},
  "log": {},
  "telemetry": {}
}
```

Optional sections use their defaults when omitted. Unknown fields in `ov.conf` and persisted account settings are ignored for upgrade compatibility, except in the `gateway` section. Known fields still validate types and values. Misspelled field names are also ignored, but the server logs a warning listing every field it did not apply.

## Top-Level Settings

| Setting | Type / values | Default | Purpose |
|---|---|---|---|
| `default_account` | string | `"default"` | Default account for the service context |
| `default_user` | string | `"default"` | Default user for the service context |
| `embedding` | object | built-in local dense model | Dense, sparse, and hybrid embedding; defaults to `local` / `bge-small-zh-v1.5-f16` |
| `vlm` | object | empty config | Content understanding, summaries, and memory extraction; configure a working model before using these capabilities |
| `query_planner` | object / `null` | `null` | Model for retrieval intent analysis and recall rewriting. Falls back to `vlm` when omitted or empty; recall rewrite in `auto` mode requires a configured cluster `query_planner` or an account-level `query_planner` or `vlm` override |
| `rerank` | object | disabled | Retrieval result reranking |
| `rules` | object | empty config | Optional dedicated models for rule compilation and applicability matching |
| `retrieval` | object | see below | Ranking and intent-analysis behavior |
| `grep` | object | built-in defaults | Text search engine |
| `glob` | object | built-in defaults | Path glob engine |
| `storage` | object | local | Workspace, file system, and vector database |
| `queue_workers` | object | see below | Runtime concurrency for QueueFS consumer workers |
| `server` | object | local development | HTTP, authentication, uploads, and observability |
| `memory` | object | see below | Memory and skill extraction on session commit |
| `parsers` | object | parser defaults | PDF, code, image, audio, video, and text parsing |
| `semantic` | object | built-in defaults | Abstract and overview generation limits |
| `parser_api` | object | disabled | Third-party file parser API |
| `compile_api` | object | disabled | External Compile task API |
| `connector` | object | disabled | External Connector ingestion service |
| `encryption` | object | disabled | File and secret encryption |
| `git` | object | local | Version backend: `local` or `s3` |
| `log` | object | console | Log level, format, and file output |
| `telemetry` | object | disabled | OpenTelemetry tracing |
| `oauth` | object | disabled | MCP OAuth 2.1 |
| `gateway` | object | disabled | OpenViking Gateway, the model API gateway that adds OpenViking memory to API-key clients. Unknown keys in this section are errors. See the [configuration reference](../gateway/13-configuration.md#configuration-reference) |
| `prompts` | object | built-in templates | Custom prompt template directory |
| `ingest` | object | built-in defaults | Conversation-log ingestion |
| `output_language_override` | string | `""` | Force summary/memory language; empty means auto-detect |
| `allow_private_networks` | boolean | `false` | Allow fetching private-network resources |

`auto_generate_l0`, `auto_generate_l1`, `default_search_mode`, and `default_search_limit` are deprecated compatibility fields. They are accepted when loading older configuration files but have no runtime effect.

## Model Settings

API-based `embedding`, `vlm`, `query_planner`, and `rerank` configurations reuse some field names, but each module has its own schema. Use only fields supported by the applicable module below.

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "<your-ark-api-key>",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-code-preview-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<your-ark-api-key>",
    "temperature": 0,
    "max_retries": 3,
    "thinking": false
  },
  "query_planner": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-code-preview-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<your-ark-api-key>",
    "thinking": false
  },
  "rerank": {
    "provider": "vikingdb",
    "ak": "<your-volcengine-ak>",
    "sk": "<your-volcengine-sk>",
    "host": "api-vikingdb.vikingdb.cn-beijing.volces.com",
    "model_name": "doubao-seed-rerank",
    "model_version": "251028",
    "threshold": 0.1,
    "max_input_tokens": 0
  }
}
```

| Field / path | Applies to | Purpose |
|---|---|---|
| `provider`, `model`, `api_base`, `api_key` | Embedding, VLM, Query Planner, Rerank | Model service, endpoint, and credential |
| `api_version` | Embedding, VLM, Query Planner | API version for providers such as Azure |
| `extra_headers` | Embedding, VLM, Query Planner, Rerank | Additional request headers |
| `extra_request_body` | VLM, Query Planner | Additional completion request fields |
| `extra_body` | `embedding.dense` / `sparse` / `hybrid` | Additional embedding request fields |
| `timeout` | VLM, Query Planner, Rerank | Per-request timeout in seconds |
| `embedding.max_retries`, `vlm.max_retries`, `query_planner.max_retries` | Embedding, VLM, Query Planner | Retry count; Rerank has no `max_retries` field |

### `embedding.dense`

| Field | Type / values | Purpose |
|---|---|---|
| `provider` | `openai`, `volcengine`, `azure`, `ollama`, `local`, etc. | Dense embedding service |
| `dimension` | integer, `> 0` | Vector dimension; must match model output and existing collections |
| `input` | `"text"` / `"multimodal"` | Input type |
| `encoding_format` | `"float"` / `"base64"` | OpenAI-compatible vector encoding |

Changing the model or `dimension` can make existing vector collections incompatible and may require migration or reindexing.

### `rules`

Optional models and lifecycle settings for compiled Rules:

```json
{
  "rules": {
    "jev_rule_matcher": {
      "provider": "typesafe",
      "api_key": "${JEV_API_KEY}"
    },
    "compiler": {
      "provider": "openai",
      "model": "gpt-4o"
    },
    "compiler_context_window": 0
  }
}
```

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `jev_rule_matcher` | Jev Noul config / `null` | `null` | Jev Noul matcher for Scenario filtering and Rule applicability |
| `compiler` | VLM config / `null` | `null` | Dedicated Rule compiler |
| `compiler_context_window` | `0` or integer `>= 2048` | `0` | Compiler context window; `0` auto-detects with a conservative fallback |
| `scenario_irrelevance_threshold` | number, `0.5`-`1.0` | `0.9` | Skip a Scenario only at or above this irrelevance probability |
| `archived_retention_days` | integer, `>= 1` | `30` | Days before archived Rules are permanently removed |
| `archived_cleanup_interval_seconds` | integer, `>= 60` | `2592000` | Archived-Rule cleanup interval |
| `archived_cleanup_enabled` | boolean | `true` | Enable periodic archived-Rule cleanup |

Rule compilation always calls the dedicated compiler, or the account VLM fallback,
with `thinking=true`, overriding its configured default. Use a model that supports
thinking mode, or compilation may fail.

#### `rules.jev_rule_matcher`

This block configures the independent Jev System One judgments used for Scenario
filtering and Rule applicability. It does not configure retrieval reranking.

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `provider` | `typesafe`, `vercel`, `openrouter`, `custom` | `typesafe` | Select endpoint and model defaults |
| `api_key` | string | required | API key for the selected provider |
| `api_base` | URL / `null` | provider default | Complete System One request URL; an explicit value is used unchanged |
| `model` | string / `null` | provider default | Model name or endpoint ID; required for `custom` |
| `timeout` | number | `30` | HTTP request timeout in seconds |
| `extra_headers` | object / `null` | `null` | Additional HTTP request headers |
| `log_payloads` | boolean | `false` | Log complete requests and responses; these may contain task and Rule content |

| Provider | Default request URL | Default model | Notes |
|---|---|---|---|
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `jev-latest` | Direct TypeSafe access |
| `vercel` | `https://ai-gateway.vercel.sh/typesafe/v1/systemone` | `typesafe-ai/jev` | Vercel AI Gateway TypeSafe-compatible API |
| `openrouter` | `https://openrouter.ai/api/v1/systemone` | `jev-latest` | OpenRouter System One API |
| `custom` | none | none | Requires both `api_base` and `model` |

`api_base` and `model` override provider defaults independently. An explicit
`api_base` is the complete POST URL and is never modified, including for `custom`.
`extra_headers` extends provider headers and can override a provider default.
Rule matching always uses independent Noul judgments, so it has no `mode`,
`threshold`, or `max_input_tokens` setting. Configure Scenario filtering with
`rules.scenario_irrelevance_threshold`; Rule applicability uses the threshold supplied
as `rule_probability_threshold` in the Rule search request (default `0.65`).

Rule status is `active`, `paused`, or `archived`. `PAUSE` suspends retrieval without
expiry; `ARCHIVE` starts retention from `archived_at`. `RESTORE` reactivates a paused
Rule. Compilation considers active and paused Rules; archived Rules are logically
deleted, are not compiler candidates, and cannot be restored through Rule compilation.
`ARCHIVE` requires explicit deletion, discard, or recycle-bin intent in the user's
instruction; non-deletion deactivation requests use `PAUSE`.

Rules live directly in `memories/rules/<scenario_id>/`: `abstract.md` stores Scenario
metadata and `<atomic_id>.md` stores each Rule. See
[Rules (Beta)](../concepts/17-rule-scenarios.md) for the storage format.

When `jev_rule_matcher` is unconfigured, unavailable, fails, or returns invalid
results, Scenario and Rule judgments pass the candidates without calling a VLM.
Scenario diagnostics record `decision=pass`.
Rules return `judge=pass`, a warning, and score `1.0` to pass the threshold; this score
is not a model-estimated applicability probability. Agents still check applicability
and exceptions against task facts. Compilation falls back from `compiler` to the
account VLM. The former `rule_matcher` setting has been removed and is no longer read.

Rule `applies_when` may be empty and defaults to an empty string. Empty means
applicable in every situation unless `exception` holds. Candidates with neither an
applicability condition nor an exception receive probability `1.0` directly.
The Compiler prefers faithful, non-empty, decidable conditions derived from source
objects, operations, prerequisites, and scenarios. It must not invent triggers or
discard source restrictions. Empty does not mean unknown or inapplicable.

### `rerank`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `provider` | `vikingdb`, `cohere`, `openai`, `litellm`, `jev` / `null` | `null` | Rerank service; inferred from credentials when omitted |
| `model` | string / `null` | `null` | OpenAI-compatible, LiteLLM, or Jev rerank model |
| `mode` | `noul`, `choice`, or `null` | `noul` | Jev rerank mode; `null` also uses `noul` |
| `threshold` | number | `0.1` | Minimum rerank score considered relevant; filters only model rerank scores, never vector scores, and a request `score_threshold` overrides it |
| `max_input_tokens` | integer; `0` or `>= 128` | `0` | Maximum estimated tokens per query-document pair; `0` disables truncation |
| `log_payloads` | boolean | `false` | Log complete rerank request and response payloads; may expose query and document content |

Rerank has no separate `enabled` field. It becomes available when the required provider credentials are configured.

`jev` supports direct TypeSafe access (`https://api.typesafe.ai/v1/systemone`, model `jev-latest`) and Vercel AI Gateway's TypeSafe-compatible endpoint (`https://ai-gateway.vercel.sh/typesafe/v1/systemone`, model `typesafe-ai/jev`) through the existing `api_base` and `model` fields; both speak the same protocol. For rerank, `api_base` may be either the service root or the complete System One URL; `/v1/systemone` is appended when absent. Its default `noul` mode scores each candidate independently. The optional `choice` mode compares all candidates and returns relative probabilities; set `threshold` to `0` when using it. Setting `provider` explicitly requires the credentials that provider needs: `ak` and `sk` for `vikingdb`, `api_key` for `cohere` and `jev`, `api_key` and `api_base` for `openai`, `model` for `litellm`. An incomplete block is rejected when the configuration loads.

## Retrieval Settings

```json
{
  "retrieval": {
    "enable_intent": true,
    "apply_rerank_to_context": false
  }
}
```

### `retrieval`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `enable_intent` | boolean | `true` | Run intent analysis/query planning when `session_id` is present |
| `apply_rerank_to_context` | boolean | `false` | Rerank `search(mode="context")` candidates with the configured `rerank` model |

Search and Find requests default to `limit: 10`; override the limit on each API or SDK request. `retrieval.enable_intent` controls LLM query planning for session-aware Search, while result reranking is enabled only when `rerank` has a usable provider configuration. Context mode recalls with Find's vector scores; with `retrieval.apply_rerank_to_context` and a usable `rerank`, it also reranks its merged candidates once against the original query and its expansions before quotas and the token budget apply, which adds one rerank call per request.

## Storage Settings

```json
{
  "storage": {
    "workspace": "./data",
    "skip_process_lock": false,
    "agfs": {
      "backend": "local"
    },
    "vectordb": {
      "backend": "local"
    },
    "parse_output": {
      "mode": "agfs"
    }
  },
  "parsers": {
    "download_temp_dir": "/data/openviking/parser-tmp"
  }
}
```

### `storage`

| Field | Type / common values | Default | Purpose |
|---|---|---|---|
| `workspace` | path | `"./data"` | OpenViking workspace |
| `agfs.backend` | `local`, `memory`, `s3` | `local` | File and metadata backend |
| `agfs.pathlock.provider` | `filesystem`, `memory`, `sqlite`, `cache` | `filesystem` | Path lock storage |
| `agfs.pathlock.sqlite_path` | absolute local file path | `{workspace}/_system/pathlock/pathlock.db` for `sqlite` | Shared SQLite lock database; optional override |
| `vectordb.backend` | `local`, `cuvs`, `http`, `volcengine`, `vikingdb` | `local` | Vector database backend |
| `vectordb.dimension` | integer | follows Embedding | Vector collection dimension |
| `parse_output.mode` | `agfs`, `local` | `agfs` | Backend for intermediate parser artifacts |
| `parse_output.local_root` | path or `null` | system temp directory | Root directory used by local parser artifacts |
| `skip_process_lock` | boolean | `false` | Skip the workspace process lock; use only when accepting concurrent-write risk |

For same-machine path locking on a local disk without recursive business-directory scans, set
`storage.agfs.pathlock` to `{"provider":"sqlite"}`. The database defaults to
`{storage.workspace}/_system/pathlock/pathlock.db`, resolved to an absolute path
from the final workspace when initializing the runtime binding. An explicit absolute
`sqlite_path` takes precedence. The database parent directory is created automatically.
Omitting `sqlite_path` or setting it to `null` also uses this default when constructing
`RAGFSBindingClient(config_path=..., config={"service_name": ...})` directly.
Every participating process must access the same actual local database file,
with the same top-level `service_name` and lock expiry setting. Containers on the
same host must share that local file; identical paths in separate container
filesystems refer to independent databases and do not coordinate locks.
SQLite stores authoritative lock tokens and atomically checks Exact/Tree conflicts
using its path index. Blocking database work runs off the async executor.
SQLite waits up to 100 ms per attempt for transient database writer contention,
including when the logical lock wait timeout is zero. The provider retries only
Busy, with three total attempts and 1 ms / 4 ms backoff. Database waits can total
about 300 ms, plus 5 ms backoff and execution/scheduling overhead. Exhausted
retries still report Busy to the manager; logical lock Conflict is not retried. WAL uses
`synchronous=FULL` to preserve committed lock tokens after a crash, at the cost of
additional synchronization I/O compared with `NORMAL`.
Lease heartbeats refresh owned paths in one SQLite transaction (bounded batches
for large leases). `stale_locks_removed` includes tokens recovered by committed
atomic SQLite/Redis acquisitions; failed transactions do not increment the count.

SQLite acquisitions completely delete expired tokens belonging to other owners
that conflict with the request: the target path, ancestor Tree locks, and all
descendants of a requested Tree lock. Cleanup and acquisition share one transaction,
so a previous owner cannot revive these tokens by refreshing them after takeover.
Opportunistic global cleanup is limited to 256 tokens per acquisition; conflicting
cleanup can exceed that limit and costs more for large expired subtrees.

Do not place the database on EFS, NFS, or SMB. SQLite WAL requires participants
on the same machine and is not supported on network filesystems; frequent lock
updates also add synchronization I/O. If `storage.workspace` is on EFS, the
default database path is on EFS too. Moving it to each container's local disk
creates independent lock authorities and cannot coordinate replicas.

For multiple containers or hosts sharing EFS, use shared Redis PathLock:
set `storage.agfs.pathlock.provider="cache"` and top-level `cache.provider="redis"`.
All replicas must use the same Redis deployment, top-level `service_name`, and
lock expiry setting. See the [shared Redis configuration example](../guides/01-configuration.md).
Shared QueueFS must also use a backend suitable for that deployment, such as
`storage.agfs.queuefs.backend="cache"`; changing PathLock alone does not change
the default SQLite queue backend.

Stop all participating processes before switching providers:
SQLite and filesystem lock files do not share a protocol. Do not delete the database,
WAL, or SHM files while participants are running. Crashed owners' tokens become
eligible for takeover after `lock_expire_secs` (default 30 seconds). This setting
does not change the workspace process lock or other backend concurrency constraints.

Remote backends also require endpoint, bucket/collection, credentials, and timeout fields. See [Configuration](../guides/01-configuration.md#storage) for complete examples.

`parse_output.mode=local` avoids writing parser intermediates to shared AGFS.
The same worker must commit the required bytes to the formal resource tree before
enqueueing downstream work. Artifacts are temporary and are removed after the
content commit; provision `local_root` with enough space for concurrent imports.

### `parsers`

| Field | Type / common values | Default | Purpose |
|---|---|---|---|
| `download_temp_dir` | path or `null` | system temp directory | Temporary directory for built-in parser downloads, Git checkouts, and archive extraction |

When configured, parsers create temporary directories with prefixes such as `ov_git_` and `ov_zip_` below this directory. When unset, the system temporary directory is used.

## Queue Worker Settings

### `queue_workers.external_parse`

| Field | Type | Default | Description |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | Number of complete ExternalParse jobs consumed concurrently; must be greater than `0`; requires a server restart after changes |

This setting controls queue-job concurrency. It is separate from `vlm.media.max_concurrent`, which limits audio/video VLM calls, and does not limit individual Understanding API HTTP requests.

### `queue_workers.add_resource`

| Field | Type | Default | Description |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | Number of complete AddResource jobs consumed concurrently; must be greater than `0`; requires a server restart after changes |
| `file_operation_concurrency` | integer | `16` | Maximum concurrent file-level commit and fallback comparison operations within one AddResource job; must be greater than `0`; requires a server restart after changes |
| `file_vectorization_concurrency` | integer | `8` | Number of files concurrently read, prepared, and enqueued within one directory AddResource job when `processing_mode="vectors_only"`; must be greater than `0`; values above the internal safety limit of `64` are capped; requires a server restart after changes |

`max_concurrent` controls independent AddResource jobs. `file_operation_concurrency` controls file commit and fallback comparison work within one AddResource job, while `file_vectorization_concurrency` controls files within one vectors-only directory job.

### `queue_workers.reindex`

| Field | Type | Default | Description |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | Number of complete Reindex root jobs consumed concurrently; must be greater than `0`; requires a server restart after changes |

This setting limits independent asynchronous reindex requests. URI-overlapping requests remain protected by path locks, while VLM and embedding work continue to use their respective concurrency limits.

### `queue_workers.session_commit`

| Field | Type | Default | Description |
|---|---|---:|---|
| `max_concurrent` | integer | `8` | Number of SessionCommit jobs consumed concurrently; must be greater than `0`; requires a server restart after changes |

### `queue_workers.external_task`

| Field | Type | Default | Description |
|---|---|---:|---|
| `max_concurrent` | integer | `10` | Number of external asynchronous tasks consumed concurrently; must be greater than `0`; requires a server restart after changes |

## Compile API Settings

| Field | Type | Default | Description |
|---|---|---:|---|
| `base_url` | string | `""` | External service base URL, including `http://` or `https://`; a non-empty value enables external Compile |
| `gateway_token` | string | `""` | Optional service credential used by OV to call the Compile Gateway |
| `http_timeout_seconds` | number | `10` | Timeout for one HTTP request |
| `poll_interval_ms` | integer | `30000` | External task polling interval |

When `base_url` is configured, OV sends the current user's OV API key in `X-API-Key`. It sends `X-Gateway-Token` only when `gateway_token` is configured.

## Reindex Settings

### `reindex`

| Field | Type | Default | Description |
|---|---|---:|---|
| `file_vectorization_concurrency` | integer | `8` | Number of files concurrently read, fingerprinted, and prepared by one resource/skill reindex task; must be greater than `0`; values above the internal safety limit of `64` are capped; requires a server restart after changes |

## HTTP Server Settings

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "workers": 1,
    "executor_threads": 0,
    "auth_mode": "dev",
    "cors_origins": ["http://localhost:5173"],
    "profile_enabled": false,
    "temp_upload": {
      "default_mode": "local"
    }
  }
}
```

### `server`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `host` | IP / hostname | `"127.0.0.1"` | Listen address |
| `port` | integer | `1933` | Listen port |
| `workers` | integer | `1` | Worker process count |
| `executor_threads` | non-negative integer | `0` | Maximum threads in each worker process's default asyncio executor; `0` uses Python's default sizing policy |
| `timeout_keep_alive` | integer (seconds) | `5` | Idle HTTP keep-alive timeout; raise it above the upstream's idle-connection lifetime |
| `auth_mode` | `dev`, `api_key`, `trusted` / `null` | `null` | Auth mode; null is inferred from `root_api_key` |
| `root_api_key` | string / `null` | `null` | Root key; setting it defaults auth to `api_key` |
| `cors_origins` | string[] | `["*"]` | Allowed origins |
| `profile_enabled` | boolean | `false` | Allow performance profiles |
| `with_bot` | boolean | `false` | Enable the VikingBot API proxy |
| `bot_api_url` | URL | `http://localhost:18790` | VikingBot OpenAPI endpoint |
| `public_base_url` | URL / `null` | `null` | Externally visible base URL |
| `upload_signed_ttl_seconds` | integer | `600` | Signed upload and download URL lifetime |
| `temp_upload.default_mode` | `"local"` / `"shared"` | `"local"` | Temporary upload storage |

### Encryption and API Key Hashing

File encryption and API key hashing are configured in the top-level `encryption` section, not under `server`:

```json
{
  "encryption": {
    "enabled": false,
    "api_key_hashing": {
      "enabled": false
    }
  }
}
```

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `encryption.enabled` | boolean | `false` | Enable file-level AES encryption |
| `encryption.api_key_hashing.enabled` | boolean | `false` | Store API keys with Argon2id |

See [Encryption](../guides/08-encryption.md) for provider and key-management settings.

### Authentication Modes

| Value | Use case |
|---|---|
| `dev` | Local-only development without API keys |
| `api_key` | Validate root/user/admin keys |
| `trusted` | Trust an upstream gateway to inject account/user identity |

## Memory Settings

```json
{
  "memory": {
    "custom_templates_dir": "",
    "experimental_memory_switch": false,
    "eager_prefetch": true,
    "prefetch_search_topn": 5,
    "extraction_enabled": true,
    "session_skill_extraction_enabled": false,
    "link_enabled": false
  }
}
```

### `memory`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `custom_templates_dir` | path | `""` | Custom memory template directory; a matching `memory_type` overrides the loaded template |
| `experimental_memory_switch` | boolean | `false` | Enable experimental templates |
| `eager_prefetch` | boolean | `true` | Search and read memories before extraction |
| `prefetch_search_topn` | integer, `>= 1` | `5` | Conversation-search results read for other mutable memory types. Entity/Preference maintenance reads the full deduplicated union of conversation and supplemental searches. Applies when `eager_prefetch` is enabled. |
| `extraction_enabled` | boolean | `true` | Extract long-term memories on session commit |
| `session_skill_extraction_enabled` | boolean | `false` | Also extract reusable skills |
| `link_enabled` | boolean | `false` | Generate and resolve memory links |

Automatic commits require a separate policy; enabling memory extraction does not make sessions commit automatically. See `memory.session_auto_commit` in [Configuration](../guides/01-configuration.md) and the [Sessions API](../api/05-sessions.md). For template loading order and when changes take effect, see the [Prompt Guide](../guides/10-prompt-guide.md).

## Parser Settings

Parsers live under `parsers`:

```json
{
  "parsers": {
    "pdf": {},
    "code": {
      "code_summary_mode": "ast",
      "extract_functions": true,
      "extract_classes": true,
      "max_token_limit": 50000
    },
    "image": {},
    "audio": {},
    "video": {},
    "markdown": {},
    "anydoc": {
      "enabled": true
    },
    "html": {},
    "text": {},
    "directory": {
      "preserve_structure": true,
      "max_files": null,
      "max_depth": 10,
      "max_concurrent": 4
    },
    "feishu": {
      "domain": "https://open.feishu.cn",
      "max_rows_per_sheet": 1000,
      "max_records_per_table": 1000,
      "download_images": true
    },
    "webfeed": {}
  }
}
```

`parsers.directory.max_files` defaults to `null`, meaning no file-count limit.
Set it to a positive integer to limit the number of files per directory import.

`parsers.directory.max_concurrent` is shared by all directory imports in the
server event loop. With the default value `4`, one directory can run four
Understanding jobs concurrently, while multiple concurrent directories still
run at most four in total.

`max_files` and `max_depth` apply when Understanding directory routing is enabled.
Each `DirectoryParser` scan applies these limits independently before submitting its
own Understanding requests. A nested ZIP starts a new directory scan and does not
share the outer scan's file-count or depth budget.
When Understanding is disabled, native OpenViking directory parsing does not apply
these two limits.

When a local directory is added through the client, the complete directory ZIP is
subject to the `/resources/temp_upload` size limit. After extraction,
`DirectoryParser` does not impose a common per-file byte limit. Each selected file
follows the limits and upload behavior of its assigned built-in parser or
Understanding API backend.

| Setting | Purpose |
|---|---|
| `pdf` | PDF text, image, and layout parsing |
| `code` | Repository file types, ignore rules, and network safety |
| `image` | Image understanding and OCR |
| `audio`, `video` | Audio/video parsing |
| `markdown`, `html`, `text` | Text document chunking |
| `anydoc` | Office and EPUB conversion; `enabled=false` rejects those formats |
| `directory` | Directory scanning and ignore rules |
| `feishu` | Feishu/Lark access and parsing |
| `webfeed` | Sitemap, RSS, and Atom ingestion |

Provider-, parser-, storage-, and encryption-specific fields are documented in [Configuration](../guides/01-configuration.md).

## Minimal Example

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "<your-ark-api-key>",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-code-preview-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<your-ark-api-key>",
    "thinking": false
  },
  "storage": {
    "workspace": "./data"
  },
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  }
}
```
