# LLM Configuration

Configure LLM for semantic extraction (L0/L1 generation) and reranking.

## VLM (Vision Language Model)

Used for generating L0/L1 content from resources.

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-volcengine-api-key",
    "model": "doubao-seed-1-8-251228",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `api_key` | str | Volcengine API key |
| `model` | str | Model name |
| `base_url` | str | API endpoint (optional) |

### Available Models

| Model | Notes |
|-------|-------|
| `doubao-seed-1-8-251228` | Recommended for semantic extraction |
| `doubao-pro-32k` | For longer context |

## Rerank Model

Used for search result refinement.

```json
{
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-volcengine-api-key",
    "model": "doubao-rerank-250615"
  }
}
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | str | `"volcengine"` |
| `api_key` | str | Volcengine API key |
| `model` | str | Model name |

## Environment Variables

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

## Programmatic Configuration

```python
from openviking.utils.config import OpenVikingConfig

config = OpenVikingConfig(
    vlm={
        "api_key": "your-api-key",
        "model": "doubao-seed-1-8-251228"
    },
    rerank={
        "provider": "volcengine",
        "api_key": "your-api-key",
        "model": "doubao-rerank-250615"
    }
)
```

## How LLMs Are Used

### L0/L1 Generation

When resources are added, VLM generates:

1. **L0 (Abstract)**: ~100 token summary
2. **L1 (Overview)**: ~2k token overview with navigation

```
Resource → Parser → VLM → L0/L1 → Storage
```

### Reranking

During search, rerank model refines results:

```
Query → Vector Search → Candidates → Rerank → Final Results
```

## Disabling LLM Features

### Without VLM

If VLM is not configured:
- L0/L1 will be generated from content directly (less semantic)
- Multimodal resources may have limited descriptions

### Without Rerank

If rerank is not configured:
- Search uses vector similarity only
- Results may be less accurate

## Troubleshooting

### VLM Timeout

```
Error: VLM request timeout
```

- Check network connectivity
- Increase timeout in config
- Try a smaller model

### Rerank Not Working

```
Warning: Rerank not configured, using vector search only
```

Add rerank configuration to enable two-stage retrieval.

## Related Documentation

- [Configuration](./configuration.md) - Main configuration
- [Embedding Configuration](./embedding.md) - Embedding setup
- [Context Layers](../concepts/context-layers.md) - L0/L1/L2
