# Configuration

OpenViking uses a JSON configuration file (`~/.openviking/ov.conf`) for settings.

## Configuration File

Create `~/.openviking/ov.conf` in your project directory:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-pro-260215"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  },
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
  }
}
```

## Configuration Examples

<details>
<summary><b>Volcengine (Doubao Models)</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-250615",
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-2-0-pro-260215"
  }
}
```

</details>

<details>
<summary><b>OpenAI Models</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 3072,
      "model"    : "text-embedding-3-large"
    }
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4-vision-preview"
  }
}
```

</details>

## Configuration Sections

### embedding

Embedding model configuration for vector search, supporting dense, sparse, and hybrid modes.

#### Dense Embedding

```json
{
  "embedding": {
    "max_concurrent": 10,
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_concurrent` | int | Maximum concurrent embedding requests (`embedding.max_concurrent`, default: `10`) |
| `provider` | str | `"volcengine"`, `"openai"`, `"vikingdb"`, `"jina"`, `"voyage"`, or `"gemini"` |
| `api_key` | str | API key |
| `model` | str | Model name |
| `dimension` | int | Vector dimension. For Voyage, this maps to `output_dimension` |
| `input` | str | Input type: `"text"` or `"multimodal"` |
| `batch_size` | int | Batch size for embedding requests |

**Available Models**

| Model | Dimension | Input Type | Notes |
|-------|-----------|------------|-------|
| `doubao-embedding-vision-250615` | 1024 | multimodal | Recommended |
| `doubao-embedding-250615` | 1024 | text | Text only |

With `input: "multimodal"`, OpenViking can embed text, images (PNG, JPG, etc.), and mixed content.

**Supported providers:**
- `openai`: OpenAI Embedding API
- `volcengine`: Volcengine Embedding API
- `vikingdb`: VikingDB Embedding API
- `jina`: Jina AI Embedding API
- `voyage`: Voyage AI Embedding API
- `minimax`: MiniMax Embedding API
- `gemini`: Google Gemini Embedding API (text-only; requires `google-genai>=1.0.0`)

**minimax provider example:**

```json
{
  "embedding": {
    "dense": {
      "provider": "minimax",
      "api_key": "your-minimax-api-key",
      "model": "embo-01",
      "dimension": 1536,
      "query_param": "query",
      "document_param": "db",
      "extra_headers": {
        "GroupId": "your-group-id"
      }
    }
  }
}
```

**vikingdb provider example:**

```json
{
  "embedding": {
    "dense": {
      "provider": "vikingdb",
      "model": "bge_large_zh",
      "ak": "your-access-key",
      "sk": "your-secret-key",
      "region": "cn-beijing",
      "dimension": 1024
    }
  }
}
```

**jina provider example:**

```json
{
  "embedding": {
    "dense": {
      "provider": "jina",
      "api_key": "jina_xxx",
      "model": "jina-embeddings-v5-text-small",
      "dimension": 1024
    }
  }
}
```

Available Jina models:
- `jina-embeddings-v5-text-small`: 677M params, 1024 dim, max seq 32768 (default)
- `jina-embeddings-v5-text-nano`: 239M params, 768 dim, max seq 8192

Get your API key at https://jina.ai

**voyage provider example:**

```json
{
  "embedding": {
    "dense": {
      "provider": "voyage",
      "api_key": "pa-xxx",
      "api_base": "https://api.voyageai.com/v1",
      "model": "voyage-4-lite",
      "dimension": 1024
    }
  }
}
```

Supported Voyage text embedding models include:
- `voyage-4-lite`
- `voyage-4`
- `voyage-4-large`
- `voyage-code-3`
- `voyage-context-3`
- `voyage-3`
- `voyage-3.5`
- `voyage-3.5-lite`
- `voyage-finance-2`
- `voyage-law-2`

If `dimension` is omitted, OpenViking uses the model's default output dimension when creating the vector schema.

OpenViking also expects dense float vectors throughout storage and retrieval, so Voyage quantized output dtypes are not exposed in config.

**Local deployment (GGUF/MLX):** Jina embedding models are open-weight and available in GGUF and MLX formats on [Hugging Face](https://huggingface.co/jinaai). You can run them locally with any OpenAI-compatible server (e.g. llama.cpp, MLX, vLLM) and point the `api_base` to your local endpoint:

```json
{
  "embedding": {
    "dense": {
      "provider": "jina",
      "api_key": "local",
      "api_base": "http://localhost:8080/v1",
      "model": "jina-embeddings-v5-text-nano",
      "dimension": 768
    }
  }
}
```

**gemini provider example:**

> **Note:** Requires `pip install "google-genai>=1.0.0"`. For async batching: `pip install "openviking[gemini-async]"`.

```json
{
  "embedding": {
    "dense": {
      "provider": "gemini",
      "api_key": "your-google-api-key",
      "model": "gemini-embedding-2-preview",
      "dimension": 3072
    }
  }
}
```

Available Gemini embedding models:
- `gemini-embedding-2-preview`: 8192 token input limit, 1–3072 output dimension (MRL)
- `gemini-embedding-001`: 2048 token input limit, 1–3072 output dimension (MRL)
- `text-embedding-004`: 2048 token input limit, 768 output dimension (fixed)

Recommended dimensions: `768`, `1536`, or `3072` (default: `3072`).

Get your API key at https://aistudio.google.com/apikey

**Non-symmetric retrieval** (different task types for indexing vs. query):

```json
{
  "embedding": {
    "dense": {
      "provider": "gemini",
      "api_key": "your-google-api-key",
      "model": "gemini-embedding-2-preview",
      "dimension": 3072,
      "query_param": "RETRIEVAL_QUERY",
      "document_param": "RETRIEVAL_DOCUMENT"
    }
  }
}
```

Supported task types: `RETRIEVAL_QUERY`, `RETRIEVAL_DOCUMENT`, `SEMANTIC_SIMILARITY`, `CLASSIFICATION`, `CLUSTERING`, `CODE_RETRIEVAL_QUERY`, `QUESTION_ANSWERING`, `FACT_VERIFICATION`.

#### Sparse Embedding

> **Note:** Volcengine sparse embedding is supported starting from model `doubao-embedding-vision-250615`.

```json
{
  "embedding": {
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615"
    }
  }
}
```

#### Hybrid Embedding

Two approaches are supported:

**Option 1: Single hybrid model**

```json
{
  "embedding": {
    "hybrid": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-hybrid",
      "dimension": 1024
    }
  }
}
```

**Option 2: Combine dense + sparse**

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024
    },
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615"
    }
  }
}
```

### vlm

Vision Language Model for semantic extraction (L0/L1 generation).

```json
{
  "vlm": {
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `api_key` | str | API key |
| `model` | str | Model name |
| `api_base` | str | API endpoint (optional) |
| `thinking` | bool | Enable thinking mode for VolcEngine models (default: `false`) |
| `max_concurrent` | int | Maximum concurrent semantic LLM calls (default: `100`) |
| `extra_headers` | object | Custom HTTP headers (for OpenAI-compatible providers, optional) |
| `stream` | bool | Enable streaming mode (for OpenAI-compatible providers, default: `false`) |

**Available Models**

| Model | Notes |
|-------|-------|
| `doubao-seed-2-0-pro-260215` | Recommended for semantic extraction |
| `doubao-pro-32k` | For longer context |

When resources are added, VLM generates:

1. **L0 (Abstract)**: ~100 token summary
2. **L1 (Overview)**: ~2k token overview with navigation

If VLM is not configured, L0/L1 will be generated from content directly (less semantic), and multimodal resources may have limited descriptions.

**Custom HTTP Headers**

For OpenAI-compatible providers (e.g., OpenRouter), you can add custom HTTP headers via `extra_headers`:

```json
{
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o",
    "api_base": "https://openrouter.ai/api/v1",
    "extra_headers": {
      "HTTP-Referer": "https://your-site.com",
      "X-Title": "Your App Name"
    }
  }
}
```

Common use cases:
- **OpenRouter**: Requires `HTTP-Referer` and `X-Title` to identify your application
- **Custom proxies**: Add authentication or tracing headers
- **API gateways**: Add version or routing identifiers

**Streaming Mode**

For OpenAI-compatible providers that return SSE (Server-Sent Events) format responses, enable `stream` mode:

```json
{
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o",
    "api_base": "https://api.example.com/v1",
    "stream": true
  }
}
```

> **Note**: The OpenAI SDK requires `stream=true` to properly parse SSE responses. When using providers that force SSE format, you must set this option to `true`.

### feishu

Configuration for Feishu/Lark cloud document parsing. See [Resources](../api/02-resources.md) for supported URL patterns.

```json
{
  "feishu": {
    "app_id": "",
    "app_secret": "",
    "domain": "https://open.feishu.cn",
    "max_rows_per_sheet": 1000,
    "max_records_per_table": 1000
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `app_id` | str | Feishu app ID (can also be set via `FEISHU_APP_ID` env var) |
| `app_secret` | str | Feishu app secret (can also be set via `FEISHU_APP_SECRET` env var) |
| `domain` | str | Feishu API domain. Use `https://open.larksuite.com` for Lark international |
| `max_rows_per_sheet` | int | Maximum rows to import per spreadsheet sheet (default: `1000`) |
| `max_records_per_table` | int | Maximum records to import per bitable table (default: `1000`) |

**Dependency**: `pip install 'openviking[bot-feishu]'`

**Lark international**: For Lark URLs (`*.larksuite.com`), set `domain` to `https://open.larksuite.com`.

### code

Controls how code files are summarized via `code_summary_mode`. Both config formats are equivalent:

```json
{
  "code": {
    "code_summary_mode": "ast"
  }
}
```

```json
{
  "parsers": {
    "code": {
      "code_summary_mode": "ast"
    }
  }
}
```

Set `code_summary_mode` to one of:

| Value | Description | Default |
|-------|-------------|---------|
| `"ast"` | Extract AST skeleton (class names, method signatures, first-line docstrings, imports) for files ≥100 lines, skip LLM calls. **Recommended for large-scale code indexing** | ✓ |
| `"llm"` | Always use LLM for summarization (higher cost) | |
| `"ast_llm"` | Extract AST skeleton (with full docstrings) first, then pass it as context to LLM (highest quality, moderate cost) | |

AST extraction supports: Python, JavaScript/TypeScript, Rust, Go, Java, C/C++. Other languages, extraction failures, or empty skeletons automatically fall back to LLM.

See [Code Skeleton Extraction](../concepts/06-extraction.md#code-skeleton-extraction-ast-mode) for details.

### rerank

Reranking model for search result refinement.

```json
{
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  }
}
```

**OpenAI-compatible provider (e.g. DashScope qwen3-rerank):**

```json
{
  "rerank": {
    "provider": "openai",
    "api_key": "your-api-key",
    "api_base": "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
    "model": "qwen3-rerank",
    "threshold": 0.1
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | str | `"volcengine"` or `"openai"` |
| `api_key` | str | API key |
| `model` | str | Model name |
| `api_base` | str | Endpoint URL (openai provider only) |
| `threshold` | float | Score threshold; results below this are filtered out. Default: `0.1` |

If rerank is not configured, search uses vector similarity only.

### storage

Storage configuration for context data, including file storage (AGFS) and vector database storage (VectorDB).

#### Root Configuration

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `workspace` | str | Local data storage path (main configuration) | "./data" |
| `agfs` | object | AGFS configuration | {} |
| `vectordb` | object | Vector database storage configuration | {} |


```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "timeout": 10
    },
    "vectordb": {
      "backend": "local"
    }
  }
}
```

#### agfs

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `mode` | str | `"http-client"` or `"binding-client"` | `"http-client"` |
| `backend` | str | `"local"`, `"s3"`, or `"memory"` | `"local"` |
| `url` | str | AGFS service URL for `http-client` mode | `"http://localhost:1833"` |
| `timeout` | float | Request timeout in seconds | `10.0` |
| `s3` | object | S3 backend configuration (when backend is 's3') | - |

**Configuration Examples**

<details>
<summary><b>HTTP Client (Default)</b></summary>

Connects to a remote or local AGFS service via HTTP.

```json
{
  "storage": {
    "agfs": {
      "mode": "http-client",
      "url": "http://localhost:1833",
      "timeout": 10.0
    }
  }
}
```

</details>

<details>
<summary><b>Binding Client (High Performance)</b></summary>

Directly uses the AGFS Go implementation through a shared library. 

**Config**:
```json
{
  "storage": {
    "agfs": {
      "mode": "binding-client",
      "backend": "local"
    }
  }
}
```

</details>


##### S3 Backend Configuration

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `bucket` | str | S3 bucket name | null |
| `region` | str | AWS region where the bucket is located (e.g., us-east-1, cn-beijing) | null |
| `access_key` | str | S3 access key ID | null |
| `secret_key` | str | S3 secret access key corresponding to the access key ID | null |
| `endpoint` | str | Custom S3 endpoint URL, required for S3-compatible services like MinIO or LocalStack | null |
| `prefix` | str | Optional key prefix for namespace isolation | "" |
| `use_ssl` | bool | Enable/disable SSL (HTTPS) for S3 connections | true |
| `use_path_style` | bool | true for PathStyle used by MinIO and some S3-compatible services; false for VirtualHostStyle used by TOS and some S3-compatible services | true |
| `directory_marker_mode` | str | How to persist directory markers: `none`, `empty`, or `nonempty` | `"empty"` |

`directory_marker_mode` controls how AGFS materializes directory objects in S3:

- `empty` is the default. AGFS writes a zero-byte directory marker and preserves empty-directory semantics.
- `nonempty` writes a non-empty marker payload. Use this for S3-compatible services such as TOS that reject zero-byte directory markers.
- `none` switches AGFS to prefix-style S3 semantics. AGFS does not create directory marker objects, so empty directories are not persisted and may not be discoverable until they contain at least one child object.

Typical choices:

- For MinIO, SeaweedFS, and most PathStyle backends, keep the default `empty`.
- For TOS or other VirtualHostStyle backends that reject zero-byte directory markers, use `nonempty`.
- If you want pure prefix-style behavior and do not need persisted empty directories, use `none`.

</details>

<details>
<summary><b>PathStyle S3</b></summary>
Supports S3 storage in PathStyle mode, such as MinIO, SeaweedFS.

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "my-bucket",
        "endpoint": "s3.amazonaws.com",
        "region": "us-east-1",
        "access_key": "your-ak",
        "secret_key": "your-sk"
      }
    }
  }
}
```
</details>


<details>
<summary><b>VirtualHostStyle S3</b></summary>
Supports S3 storage in VirtualHostStyle mode, such as TOS.

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "my-bucket",
        "endpoint": "s3.amazonaws.com",
        "region": "us-east-1",
        "access_key": "your-ak",
        "secret_key": "your-sk",
        "use_path_style": false,
        "directory_marker_mode": "nonempty"
      }
    }
  }
}
```

</details>

#### vectordb

Vector database storage configuration

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `backend` | str | VectorDB backend type: 'local' (file-based), 'http' (remote service), 'volcengine' (cloud VikingDB), or 'vikingdb' (private deployment) | "local" |
| `name` | str | VectorDB collection name | "context" |
| `url` | str | Remote service URL for 'http' type (e.g., 'http://localhost:5000') | null |
| `project_name` | str | Project name (alias project) | "default" |
| `distance_metric` | str | Distance metric for vector similarity search (e.g., 'cosine', 'l2', 'ip') | "cosine" |
| `dimension` | int | Vector embedding dimension | 0 |
| `sparse_weight` | float | Sparse weight for hybrid vector search, only effective when using hybrid index | 0.0 |
| `volcengine` | object | 'volcengine' type VikingDB configuration | - |
| `vikingdb` | object | 'vikingdb' type private deployment configuration | - |

Default local mode
```
{
  "storage": {
    "vectordb": {
      "backend": "local"
    }
  }
}
```

<details>
<summary><b>volcengine vikingDB</b></summary>
Supports cloud-deployed VikingDB on Volcengine

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "volcengine",
      "project": "default",
      "volcengine": {
        "region": "cn-beijing",
        "ak": "your-access-key",
        "sk": "your-secret-key"
      }
  }
}
```
</details>


## Config Files

OpenViking uses two config files:

| File | Purpose | Default Path |
|------|---------|-------------|
| `ov.conf` | SDK embedded mode + server config | `~/.openviking/ov.conf` |
| `ovcli.conf` | HTTP client and CLI connection to remote server | `~/.openviking/ovcli.conf` |

When config files are at the default path, OpenViking loads them automatically — no additional setup needed.

If config files are at a different location, there are two ways to specify:

```bash
# Option 1: Environment variable
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf

# Option 2: Command-line argument (serve command only)
openviking-server --config /path/to/ov.conf
```

### ov.conf

The config sections documented above (embedding, vlm, rerank, storage) all belong to `ov.conf`. SDK embedded mode and server share this file.

For memory-related settings, add a `memory` section in `ov.conf`:

```json
{
  "memory": {
    "agent_scope_mode": "user+agent"
  }
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `agent_scope_mode` | Agent memory namespace mode: `"user+agent"` isolates by `(user_id, agent_id)`, while `"agent"` isolates only by `agent_id` and shares agent memories across users of the same agent | `"user+agent"` |

`agent_scope_mode` only affects agent-level namespaces such as `viking://agent/{agent_space}/memories/...`. User memories under `viking://user/{user_space}/memories/...` are not affected.

### ovcli.conf

Config file for the HTTP client (`SyncHTTPClient` / `AsyncHTTPClient`) and CLI to connect to a remote server:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent",
  "output": "table"
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `url` | Server address | (required) |
| `api_key` | API key for authentication (root key or user key) | `null` (no auth) |
| `account` | Default account sent as `X-OpenViking-Account` | `null` |
| `user` | Default user sent as `X-OpenViking-User` | `null` |
| `agent_id` | Agent identifier for agent space isolation | `null` |
| `output` | Default output format: `"table"` or `"json"` | `"table"` |

CLI flags can override these identity fields per command:

```bash
openviking --account acme --user alice --agent-id assistant-2 ls viking://
```

See [Deployment](./03-deployment.md) for details.

## server Section

When running OpenViking as an HTTP service, add a `server` section to `ov.conf`:

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key",
    "cors_origins": ["*"]
  }
}
```

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `host` | str | Bind address | `0.0.0.0` |
| `port` | int | Bind port | `1933` |
| `auth_mode` | str | Authentication mode: `"api_key"` or `"trusted"` | `"api_key"` |
| `root_api_key` | str | Root API key for multi-tenant auth in `api_key` mode | `null` |
| `cors_origins` | list | Allowed CORS origins | `["*"]` |

`api_key` mode uses API keys. `trusted` mode trusts `X-OpenViking-Account` / `X-OpenViking-User` headers from a trusted gateway or internal caller.

When `root_api_key` is configured, the server enables multi-tenant authentication. Use the Admin API to create accounts and user keys. Development mode only applies when `auth_mode = "api_key"` and `root_api_key` is not set.

For startup and deployment details see [Deployment](./03-deployment.md), for authentication see [Authentication](./04-authentication.md).

## storage.transaction Section

Path locks are enabled by default and usually require no configuration. **The default behavior is no-wait**: if the target path is already locked by another operation, the operation fails immediately with `LockAcquisitionError`. Set `lock_timeout` to a positive value to allow polling/retry.

```json
{
  "storage": {
    "transaction": {
      "lock_timeout": 5.0,
      "lock_expire": 300.0
    }
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `lock_timeout` | float | Path lock acquisition timeout (seconds). `0` = fail immediately if locked (default). `> 0` = wait/retry up to this many seconds, then raise `LockAcquisitionError`. | `0.0` |
| `lock_expire` | float | Stale lock expiry threshold (seconds). Locks held longer than this by a crashed process are force-released. | `300.0` |

For details on the lock mechanism, see [Path Locks and Crash Recovery](../concepts/09-transaction.md).

## encryption Section

Enable at-rest data encryption to ensure data security and isolation in multi-tenant environments. Encryption is completely transparent to users with no API changes.

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local|vault|volcengine_kms"
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `enabled` | bool | Whether encryption is enabled | `false` |
| `provider` | str | Key provider: `"local"`, `"vault"`, or `"volcengine_kms"` | - |

### Local (File)

Suitable for development environments and single-node deployments:

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `local.key_file` | str | Root key file path | `~/.openviking/master.key` |

### Vault (HashiCorp Vault)

Suitable for production and multi-cloud deployments:

```json
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "vault-token-xxx",
      "mount_point": "transit",
      "key_name": "openviking-root"
    }
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `vault.address` | str | Vault service address | - |
| `vault.token` | str | Vault access token | - |
| `vault.mount_point` | str | Transit engine mount point | `"transit"` |
| `vault.key_name` | str | Root key name | `"openviking-root"` |

### Volcengine KMS

Suitable for Volcengine cloud deployments:

```json
{
  "encryption": {
    "enabled": true,
    "provider": "volcengine_kms",
    "volcengine_kms": {
      "key_id": "kms-key-id-xxx",
      "region": "cn-beijing",
      "access_key": "AKLTxxxxxxxx",
      "secret_key": "Tmpxxxxxxxx"
    }
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `volcengine_kms.key_id` | str | KMS key ID | - |
| `volcengine_kms.region` | str | Region | `"cn-beijing"` |
| `volcengine_kms.access_key` | str | Volcengine Access Key | - |
| `volcengine_kms.secret_key` | str | Volcengine Secret Key | - |

For detailed encryption explanations, see [Data Encryption](../concepts/10-encryption.md). For complete usage instructions, see [Encryption Guide](./08-encryption.md).

## Full Schema

```json
{
  "embedding": {
    "max_concurrent": 10,
    "dense": {
      "provider": "volcengine",
      "api_key": "string",
      "model": "string",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "string",
    "api_key": "string",
    "model": "string",
    "api_base": "string",
    "thinking": false,
    "max_concurrent": 100,
    "extra_headers": {},
    "stream": false
  },
  "rerank": {
    "provider": "volcengine|openai",
    "api_key": "string",
    "model": "string",
    "api_base": "string",
    "threshold": 0.1
  },
  "encryption": {
    "enabled": false,
    "provider": "local|vault|volcengine_kms",
    "local": {
      "key_file": "~/.openviking/master.key"
    },
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "string",
      "mount_point": "transit",
      "key_name": "openviking-root"
    },
    "volcengine_kms": {
      "key_id": "string",
      "region": "cn-beijing",
      "access_key": "string",
      "secret_key": "string"
    }
  },
  "storage": {
    "workspace": "string",
    "agfs": {
      "backend": "local|s3|memory",
      "url": "string",
      "timeout": 10
    },
    "transaction": {
      "lock_timeout": 0.0,
      "lock_expire": 300.0
    },
    "vectordb": {
      "backend": "local|remote",
      "url": "string",
      "project": "string"
    }
  },
  "code": {
    "code_summary_mode": "ast"
  },
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "string",
    "cors_origins": ["*"]
  }
}
```

Notes:
- `storage.vectordb.sparse_weight` controls hybrid (dense + sparse) indexing/search. It only takes effect when you use a hybrid index; set it > 0 to enable sparse signals.

## Troubleshooting

### API Key Error

```
Error: Invalid API key
```

Check your API key is correct and has the required permissions.

### Vector Dimension Mismatch

```
Error: Vector dimension mismatch
```

Ensure the `dimension` in config matches the model's output dimension.

### VLM Timeout

```
Error: VLM request timeout
```

- Check network connectivity
- Increase timeout in config
- Try a smaller model

### Rate Limiting

```
Error: Rate limit exceeded
```

Volcengine has rate limits. Consider batch processing with delays or upgrading your plan.

## Related Documentation

- [Volcengine Purchase Guide](./02-volcengine-purchase-guide.md) - API key setup
- [API Overview](../api/01-overview.md) - Client initialization
- [Server Deployment](./03-deployment.md) - Server configuration
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
