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

See [Embedding Configuration](./embedding.md) for details.

### vlm

Vision Language Model for semantic extraction.

```json
{
  "vlm": {
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

See [LLM Configuration](./llm.md) for details.

### rerank

Reranking model for search refinement.

```json
{
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  }
}
```

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

Configuration values can be set via environment variables:

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

## Configuration Reference

### Full Schema

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

## Related Documentation

- [Embedding Configuration](./embedding.md) - Embedding setup
- [LLM Configuration](./llm.md) - LLM setup
- [Client](../api/client.md) - Client initialization
