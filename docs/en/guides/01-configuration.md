# Configuration

OpenViking uses a JSON configuration file (`~/.openviking/ov.conf`) for settings.

For a first-time setup, the recommended flow is:

```bash
openviking-server init
openviking-server doctor
```

`openviking-server init` prompts for embedding and VLM settings separately. For API-based VLM choices such as `OpenAI`, `Volcengine`, `Kimi`, and `GLM`, enter the VLM API key when prompted. If you want to use Codex as the VLM provider, choose `OpenAI Codex`; the wizard can import existing Codex auth or guide you through login directly.

## Configuration File

Create `~/.openviking/ov.conf` in your project directory:

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "name": "context",
      "backend": "local"
    },
    "agfs": {
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",
      "api_key"  : "<your-api-key>",
      "provider" : "<provider-type>",
      "dimension": 1024,
      "model"    : "<model-name>"
    }
  },
  "vlm": {
    "api_base" : "<api-endpoint>",
    "api_key"  : "<your-api-key>",
    "provider" : "<provider-type>",
    "model"    : "<model-name>"
  }
}
```

For `provider: "openai-codex"`, `vlm.api_key` is optional when Codex OAuth is already available.

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
      "model"    : "doubao-embedding-vision-251215",
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-2-0-lite-260428"
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
      "dimension": 1536,
      "model"    : "text-embedding-3-small"
    }
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-5.4"
  }
}
```

</details>

<details>
<summary><b>Volcengine Embedding + Codex VLM</b></summary>

Use `openviking-server init` to complete the Codex login/import step, then run `openviking-server doctor`.

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "provider" : "openai-codex",
    "model"    : "gpt-5.4",
    "api_base" : "https://chatgpt.com/backend-api/codex"
  }
}
```

</details>

<details>
<summary><b>Volcengine Embedding + Kimi Coding VLM</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "provider" : "kimi",
    "model"    : "kimi-code",
    "api_key"  : "your-kimi-subscription-api-key",
    "api_base" : "https://api.kimi.com/coding"
  }
}
```

`kimi` applies the Kimi Coding defaults automatically, including the default Kimi Coding user agent.

</details>

<details>
<summary><b>Volcengine Embedding + GLM Coding Plan VLM</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "provider" : "glm",
    "model"    : "glm-4.6v",
    "api_key"  : "your-zai-api-key",
    "api_base" : "https://api.z.ai/api/coding/paas/v4"
  }
}
```

Use a vision-capable GLM model such as `glm-4.6v` or `glm-5v-turbo` when OpenViking needs image understanding.

</details>

## Configuration Sections

### embedding

Embedding model configuration for vector search, supporting dense, sparse, and hybrid modes.

#### Dense Embedding

```json
{
  "embedding": {
    "max_concurrent": 10,
    "max_retries": 3,
    "text_source": "content_only",
    "max_input_tokens": 4096,
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
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
| `max_retries` | int | Maximum retry attempts for transient embedding provider errors (`embedding.max_retries`, default: `3`; `0` disables retry) |
| `text_source` | str | Text used for vectorizing text files. `content_only` reads raw content, `summary_first` uses summary when available and falls back to content, `summary_only` uses only summary. Default: `content_only` |
| `max_input_tokens` | int | Maximum estimated raw text tokens sent to the embedding model when content is used. Default: `4096` |
| `provider` | str | `"openai"`, `"azure"`, `"volcengine"`, `"vikingdb"`, `"jina"`, `"ollama"`, `"gemini"`, `"voyage"`, `"dashscope"`, `"minimax"`, `"cohere"`, `"litellm"`, or `"local"` |
| `api_key` | str | API key |
| `model` | str | Model name |
| `dimension` | int | Vector dimension. For Voyage, this maps to `output_dimension` |
| `input` | str | Input type: `"text"` or `"multimodal"` |
| `batch_size` | int | Batch size for embedding requests |
| `encoding_format` | str | (OpenAI / Azure only) Wire format for embedding values: `"float"` or `"base64"`. Leave unset to use the OpenAI Python SDK default. Set to `"float"` when the upstream gateway cannot deserialize base64 embedding payloads correctly. |

`embedding.max_retries` only applies to transient errors such as `429`, `5xx`, timeouts, and connection failures. Permanent errors such as `400`, `401`, `403`, and `AccountOverdue` are not retried automatically. The backoff strategy is exponential backoff with jitter, starting at `0.5s` and capped at `8s`.

#### Embedding Circuit Breaker

When the embedding provider experiences consecutive transient failures (e.g. `429`, `5xx`), OpenViking opens a circuit breaker to temporarily stop calling the provider and re-enqueue embedding tasks. After the base `reset_timeout`, it allows a probe request (HALF_OPEN). If the probe fails, the next `reset_timeout` is doubled (capped by `max_reset_timeout`).

```json
{
  "embedding": {
    "circuit_breaker": {
      "failure_threshold": 5,
      "reset_timeout": 60,
      "max_reset_timeout": 600
    }
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `circuit_breaker.failure_threshold` | int | Consecutive failures required to open the breaker (default: `5`) |
| `circuit_breaker.reset_timeout` | float | Base reset timeout in seconds (default: `60`) |
| `circuit_breaker.max_reset_timeout` | float | Maximum reset timeout in seconds when backing off (default: `600`) |

**Available Models**

| Model | Dimension | Input Type | Notes |
|-------|-----------|------------|-------|
| `doubao-embedding-vision-251215` | 1024 | multimodal | Recommended |
| `doubao-embedding-250615` | 1024 | text | Text only |

With `input: "multimodal"`, OpenViking can embed text, images (PNG, JPG, etc.), and mixed content. Image-to-image search requires this mode; text-only embedding models continue to index image summaries but cannot accept image queries.

**Supported providers:**
- `openai`: OpenAI Embedding API
- `azure`: Azure OpenAI Embedding API
- `volcengine`: Volcengine Embedding API
- `vikingdb`: VikingDB Embedding API
- `jina`: Jina AI Embedding API
- `ollama`: Ollama local OpenAI-compatible Embedding API
- `voyage`: Voyage AI Embedding API
- `minimax`: MiniMax Embedding API
- `cohere`: Cohere Embedding API
- `gemini`: Google Gemini Embedding API (text-only; requires `google-genai>=1.0.0`)
- `dashscope`: DashScope (Alibaba Tongyi) Embedding API
- `litellm`: LiteLLM Embedding API
- `local`: Local GGUF embedding models

**OpenAI-compatible provider example with JSON float embeddings:**

```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "your-api-key",
      "api_base": "https://your-openai-compatible-endpoint/v1",
      "model": "text-embedding-3-large",
      "dimension": 3072,
      "encoding_format": "float"
    }
  }
}
```

`encoding_format` is optional and is only forwarded for `provider: "openai"` and `provider: "azure"`. Leave it unset for the OpenAI Python SDK default. Set it to `"float"` when an OpenAI-compatible upstream gateway cannot deserialize base64 embedding payloads correctly.

**Azure OpenAI provider example with JSON float embeddings:**

```json
{
  "embedding": {
    "dense": {
      "provider": "azure",
      "api_key": "your-azure-api-key",
      "api_base": "https://your-resource-name.openai.azure.com",
      "api_version": "2025-01-01-preview",
      "model": "your-embedding-deployment-name",
      "dimension": 3072,
      "encoding_format": "float"
    }
  }
}
```

For Azure OpenAI, `model` must be the embedding deployment name configured in Azure.

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

**DashScope (Alibaba Tongyi) provider:**

```json
{
  "embedding": {
    "dense": {
      "provider": "dashscope",
      "api_key": "${DASHSCOPE_API_KEY}",
      "model": "text-embedding-v4",
      "dimension": 1024
    }
  }
}
```

**Available DashScope models:**

| Model | Dimension | Input Type | Notes |
|-------|-----------|------------|-------|
| `text-embedding-v3` | 1024 | text | Optimized for Chinese |
| `text-embedding-v4` | 1024 | text | Optimized for Chinese |
| `tongyi-embedding-vision-plus` | 1152 | multimodal | Supports fusion via `enable_fusion` |
| `tongyi-embedding-vision-flash` | 768 | multimodal | Faster, lower cost |
| `qwen3-vl-embedding` | 2560 | multimodal | Text + image + video |
| `qwen2.5-vl-embedding` | 1024 | multimodal | Text + image + video |

**Multimodal parameters** (text+image/video models only):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_type` | str | `"multimodal"` or `"text"` | Embedding mode (default: `"multimodal"`) |
| `enable_fusion` | bool | `false` | Enable fusion vectors for `tongyi-embedding-vision-*` models |
| `res_level` | int | `2` | Image resolution level (1=high, 2=medium, 3=low) |
| `max_video_frames` | int | `16` | Maximum video frames to embed |

**Endpoint selection** — DashScope provides `api_base` defaults for China (`cn`) and international (`intl`) regions:

| Region | `api_base` | Notes |
|--------|-----------|-------|
| China | `https://dashscope.aliyuncs.com` (default) | Recommended for users in mainland China |
| International | `https://dashscope-intl.aliyuncs.com` | For users outside China |

Custom endpoint URLs are also supported by setting a full URL.

Get your API key at https://dashscope.console.aliyun.com/api-key

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

> **Note:** Volcengine sparse embedding is supported starting from model `doubao-embedding-vision-251215`.

```json
{
  "embedding": {
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215"
    }
  }
}
```

Sparse output is a provider capability, not an endpoint inferred from
`storage.vectordb.sparse_weight`. OpenViking currently implements `sparse` and
`hybrid` embedding providers for `volcengine` and `vikingdb`. The
OpenAI-compatible, Ollama, and built-in `local` providers are dense-only; a
self-hosted `/v1/embeddings` endpoint is therefore not treated as a sparse
endpoint, and OpenViking does not probe a separate
`/v1/embeddings/sparse` route.

There is no automatic BM25 or other sparse-vector fallback when the configured
embedder returns only dense vectors. To use hybrid retrieval, configure a
supported sparse/hybrid provider and set `storage.vectordb.sparse_weight > 0`.
Model memory requirements are provider/model-specific and are not controlled by
OpenViking; size self-hosted models against their provider documentation before
enabling them in production.

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
      "model": "doubao-embedding-vision-251215",
      "dimension": 1024
    },
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215"
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
    "model": "doubao-seed-2-0-lite-260428",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "max_retries": 3
  }
}
```

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `api_key` | str | API key. Optional for `openai-codex` when Codex OAuth is available, and optional for `litellm` routes that use provider-native credentials |
| `forward_api_key` | bool | LiteLLM only. Overrides whether `api_key` is forwarded to LiteLLM. By default, OpenViking does not forward placeholder keys for native AWS/GCP routes such as `bedrock/`, `sagemaker/`, and `vertex_ai/`; set to `true` when intentionally using a LiteLLM API-key route such as Bedrock bearer-token auth |
| `model` | str | Model name |
| `api_base` | str | API endpoint (optional) |
| `thinking` | bool | Enable thinking mode for VolcEngine models (default: `false`) |
| `max_concurrent` | int | Maximum concurrent semantic LLM calls (default: `64`) |
| `max_retries` | int | Maximum retry attempts for transient VLM provider errors (default: `3`; `0` disables retry) |
| `backup` | object | Optional backup VLM configuration (same shape as `vlm`) for automatic failover when the primary fails with retryable errors such as rate limits, `5xx` responses, or connection/timeout failures. Only one level of failover is supported &mdash; the backup itself cannot define a nested `backup` |
| `timeout` | float | Per-request HTTP timeout in seconds passed to the underlying OpenAI/LiteLLM client. Increase for slow endpoints (e.g., DashScope, local inference). Must be `> 0` (default: `600.0`) |
| `extra_headers` | object | Custom HTTP headers for compatible HTTP providers. `kimi` also accepts header overrides, but already injects the required subscription headers by default |
| `extra_request_body` | object | Extra JSON body fields for OpenAI-compatible completion requests, useful for provider-specific options such as Ollama `{"think": false}` |
| `stream` | bool | Enable streaming mode (for OpenAI-compatible providers, default: `false`) |

`vlm.max_retries` only applies to transient errors such as `429`, `5xx`, timeouts, and connection failures. Permanent authentication, authorization, and billing errors are not retried automatically. The backoff strategy is exponential backoff with jitter, starting at `0.5s` and capped at `8s`.

**Available Models**

| Model | Notes |
|-------|-------|
| `doubao-seed-2-0-lite-260428` | Recommended for semantic extraction |
| `doubao-pro-32k` | For longer context |

When resources are added, VLM generates:

1. **L0 (Abstract)**: ~100 token summary
2. **L1 (Overview)**: ~2k token overview with navigation

If VLM is not configured, L0/L1 will be generated from content directly (less semantic), and multimodal resources may have limited descriptions.

**Supported providers:**
- `volcengine`: Volcengine VLM API
- `openai`: OpenAI-compatible VLM API
- `openai-codex`: Codex VLM via ChatGPT/Codex OAuth
- `kimi`: Kimi Coding subscription endpoint with built-in provider defaults
- `glm`: Z.AI GLM Coding Plan endpoint with OpenAI-compatible requests
- `litellm`: LiteLLM VLM API, including explicit LiteLLM routes such as `bedrock/`, `sagemaker/`, `vertex_ai/`, and `azure/`

For `openai-codex`, authenticate through `openviking-server init`, then verify with `openviking-server doctor`.

For `litellm`, `api_key` can be omitted when the underlying route authenticates through
environment or provider-native credentials, such as AWS IAM/IRSA for Bedrock and
SageMaker or ADC/service-account credentials for Vertex AI. Azure routes still use
`api_key` normally. If you intentionally use LiteLLM's Bedrock bearer-token API-key
auth, set `forward_api_key` to `true`.

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
- **Kimi Coding**: Override or extend the default subscription headers when you need a custom user agent
- **Custom proxies**: Add authentication or tracing headers
- **API gateways**: Add version or routing identifiers

**Custom Request Body**

For OpenAI-compatible providers that accept provider-specific JSON body fields, add them via `extra_request_body`. OpenViking merges these fields into the `extra_body` sent by the OpenAI SDK or LiteLLM:

```json
{
  "vlm": {
    "provider": "litellm",
    "api_key": "ollama",
    "model": "ollama/llama3.1",
    "api_base": "http://127.0.0.1:11434",
    "extra_request_body": {
      "think": false
    }
  }
}
```

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

### query_planner

Optional lightweight model for retrieval intent analysis and query planning. It uses the same configuration shape as `vlm`, but only affects `search()` intent analysis and query expansion. If `query_planner` is omitted or empty, OpenViking falls back to `vlm` for backward compatibility.

> In `openviking-server init` you can optionally enable a local lightweight query planner; the wizard pulls the Ollama model and writes the `query_planner` config for you. For recognized query-planner models, `search()` selects the matching bundled prompt at runtime. Models not in the mapping keep using `retrieval.intent_analysis`.

We recommend the local Ollama model [`guoxuter/ov_intent_analysis_sft:v7_q8`](https://ollama.com/guoxuter/ov_intent_analysis_sft:v7_q8). Fine-tuned from Qwen3.5-0.8B, it can be deployed locally and is well suited to letting a small model handle retrieval planning: for small talk, greetings, or turns where the context is already sufficient, it returns no queries to reduce unnecessary memory injection and token consumption; when retrieval is needed, it emits structured queries targeting `skill`, `resource`, and `memory`. The earlier [`v4_q8`](https://ollama.com/guoxuter/ov_intent_analysis_sft:v4_q8) revision is still supported as an alternative.

Pull the model first and make sure the Ollama service is reachable:

```bash
ollama pull guoxuter/ov_intent_analysis_sft:v7_q8
```

Then add the following to your OpenViking configuration:

```json
{
  "query_planner": {
    "provider": "litellm",
    "model": "ollama/guoxuter/ov_intent_analysis_sft:v7_q8",
    "api_base": "http://127.0.0.1:11434",
    "temperature": 0.0,
    "timeout": 60,
    "extra_request_body": {
      "think": false
    }
  }
}
```

For `ollama/guoxuter/ov_intent_analysis_sft:v7_q8` (and `v4_q8`), OpenViking automatically uses the matching bundled prompt during search (`retrieval.ov_intent_analysis_sft_v7` and `retrieval.ov_intent_analysis_sft_v4` respectively). No prompt file replacement or `prompts.templates_dir` override is required. If you use an unmapped model, OpenViking keeps the default `retrieval.intent_analysis` prompt.

This lets a small model handle retrieval planning with lower latency, while keeping a stronger `vlm` for semantic extraction, memory extraction, and multimodal processing.

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

**Dependency**: Included by default in `openviking[bot]` installation

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

#### Remote resource network guard

When ingesting a resource from a URL, OpenViking rejects loopback, link-local, private, and other non-public destinations, plus any host not on the code-hosting allowlist, raising `PermissionDeniedError`. To ingest code from self-hosted GitHub Enterprise / GitLab / Azure DevOps, add the host to the matching allowlist under `code`:

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `github_domains` | list[str] | Allowed GitHub hosts (add your GitHub Enterprise host here) | `["github.com", "www.github.com"]` |
| `gitlab_domains` | list[str] | Allowed GitLab hosts (add your self-hosted GitLab host here) | `["gitlab.com", "www.gitlab.com"]` |
| `azure_devops_domains` | list[str] | Allowed Azure DevOps hosts | `["dev.azure.com", "ssh.dev.azure.com", "vs-ssh.visualstudio.com"]` |
| `code_hosting_domains` | list[str] | Additional generic code-hosting hosts | `["github.com", "gitlab.com"]` |

To ingest from private/internal network addresses (e.g. an internal mirror), set the top-level `allow_private_networks` to `true` (disabled by default, so only public addresses are allowed):

```json
{
  "allow_private_networks": false,
  "code": {
    "github_domains": ["github.com", "github.example.com"]
  }
}
```

The `PermissionDeniedError` message names the exact key to add for the blocked host.

### rerank

Reranking model for search result refinement. Supports VikingDB (Volcengine), Cohere, and OpenAI-compatible APIs.

**Volcengine (VikingDB):**

```json
{
  "rerank": {
    "provider": "vikingdb",
    "ak": "your-access-key",
    "sk": "your-secret-key",
    "model_name": "doubao-seed-rerank",
    "model_version": "251028"
  }
}
```

**OpenAI-compatible provider (e.g. DashScope):**

```json
{
  "rerank": {
    "provider": "openai",
    "api_key": "your-api-key",
    "api_base": "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
    "model": "qwen3-rerank",
    "timeout": 120,
    "max_input_tokens": 2048,
    "threshold": 0.1
  }
}
```

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | str | `"vikingdb"`, `"cohere"`, or `"openai"`. Auto-detected if omitted. |
| `ak` | str | VikingDB Access Key (vikingdb provider only) |
| `sk` | str | VikingDB Secret Key (vikingdb provider only) |
| `model_name` | str | Model name (vikingdb provider only, default: `doubao-seed-rerank`) |
| `api_key` | str | API key (for `openai` or `cohere` providers) |
| `api_base` | str | Endpoint URL (for `openai` provider) |
| `model` | str | Model name (for `openai` providers) |
| `timeout` | float | HTTP request timeout in seconds for OpenAI-compatible providers. Increase for slow or cold-starting local rerank servers. Default: `30.0` |
| `max_input_tokens` | int | Maximum estimated raw-text tokens in each query-document pair sent to the reranker. Oversized inputs retain their beginning and end. `0` disables truncation. Default: `0` |
| `threshold` | float | Score threshold between `0.0` and `1.0`; results below this are filtered out. Default: `0.1` |
| `extra_headers` | object | Custom HTTP headers (for OpenAI-compatible providers, optional) |

**Supported providers:**
- `vikingdb`: Volcengine VikingDB Rerank API (uses AK/SK)
- `cohere`: Cohere Rerank API
- `openai`: OpenAI-compatible Rerank API

If rerank is not configured, search uses vector similarity only.

### retrieval

Retrieval ranking configuration for final search scores.

```json
{
  "retrieval": {
    "hotness_alpha": 0.0,
    "score_propagation_alpha": 1.0
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `hotness_alpha` | float | Weight for blending hotness into final retrieval scores. `0.0` disables the hotness boost and keeps scores equal to semantic similarity; `1.0` uses only hotness. Valid range: `0.0` to `1.0`. | `0.0` |
| `score_propagation_alpha` | float | Weight for each child result's own score when blending with its parent score during hierarchical retrieval. `1.0` ignores the parent score (semantic similarity only); `0.5` is an equal blend with the parent score; `0.0` uses only the parent score. Valid range: `0.0` to `1.0`. | `1.0` |

Keep `hotness_alpha` at `0.0` when you need scores to reflect pure vector similarity. Set it above `0.0` only when frequently accessed or recently updated contexts should receive a ranking boost.

### grep

Grep engine configuration for content pattern search. These settings are server-side only and cannot be overridden per-request.

```json
{
  "grep": {
    "engine": "auto",
    "switch_to_remote_threshold": 10000
  }
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `engine` | str | Search engine mode: `"auto"` uses VikingDB BM25 recall when available and falls back to local filesystem search; `"fs"` forces local filesystem search only. | `"auto"` |
| `switch_to_remote_threshold` | int | L2 record count threshold to switch to VikingDB BM25 recall. When the number of L2 files under the search scope reaches this threshold, VikingDB BM25 is used for phase-1 recall; otherwise local filesystem search is used. Set to `0` to always use VikingDB BM25. Must be ≥ 0. | `10000` |

For VikingDB / Volcengine FullText grep, OpenViking writes a `content` text field for BM25 recall. The source context keeps the full content, while the vector-store write payload truncates this field to **1 MB** at the final adapter boundary to stay within backend payload limits. Only VikingDB-backed backends use `content`; on all other backends (`local`, `cuvs`, `qdrant`, `opengauss`, `http`) the field is not written.

### storage

Storage configuration for context data, including file storage (RAGFS) and vector database storage (VectorDB).

#### Root Configuration

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `workspace` | str | Local data storage path (main configuration) | "./data" |
| `skip_process_lock` | bool | Whether to skip the startup process-lock check for `storage.workspace`. When enabled, OpenViking will not check or create the `.openviking.pid` lock file. | `false` |
| `agfs` | object | RAGFS (Rust-based AGFS) configuration | {} |
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

#### agfs (RAGFS)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `backend` | str | `"local"`, `"s3"`, or `"memory"` | `"local"` |
| `timeout` | float | Request timeout in seconds | `10.0` |
| `backups` | object | Multi-write storage configuration. When set, the top-level `backend` acts as the primary backend and `backups.items[]` defines backup backends | `null` |
| `redirects` | array | File redirect policies for multi-write storage. Matching files are written to the specified backup instead of the primary backend | `[]` |
| `queuefs` | object | QueueFS configuration. Controls the namespace mode, backend, and runtime options for `/queue` | `{ "mode": "shared", "backend": "sqlite", "recover_stale_sec": 0, "busy_timeout_ms": 5000 }` |
| `queue_db_path` | str (optional) | Legacy compatibility field for QueueFS sqlite DB path. Superseded by `storage.agfs.queuefs.db_path`. Defaults to `{storage.workspace}/_system/queue/queue.db` when not set. Useful when the workspace volume does not support sqlite (e.g. some network filesystems) | `null` |
| `s3` | object | S3 backend configuration (when backend is 's3') | - |

**Configuration Examples**

RAGFS uses Rust binding mode by default, directly accessing the file system through the Rust implementation.

> [!WARNING]
> `storage.agfs` no longer supports the AGFS HTTP client mode, and the old HTTP client entry should not be configured anymore. AGFS / RAGFS filesystem access now happens only through the in-process Rust binding (`RAGFSBindingClient`). This does not affect the OpenViking server HTTP API, the `ov` CLI, or `AsyncHTTPClient` / `SyncHTTPClient` when they connect to an OpenViking server.

##### Multi-Write Storage Configuration

`storage.agfs.backups` enables multi-write storage. If it is not configured, OpenViking stays in single-backend mode.

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "redirects": [
        {
          "type": "FileExtensionPolicy",
          "extensions": ["(pdf|ppt|zip)"],
          "target": ["s3-backup"]
        }
      ],
      "backups": {
        "sync_type": "async",
        "items": [
          {
            "name": "s3-backup",
            "backend": "s3",
            "s3": {
              "bucket": "openviking-backup",
              "region": "cn-beijing",
              "endpoint": "https://tos-s3-cn-beijing.volces.com",
              "access_key": "your-ak",
              "secret_key": "your-sk",
              "prefix": "multi-write"
            }
          }
        ]
      }
    }
  }
}
```

Common `backups` fields:

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `sync_type` | str | Multi-write sync mode. Supports `"async"` or `"sync"` | `"async"` |
| `write_ack_count` | int | Number of backup acknowledgements required before a `sync` write returns | all backups |
| `write_ack_timeout_ms` | int | Timeout in milliseconds while waiting for backup acknowledgements in `sync` mode | `null` |
| `write_concurrency` | int | Maximum async backup write concurrency | `null` |
| `items` | array | Backup backend list. Each item reuses normal backend configuration and adds fields such as `name`, `operations`, `excludes`, and `encryption` | `[]` |

Common `redirects` fields:

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `type` | str | Policy type. Supports `"FileExtensionPolicy"` or `"FileOverSizePolicy"` | required |
| `extensions` | array | Extension regex list used by `FileExtensionPolicy`, for example `["(pdf\\|ppt)"]` | `[]` |
| `max_size_mb` | int | File size threshold in MB used by `FileOverSizePolicy` | `null` |
| `target` | array | Backup `name` list that receives matched files | required |

File-size redirect example:

```json
{
  "type": "FileOverSizePolicy",
  "max_size_mb": 100,
  "target": ["s3-backup"]
}
```

Notes:

- `redirects` is configured at top-level `storage.agfs` and defines redirect policies for the primary backend.
- `target` must reference an existing backup `name` from `backups.items[]`.
- Files matched by redirect still appear as normal readable and listable files through the filesystem APIs.

See the [Multi-Write Storage Guide](./13-multi-write-storage.md) for more examples.

##### QueueFS Configuration

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `mode` | str | QueueFS namespace mode: `"shared"` uses `/queue`; `"worker"` isolates each worker under `/queue/worker-<index\|pid>` | `"shared"` |
| `backend` | str | QueueFS backend: `"memory"`, `"sqlite"`, or `"sqlite3"` | `"sqlite"` |
| `db_path` | str (optional) | SQLite database path for QueueFS when backend is `"sqlite"` or `"sqlite3"` | `null` |
| `recover_stale_sec` | int | Recover `processing` queue messages older than this many seconds on startup. `0` means recover all stale processing messages | `0` |
| `busy_timeout_ms` | int | SQLite busy timeout for QueueFS in milliseconds | `5000` |

Notes:

- QueueFS defaults to `sqlite` even if the main AGFS storage backend is `local`, `s3`, or `memory`.
- `mode=shared` keeps the historical global queue namespace at `/queue`; `mode=worker` isolates each worker under `/queue/worker-<index|pid>`.
- `db_path` is only used when QueueFS backend is `sqlite` or `sqlite3`.
- If both `storage.agfs.queuefs.db_path` and legacy `storage.agfs.queue_db_path` are set, `storage.agfs.queuefs.db_path` wins.
- If QueueFS backend is `memory`, any `db_path` or legacy `queue_db_path` is ignored.

Examples:

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "queuefs": {
        "mode": "shared",
        "backend": "sqlite",
        "db_path": "./data/_system/queue/custom-queue.db"
      }
    }
  }
}
```

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "queuefs": {
        "mode": "worker",
        "backend": "memory"
      }
    }
  }
}
```

Legacy compatibility example:

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "queue_db_path": "./data/_system/queue/queue.db"
    }
  }
}
```


##### S3 Backend Configuration

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `bucket` | str | S3 bucket name | null |
| `region` | str | AWS region where the bucket is located (e.g., us-east-1, cn-beijing) | null |
| `access_key` | str | S3 access key ID | null |
| `secret_key` | str | S3 secret access key corresponding to the access key ID | null |
| `endpoint` | str | Custom S3 endpoint, required for S3-compatible services like MinIO or LocalStack. Accepts a full URL (`https://...` or `http://...`) or a bare hostname; bare hostnames are auto-prefixed with `https://` or `http://` based on `use_ssl` | null |
| `prefix` | str | Optional key prefix for namespace isolation | "" |
| `use_ssl` | bool | Enable/disable SSL (HTTPS) for S3 connections. Also controls the scheme auto-prefixed onto bare-hostname `endpoint` values | true |
| `use_path_style` | bool | true for PathStyle used by MinIO and some S3-compatible services; false for VirtualHostStyle used by TOS and some S3-compatible services | true |
| `auto_detect_content_type` | bool | Automatically infer MIME type from the object key / filename extension and set the S3 object `Content-Type` header during upload | false |
| `directory_marker_mode` | str | How to persist directory markers: `none`, `empty`, or `nonempty` | `"empty"` |
| `normalize_encoding_chars` | str | Characters to escape in S3 object keys as `!HH` hexadecimal bytes; empty string disables normalization | `"?#%+@"` |

`directory_marker_mode` controls how RAGFS materializes directory objects in S3:

- `empty` is the default. RAGFS writes a zero-byte directory marker and preserves empty-directory semantics.
- `nonempty` writes a non-empty marker payload. Use this for S3-compatible services such as TOS that reject zero-byte directory markers.
- `none` switches RAGFS to prefix-style S3 semantics. RAGFS does not create directory marker objects, so empty directories are not persisted and may not be discoverable until they contain at least one child object.

Typical choices:

- For MinIO, SeaweedFS, and most PathStyle backends, keep the default `empty`.
- For TOS or other VirtualHostStyle backends that reject zero-byte directory markers, use `nonempty`.
- If you want pure prefix-style behavior and do not need persisted empty directories, use `none`.

`normalize_encoding_chars` controls which characters RAGFS rewrites before issuing S3 requests:

- The default value is `"?#%+@"`, so only `?`, `#`, `%`, `+`, and `@` are escaped.
- Escaped bytes are encoded as `!HH`, where `HH` is the uppercase hexadecimal value of the byte.
- Characters not listed in `normalize_encoding_chars`, including Chinese and other Unicode characters, remain unchanged.
- Set `normalize_encoding_chars` to `""` to keep original path segments in object keys.

`auto_detect_content_type` is disabled by default for backward compatibility. When enabled, RAGFS infers the MIME type from the object key / filename extension and writes it to the S3 object `Content-Type`:

- Detection is based on the object key / filename extension, not file content sniffing.
- Directory markers whose keys end with `/` do not get a `Content-Type`.
- Unknown extensions fall back to `application/octet-stream`.

Example:

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
        "auto_detect_content_type": true
      }
    }
  }
}
```

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
        "secret_key": "your-sk",
        "normalize_encoding_chars": "?#%+@"
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
        "directory_marker_mode": "nonempty",
        "normalize_encoding_chars": "?#%+@"
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
| `backend` | str | VectorDB backend type: 'local' (file-based), 'http' (remote service), 'volcengine' (cloud VikingDB), 'vikingdb' (private deployment), 'cuvs' (local storage + GPU dense search), 'qdrant', or 'opengauss' | "local" |
| `name` | str | VectorDB collection name | "context" |
| `url` | str | Remote service URL for 'http' type (e.g., 'http://localhost:5000') | null |
| `project_name` | str | Project name (alias project) | "default" |
| `distance_metric` | str | Distance metric for vector similarity search (e.g., 'cosine', 'l2', 'ip') | "cosine" |
| `dimension` | int | Vector embedding dimension | 0 |
| `sparse_weight` | float | Sparse weight for hybrid vector search, only effective when using hybrid index | 0.0 |
| `volcengine` | object | 'volcengine' type VikingDB configuration | - |
| `vikingdb` | object | 'vikingdb' type private deployment configuration | - |
| `cuvs` | object | NVIDIA cuVS configuration for the 'cuvs' backend and the opt-in memory-aware auto mode on 'local'; see the [cuVS guide](./16-cuvs.md) | - |
| `qdrant` | object | 'qdrant' type Qdrant configuration | - |
| `opengauss` | object | 'opengauss' native vector backend configuration | - |

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

<details>
<summary><b>openGauss</b></summary>

Requires an openGauss server with native `vector` support and a remote-capable database user.
Install the optional driver with `pip install "openviking[opengauss]"`.
In the official container, the initial `omm` user may be restricted for remote login; create a normal user for OpenViking if needed.

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "opengauss",
      "project": "default",
      "distance_metric": "cosine",
      "dimension": 1024,
      "opengauss": {
        "host": "127.0.0.1",
        "port": 5432,
        "user": "openviking",
        "password": "your-password",
        "db_name": "postgres",
        "schema": "public",
        "mode": "standalone"
      }
    }
  }
}
```

Set `mode` to `"distributed"` for openGauss distributed deployments; OpenViking will attempt to mark metadata tables as reference tables and distribute collection tables by `id`.
</details>


## Config Files

OpenViking uses two config files:

| File | Purpose | Default Path |
|------|---------|-------------|
| `ov.conf` | SDK embedded mode + server config | `~/.openviking/ov.conf` |
| `ovcli.conf` | HTTP client and CLI connection to remote server | `~/.openviking/ovcli.conf` |

When config files are at the default path, OpenViking loads them automatically — no additional setup needed.

> **Root-key two-file rule:** `server.root_api_key` in `ov.conf` is the
> credential accepted by the server. `root_api_key` in `ovcli.conf` is the
> client-side copy used by `ov --sudo`. If that CLI manages this server, keep
> the two values identical and rotate both files together. The normal
> tenant-scoped `api_key` remains a separate user/admin credential.

### Reload boundary

The server reads `ov.conf` during process startup and does not watch the file
for changes. Editing `embedding`, `vlm`, `rerank`, `retrieval`, `storage`, or
`server` settings requires restarting the OpenViking server. Queue work that is
already running is not migrated to the new configuration, so use the normal
service-manager restart procedure and verify with `openviking-server doctor`
after the process comes back.

`ovcli.conf` is client-side configuration. A new `ov` command or newly created
HTTP client reads the current file; an already-running client or plugin may keep
the values it loaded at construction time and should be restarted when its
connection or credential settings change.

If config files are at a different location, there are two ways to specify:

```bash
# Option 1: Environment variable
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf

# Option 2: Command-line argument (serve command only)
openviking-server --config /path/to/ov.conf
```

### ov.conf

The config sections documented above (embedding, vlm, rerank, retrieval, grep, storage) all belong to `ov.conf`. SDK embedded mode and server share this file.

For memory-related settings, add a `memory` section in `ov.conf`:

```json
{
  "memory": {
    "custom_templates_dir": "/path/to/custom-memory"
  }
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `version` | Deprecated and ignored. OpenViking always uses the v3 memory extraction pipeline; existing configs that set this field still load without error. | `"v3"` |
| `custom_templates_dir` | Custom memory templates directory. If set, templates from this directory are loaded in addition to built-in templates. | `""` |
| `extraction_enabled` | Whether session commit runs long-term memory extraction. | `true` |
| `session_skill_extraction_enabled` | Whether session commit also extracts reusable skills into the current user's skill directory. | `false` |
| `link_enabled` | Whether memory extraction writes and resolves memory links. | `false` |

### ovcli.conf

You can edit this file by hand, or generate it interactively with `ov config`. If you maintain configurations for multiple servers, switch between them with `ov config switch`.

For the guided CLI setup flow, see [OpenViking CLI Setup](../getting-started/05-cli-setup.md).

Config file for the HTTP client (`SyncHTTPClient` / `AsyncHTTPClient`) and CLI to connect to a remote server:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key",
  "profile": false,
  "upload": {
    "mode": "local",
    "ignore_dirs": "node_modules,.cache,.nx",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  }
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `url` | Server address | (required) |
| `api_key` | API key for authentication (root key or user key) | `null` (no auth) |
| `account` | Optional trusted-mode account identity header value | `null` |
| `user` | Optional trusted-mode user identity header value | `null` |
| `profile` | Whether to append `profile=1` to HTTP requests by default. Applies to both the Python HTTP client and the `ov` CLI; `ov --profile` can enable it per invocation. Actual effect still depends on the server enabling `server.profile_enabled`. | `false` |
| `upload.ignore_dirs` | Default directory ignore list for `add-resource` (CSV) | `null` |
| `upload.include` | Default include patterns for `add-resource` (CSV) | `null` |
| `upload.exclude` | Default exclude patterns for `add-resource` (CSV) | `null` |
| `upload.mode` | Temporary upload backend: `"local"` (per-instance disk) or `"shared"` (distributed shared store, required when consumer requests can land on a different server instance than the upload). Per-call override via `OPENVIKING_UPLOAD_MODE`. | `null` (server's `temp_upload.default_mode`, which itself defaults to `"local"`) |

Local directory uploads respect `.gitignore` files (root and nested). `ignore_dirs/include/exclude` apply on top of that.

For trusted gateway deployments, CLI flags can override these identity fields per command:

```bash
openviking --account acme --user alice ls viking://
```

For `add-resource`, upload filter flags are merged additively with `ovcli.conf` defaults:

```bash
# ovcli.conf: upload.exclude="*.log"
openviking add-resource ./docs --exclude "*.tmp"
# effective exclude sent to server: "*.log,*.tmp"
```

See [Deployment](./03-deployment.md) for details.

## server Section

When running OpenViking as an HTTP service, add a `server` section to `ov.conf`:

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key",
    "profile_enabled": false,
    "cors_origins": ["*"],
    "public_base_url": "https://ov.example.com",
    "upload_signed_ttl_seconds": 600,
    "temp_upload": {
      "default_mode": "local",
      "shared_max_size_bytes": 536870912,
      "shared_prefix": "viking://upload"
    },
    "user_config_defaults": {
      "add_targets": {
        "resource_uri": "viking://user/resources",
        "skill_uri": "viking://user/skills"
      }
    }
  }
}
```

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `host` | str | Bind address | `127.0.0.1` |
| `port` | int | Bind port | `1933` |
| `auth_mode` | str | Authentication mode: `"api_key"` or `"trusted"`. Default is `"api_key"` | `"api_key"` |
| `root_api_key` | str | Root API key for multi-tenant auth in `api_key` mode. In `trusted` mode it is optional on localhost, but required for any non-localhost deployment; it does not become the source of user identity | `null` |
| `profile_enabled` | bool | Whether to allow request-scoped cProfile via `profile=1` on HTTP requests. When disabled, the server ignores that query parameter. When enabled, the CLI can display the returned `profile`, while the Python HTTP client currently triggers profiling but does not automatically attach the top-level `profile` field to most SDK return values. | `false` |
| `cors_origins` | list | Allowed CORS origins | `["*"]` |
| `public_base_url` | str | Public-facing base URL emitted in MCP-issued upload instructions. Resolution order: env var `OPENVIKING_PUBLIC_BASE_URL` → this field → `X-Forwarded-Host`/`X-Forwarded-Proto` request headers → `Host` request header → listen-address fallback. Set this (or the env var) when the server runs behind a reverse proxy that does not forward `X-Forwarded-*` headers. | `null` |
| `upload_signed_ttl_seconds` | int | TTL in seconds for the one-shot tokens minted by the MCP `add_resource` tool for local-file uploads via `POST /api/v1/resources/temp_upload?token=...`. | `600` (10 minutes) |
| `temp_upload.default_mode` | str | Server-side default for `POST /api/v1/resources/temp_upload` when the client does not send `upload_mode`: `"local"` (per-instance disk, current single-node behavior) or `"shared"` (distributed shared store usable across replicas). | `"local"` |
| `temp_upload.shared_max_size_bytes` | int | Maximum size accepted in `shared` mode, in bytes. Requests above this size are rejected before object-store write. | `536870912` (512 MiB) |
| `temp_upload.shared_prefix` | str | URI prefix used when allocating shared `temp_file_id` objects. | `"viking://upload"` |
| `user_config_defaults.add_targets.resource_uri` | str | Deployment default resource add directory used when `add_resource` omits both `to` and `parent`. `viking://user/...` resolves per request user. | `null` |
| `user_config_defaults.add_targets.skill_uri` | str | Deployment default skill add root used when `add_skill` omits `target_uri`. Only `viking://user/skills` and `viking://agent/skills` are accepted. | `null` |

`api_key` mode uses API keys and is the default. `trusted` mode trusts `X-OpenViking-Account` / `X-OpenViking-User` headers from a trusted gateway or internal caller.

When `root_api_key` is configured in `api_key` mode, the server enables multi-tenant authentication. Use the Admin API to create accounts and user keys. In `trusted` mode, ordinary requests do not require user registration first; each request is resolved as `USER` from the injected identity headers. However, skipping `root_api_key` in `trusted` mode is allowed only on localhost. Development mode only applies when `auth_mode = "api_key"` and `root_api_key` is not set.

`user_config_defaults` sets defaults for new and existing users when they have no per-user override. For add operations, explicit request targets still win: `add_resource.to` / `add_resource.parent` take precedence over user defaults, and `add_skill.target_uri` takes precedence over user defaults. Per-user overrides are stored in `viking://user/{user_id}/settings/user_config.json`.

Supported add target URIs:

- `resource_uri` is used as the default `add_resource` parent directory, equivalent to `parent=<uri>, create_parent=true`. It must be a writable resource directory URI for the request user. Supported forms are `viking://resources` or `viking://resources/...`, `viking://user/resources` or `viking://user/resources/...`, `viking://user/{user_id}/resources` or `viking://user/{user_id}/resources/...`, and `viking://user/{user_id}/peers/{peer_id}/resources` or `viking://user/{user_id}/peers/{peer_id}/resources/...`. The `viking://user/...` shorthand resolves per request user.
- `skill_uri` is used as the default `add_skill` target root. In v1, only `viking://user/skills` and `viking://agent/skills` are accepted; explicit `viking://user/{user_id}/skills` is not accepted.

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
| `lock_expire` | float | Lock inactivity threshold (seconds). Locks not refreshed within this window are treated as stale and reclaimed. | `300.0` |

For details on the lock mechanism, see [Path Locks and Crash Recovery](../concepts/09-transaction.md).

## Task Tracker Persistence

The task tracker records async task state for endpoints that return a `task_id` (task types include `session_commit`, `add_resource`, `add_skill`, and `admin_reindex`). Task records are always persisted in AGFS, so a `task_id` returned by one instance can be looked up from another instance and task history survives a restart.

No `storage.task_tracker` configuration is required. If an older configuration still includes `storage.task_tracker`, OpenViking logs a warning and ignores it.

Task record files are stored under the owning account's system directory:

```text
/local/{account_id}/_system/tasks/{user_id}/{task_id}.json
```

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
| `api_key_hashing.enabled` | bool | Whether to apply Argon2id one-way hashing to API key values (independent of file-level `enabled`); see [Encryption Guide](./08-encryption.md) | `false` |

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
    "max_retries": 3,
    "text_source": "content_only",
    "max_input_tokens": 4096,
    "dense": {
      "provider": "volcengine",
      "api_key": "string",
      "model": "string",
      "dimension": 1024,
      "input": "multimodal",
      "encoding_format": "float|base64"
    }
  },
  "vlm": {
    "provider": "string",
    "api_key": "string",
    "model": "string",
    "api_base": "string",
    "thinking": false,
    "max_concurrent": 64,
    "max_retries": 3,
    "extra_headers": {},
    "extra_request_body": {},
    "stream": false
  },
  "rerank": {
    "provider": "volcengine|openai",
    "api_key": "string",
    "model": "string",
    "api_base": "string",
    "max_input_tokens": 0,
    "threshold": 0.1,
    "extra_headers": {}
  },
  "retrieval": {
    "hotness_alpha": 0.0,
    "score_propagation_alpha": 1.0
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
    "host": "127.0.0.1",
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
- For intermittent timeouts, increase `vlm.max_retries` moderately
- Try a smaller model
- For bulk ingestion, consider lowering `vlm.max_concurrent`

### Rate Limiting

```
Error: Rate limit exceeded
```

Volcengine has rate limits. Consider batch processing with delays or upgrading your plan.
- Lower `embedding.max_concurrent` / `vlm.max_concurrent` first
- Keep a small `max_retries` value for occasional `429`s; set it to `0` if you prefer fail-fast behavior

## Related Documentation

- [Volcengine Purchase Guide](./02-volcengine-purchase-guide.md) - API key setup
- [API Overview](../api/01-overview.md) - Client initialization
- [Server Deployment](./03-deployment.md) - Server configuration
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
