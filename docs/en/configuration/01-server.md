# Server Configuration

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

## Configuration Structure

```json
{
  "embedding": {},
  "vlm": {},
  "query_planner": {},
  "rerank": {},
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

Optional sections use their defaults when omitted. Unknown fields are rejected.

## Top-Level Settings

| Setting | Type / values | Default | Purpose |
|---|---|---|---|
| `default_account` | string | `"default"` | Default account for the service context |
| `default_user` | string | `"default"` | Default user for the service context |
| `embedding` | object | built-in local dense model | Dense, sparse, and hybrid embedding; defaults to `local` / `bge-small-zh-v1.5-f16` |
| `vlm` | object | empty config | Content understanding, summaries, and memory extraction; configure a working model before using these capabilities |
| `query_planner` | object / `null` | `null` | Retrieval intent model; falls back to `vlm` |
| `rerank` | object | disabled | Retrieval result reranking |
| `retrieval` | object | see below | Ranking and intent-analysis behavior |
| `grep` | object | built-in defaults | Text search engine |
| `storage` | object | local | Workspace, file system, and vector database |
| `queue_workers` | object | see below | Runtime concurrency for QueueFS consumer workers |
| `server` | object | local development | HTTP, authentication, uploads, and observability |
| `memory` | object | see below | Memory and skill extraction on session commit |
| `parsers` | object | parser defaults | PDF, code, image, audio, video, and text parsing |
| `semantic` | object | built-in defaults | Abstract and overview generation limits |
| `parser_api` | object | disabled | Third-party file parser API |
| `connector` | object | disabled | External Connector ingestion service |
| `encryption` | object | disabled | File and secret encryption |
| `git` | object | local | Version backend: `local` or `s3` |
| `log` | object | console | Log level, format, and file output |
| `telemetry` | object | disabled | OpenTelemetry tracing |
| `oauth` | object | disabled | MCP OAuth 2.1 |
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

### `rerank`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `provider` | `vikingdb`, `cohere`, `openai`, `litellm` / `null` | `null` | Rerank service; inferred from credentials when omitted |
| `model` | string / `null` | `null` | OpenAI-compatible or LiteLLM rerank model |
| `threshold` | number | `0.1` | Minimum score considered relevant |
| `max_input_tokens` | integer; `0` or `>= 128` | `0` | Maximum estimated tokens per query-document pair; `0` disables truncation |

Rerank has no separate `enabled` field. It becomes available when the required provider credentials are configured.

## Retrieval Settings

```json
{
  "retrieval": {
    "hotness_alpha": 0,
    "score_propagation_alpha": 1,
    "enable_intent": true
  }
}
```

### `retrieval`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `hotness_alpha` | number, `0`–`1` | `0` | Hotness score weight; `0` disables it |
| `score_propagation_alpha` | number, `0`–`1` | `1` | Child-result score weight in hierarchical retrieval |
| `enable_intent` | boolean | `true` | Run intent analysis/query planning when `session_id` is present |

Search and Find requests default to `limit: 10`; override the limit on each API or SDK request. `retrieval.enable_intent` controls LLM query planning for session-aware Search, while result reranking is enabled only when `rerank` has a usable provider configuration.

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
    }
  }
}
```

### `storage`

| Field | Type / common values | Default | Purpose |
|---|---|---|---|
| `workspace` | path | `"./data"` | OpenViking workspace |
| `agfs.backend` | `local`, `memory`, `s3` | `local` | File and metadata backend |
| `vectordb.backend` | `local`, `cuvs`, `http`, `volcengine`, `vikingdb` | `local` | Vector database backend |
| `vectordb.dimension` | integer | follows Embedding | Vector collection dimension |
| `skip_process_lock` | boolean | `false` | Skip the workspace process lock; use only when accepting concurrent-write risk |

Remote backends also require endpoint, bucket/collection, credentials, and timeout fields. See [Configuration](../guides/01-configuration.md#storage) for complete examples.

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

### `queue_workers.session_commit`

| Field | Type | Default | Description |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | Number of SessionCommit jobs consumed concurrently; must be greater than `0`; requires a server restart after changes |

## HTTP Server Settings

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "workers": 1,
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
| `timeout_keep_alive` | integer (seconds) | `5` | Idle HTTP keep-alive timeout; raise it above the upstream's idle-connection lifetime |
| `auth_mode` | `dev`, `api_key`, `trusted` / `null` | `null` | Auth mode; null is inferred from `root_api_key` |
| `root_api_key` | string / `null` | `null` | Root key; setting it defaults auth to `api_key` |
| `cors_origins` | string[] | `["*"]` | Allowed origins |
| `profile_enabled` | boolean | `false` | Allow performance profiles |
| `with_bot` | boolean | `false` | Enable the VikingBot API proxy |
| `bot_api_url` | URL | `http://localhost:18790` | VikingBot OpenAPI endpoint |
| `public_base_url` | URL / `null` | `null` | Externally visible base URL |
| `upload_signed_ttl_seconds` | integer | `600` | Signed upload URL lifetime |
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
    "link_enabled": false,
    "v2_lock_retry_interval_seconds": 0.2,
    "v2_lock_max_retries": 0
  }
}
```

### `memory`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `custom_templates_dir` | path | `""` | Additional memory template directory |
| `experimental_memory_switch` | boolean | `false` | Enable experimental templates |
| `eager_prefetch` | boolean | `true` | Search and read memories before extraction |
| `prefetch_search_topn` | integer, `>= 1` | `5` | Results read during prefetch |
| `extraction_enabled` | boolean | `true` | Extract long-term memories on session commit |
| `session_skill_extraction_enabled` | boolean | `false` | Also extract reusable skills |
| `link_enabled` | boolean | `false` | Generate and resolve memory links |
| `v2_lock_retry_interval_seconds` | number, `>= 0` | `0.2` | Memory-lock retry interval |
| `v2_lock_max_retries` | integer, `>= 0` | `0` | Retry limit; `0` means unlimited |

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
    "excel": {},
    "html": {},
    "text": {},
    "directory": {},
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

| Setting | Purpose |
|---|---|
| `pdf` | PDF text, image, and layout parsing |
| `code` | Repository file types, ignore rules, and network safety |
| `image` | Image understanding and OCR |
| `audio`, `video` | Audio/video parsing |
| `markdown`, `html`, `text` | Text document chunking |
| `excel` | Workbook parsing and chunking |
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
