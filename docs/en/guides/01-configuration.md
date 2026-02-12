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
    "model": "doubao-seed-1-8-251228"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  },
  "storage": {
    "agfs": {
      "backend": "local",
      "path": "./data"
    },
    "vectordb": {
      "backend": "local",
      "path": "./data"
    }
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
    "model"    : "doubao-seed-1-8-251228"
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
| `provider` | str | `"volcengine"`, `"openai"`, or `"vikingdb"` |
| `api_key` | str | API key |
| `model` | str | Model name |
| `dimension` | int | Vector dimension |
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

#### Sparse Embedding

```json
{
  "embedding": {
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "bm25-sparse-v1"
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
      "model": "bm25-sparse-v1"
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
    "model": "doubao-seed-1-8-251228",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `api_key` | str | API key |
| `model` | str | Model name |
| `base_url` | str | API endpoint (optional) |

**Available Models**

| Model | Notes |
|-------|-------|
| `doubao-seed-1-8-251228` | Recommended for semantic extraction |
| `doubao-pro-32k` | For longer context |

When resources are added, VLM generates:

1. **L0 (Abstract)**: ~100 token summary
2. **L1 (Overview)**: ~2k token overview with navigation

If VLM is not configured, L0/L1 will be generated from content directly (less semantic), and multimodal resources may have limited descriptions.

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

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | str | `"volcengine"` |
| `api_key` | str | API key |
| `model` | str | Model name |

If rerank is not configured, search uses vector similarity only.

### storage

Storage backend configuration.

```json
{
  "storage": {
    "agfs": {
      "backend": "local",
      "path": "./data",
      "timeout": 30.0
    },
    "vectordb": {
      "backend": "local",
      "path": "./data"
    }
  }
}
```

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
python -m openviking serve --config /path/to/ov.conf
```

### ov.conf

The config sections documented above (embedding, vlm, rerank, storage) all belong to `ov.conf`. SDK embedded mode and server share this file.

### ovcli.conf

Config file for the HTTP client (`SyncHTTPClient` / `AsyncHTTPClient`) and CLI to connect to a remote server:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key",
  "output": "table"
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `url` | Server address | (required) |
| `api_key` | API key for authentication | `null` (no auth) |
| `output` | Default output format: `"table"` or `"json"` | `"table"` |

See [Deployment](./03-deployment.md) for details.

## server Section

When running OpenViking as an HTTP service, add a `server` section to `ov.conf`:

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secret-key",
    "cors_origins": ["*"]
  }
}
```

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `host` | str | Bind address | `0.0.0.0` |
| `port` | int | Bind port | `1933` |
| `api_key` | str | API Key auth, disabled if not set | `null` |
| `cors_origins` | list | Allowed CORS origins | `["*"]` |

For startup and deployment details see [Deployment](./03-deployment.md), for authentication see [Authentication](./04-authentication.md).

## Full Schema

```json
{
  "embedding": {
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
    "base_url": "string"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "string",
    "model": "string"
  },
  "storage": {
    "agfs": {
      "backend": "local|remote",
      "path": "string",
      "url": "string",
      "timeout": 30.0
    },
    "vectordb": {
      "backend": "local|remote",
      "path": "string",
      "url": "string"
    }
  },
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "string",
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

- [Volcengine Purchase Guide](./volcengine-purchase-guide.md) - API key setup
- [API Overview](../api/01-overview.md) - Client initialization
- [Server Deployment](./03-deployment.md) - Server configuration
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
