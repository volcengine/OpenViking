# Embedding Configuration

Configure embedding models for vector search.

## Volcengine Doubao (Recommended)

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-volcengine-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | str | `"volcengine"` |
| `api_key` | str | Volcengine API key |
| `model` | str | Model name |
| `dimension` | int | Vector dimension |
| `input` | str | Input type: `"text"` or `"multimodal"` |

### Available Models

| Model | Dimension | Input Type | Notes |
|-------|-----------|------------|-------|
| `doubao-embedding-vision-250615` | 1024 | multimodal | Recommended |
| `doubao-embedding-250615` | 1024 | text | Text only |

## Getting Volcengine API Key

1. Visit [Volcengine Console](https://console.volcengine.com/)
2. Navigate to **Ark** service
3. Create an API key
4. Copy the key to your configuration

## Environment Variable

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

Then in config:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024
    }
  }
}
```

## Programmatic Configuration

```python
from openviking.utils.config import EmbeddingConfig, DenseEmbeddingConfig

embedding_config = EmbeddingConfig(
    dense=DenseEmbeddingConfig(
        provider="volcengine",
        api_key="your-api-key",
        model="doubao-embedding-vision-250615",
        dimension=1024,
        input="multimodal"
    )
)
```

## Multimodal Support

With `input: "multimodal"`, OpenViking can embed:

- Text content
- Images (PNG, JPG, etc.)
- Mixed text and images

```python
# Multimodal embedding is used automatically
await client.add_resource("image.png")  # Image embedded
await client.add_resource("doc.pdf")    # Text + images embedded
```

## Troubleshooting

### API Key Error

```
Error: Invalid API key
```

Check your API key is correct and has embedding permissions.

### Dimension Mismatch

```
Error: Vector dimension mismatch
```

Ensure the `dimension` in config matches the model's output dimension.

### Rate Limiting

```
Error: Rate limit exceeded
```

Volcengine has rate limits. Consider:
- Batch processing with delays
- Upgrading your plan

## Related Documentation

- [Configuration](./configuration.md) - Main configuration
- [LLM Configuration](./llm.md) - LLM setup
- [Resources](../api/resources.md) - Adding resources
