# Server Configuration

For initial setup, run `openviking-server init`, then run `openviking-server doctor` after saving the configuration.

The OpenViking server and embedded Python SDK mode read `ov.conf`. The default path is:

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
| `default_account` | string | `"default"` | Default account in embedded SDK mode |
| `default_user` | string | `"default"` | Default user in embedded SDK mode |
| `embedding` | object | empty config | Dense, sparse, and hybrid embedding; configure a working model before using these capabilities |
| `vlm` | object | empty config | Content understanding, summaries, and memory extraction; configure a working model before using these capabilities |
| `query_planner` | object / `null` | `null` | Retrieval intent model; falls back to `vlm` |
| `rerank` | object | disabled | Retrieval result reranking |
| `retrieval` | object | see below | Ranking and intent-analysis behavior |
| `grep` | object | built-in defaults | Text search engine |
| `storage` | object | local | Workspace, file system, and vector database |
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
| `auto_generate_l0` | boolean | `true` | Generate an abstract when missing |
| `auto_generate_l1` | boolean | `true` | Generate an overview when missing |
| `default_search_mode` | `"fast"` / `"thinking"` | `"thinking"` | Default search mode |
| `default_search_limit` | integer | `3` | Default result count |
| `output_language_override` | string | `""` | Force summary/memory language; empty means auto-detect |
| `allow_private_networks` | boolean | `false` | Allow fetching private-network resources |

## Model Settings

API-based `embedding`, `vlm`, `query_planner`, and `rerank` models share common fields:

```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "text-embedding-3-large",
      "dimension": 3072,
      "input": "text",
      "encoding_format": "float",
      "api_key": "<embedding-api-key>"
    }
  },
  "vlm": {
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "api_key": "<vlm-api-key>",
    "temperature": 0,
    "max_retries": 3
  },
  "query_planner": {
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "api_key": "<vlm-api-key>"
  },
  "rerank": {
    "provider": "cohere",
    "model": "rerank-v3.5",
    "api_key": "<rerank-api-key>",
    "threshold": 0.1,
    "max_input_tokens": 0
  }
}
```

| Field | Type / common values | Default | Purpose |
|---|---|---|---|
| `provider` | `openai`, `volcengine`, `azure`, `ollama`, etc. | module-specific | Model service |
| `model` | string | none | Model name or endpoint ID |
| `api_base` | URL | provider default | Model endpoint |
| `api_key` | string | `null` | Model credential |
| `api_version` | string | provider default | API version for providers such as Azure |
| `extra_headers` | object | `{}` | Additional request headers |
| `extra_request_body` | object | `null` | Additional VLM request fields; Embedding uses `extra_body` |
| `timeout` | number, seconds | module default | VLM and Rerank request timeout |
| `max_retries` | integer, `>= 0` | module default | Retry count |

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
  },
  "default_search_mode": "thinking",
  "default_search_limit": 3
}
```

### `retrieval`

| Field | Type / values | Default | Purpose |
|---|---|---|---|
| `hotness_alpha` | number, `0`–`1` | `0` | Hotness score weight; `0` disables it |
| `score_propagation_alpha` | number, `0`–`1` | `1` | Child-result score weight in hierarchical retrieval |
| `enable_intent` | boolean | `true` | Run intent analysis/query planning when `session_id` is present |

### `default_search_mode`

| Value | Behavior |
|---|---|
| `"fast"` | Vector retrieval only |
| `"thinking"` | Vector retrieval plus LLM query planning/reranking |

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
      "backend": "local",
      "dimension": 3072
    }
  }
}
```

### `storage`

| Field | Type / common values | Default | Purpose |
|---|---|---|---|
| `workspace` | path | `"./data"` | OpenViking workspace |
| `agfs.backend` | `local`, `memory`, `s3` | `local` | File and metadata backend |
| `vectordb.backend` | `local`, `cuvs`, `http`, `volcengine`, `vikingdb`, `qdrant`, `opengauss` | `local` | Vector database backend |
| `vectordb.dimension` | integer | follows Embedding | Vector collection dimension |
| `skip_process_lock` | boolean | `false` | Skip the workspace process lock; use only when accepting concurrent-write risk |

Remote backends also require endpoint, bucket/collection, credentials, and timeout fields. See [Configuration](../guides/01-configuration.md#storage) for complete examples.

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
| `auth_mode` | `dev`, `api_key`, `trusted` / `null` | `null` | Auth mode; null is inferred from `root_api_key` |
| `root_api_key` | string / `null` | `null` | Root key; setting it defaults auth to `api_key` |
| `cors_origins` | string[] | `["*"]` | Allowed origins |
| `profile_enabled` | boolean | `false` | Allow performance profiles |
| `with_bot` | boolean | `false` | Enable the VikingBot API proxy |
| `bot_api_url` | URL | `http://localhost:18790` | VikingBot OpenAPI endpoint |
| `public_base_url` | URL / `null` | `null` | Externally visible base URL |
| `upload_signed_ttl_seconds` | integer | `600` | Signed upload URL lifetime |
| `temp_upload.default_mode` | `"local"` / `"shared"` | `"local"` | Temporary upload storage |
| `api_key_hashing_enabled` | boolean | `false` | Store API keys with Argon2id |
| `encryption_enabled` | boolean | `false` | Enable file-level AES encryption |

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
      "provider": "openai",
      "model": "text-embedding-3-large",
      "dimension": 3072,
      "api_key": "<embedding-api-key>"
    }
  },
  "vlm": {
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "api_key": "<vlm-api-key>"
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
