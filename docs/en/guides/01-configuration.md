# Configuration

OpenViking uses a JSON configuration file (`ov.conf`) for settings.

## Configuration File

Create `ov.conf` in your project directory:

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

## Configuration Sections

### embedding

Embedding model configuration for vector search.

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

## Environment Variables

```bash
export VOLCENGINE_API_KEY="your-api-key"
export OPENVIKING_DATA_PATH="./data"
```

## Configuration Priority

1. Constructor parameters (highest)
2. Config object
3. Configuration file (`ov.conf`)
4. Environment variables
5. Default values (lowest)

## Programmatic Configuration

```python
from openviking.utils.config import (
    OpenVikingConfig,
    StorageConfig,
    AGFSConfig,
    VectorDBBackendConfig,
    EmbeddingConfig,
    DenseEmbeddingConfig
)

config = OpenVikingConfig(
    storage=StorageConfig(
        agfs=AGFSConfig(
            backend="local",
            path="./custom_data",
        ),
        vectordb=VectorDBBackendConfig(
            backend="local",
            path="./custom_data",
        )
    ),
    embedding=EmbeddingConfig(
        dense=DenseEmbeddingConfig(
            provider="volcengine",
            api_key="your-api-key",
            model="doubao-embedding-vision-250615",
            dimension=1024
        )
    )
)

client = ov.AsyncOpenViking(config=config)
```

## Full Configuration Schema

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
  "user": "string"
}
```

Notes:
- `storage.vectordb.sparse_weight` controls hybrid (dense + sparse) indexing/search. It only takes effect when you use a hybrid index; set it > 0 to enable sparse signals.

## Server Configuration

When running OpenViking as an HTTP server, the server reads its configuration from the same JSON config file (via `--config` or `OPENVIKING_CONFIG_FILE`):

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secret-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "path": "/data/openviking"
  }
}
```

Server configuration can also be set via environment variables:

| Variable | Description |
|----------|-------------|
| `OPENVIKING_HOST` | Server host |
| `OPENVIKING_PORT` | Server port |
| `OPENVIKING_API_KEY` | API key for authentication |
| `OPENVIKING_PATH` | Storage path |

See [Server Deployment](./03-deployment.md) for full details.

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
