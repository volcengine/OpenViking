# Quick Start

Get started with OpenViking in 5 minutes.

## Prerequisites

Before using OpenViking, ensure your environment meets the following requirements:

- **Python Version**: 3.10 or higher
- **Operating System**: Linux, macOS, Windows
- **Network Connection**: Stable network connection required (for downloading dependencies and accessing model services)

## Installation

```bash
pip install openviking
```

## Model Preparation

OpenViking requires the following model capabilities:
- **VLM Model**: For image and content understanding
- **Embedding Model**: For vectorization and semantic retrieval

OpenViking supports multiple model services:
- **Volcengine (Doubao Models)**: Recommended, cost-effective with good performance, free quota for new users. For purchase and activation, see: [Volcengine Purchase Guide](../guides/02-volcengine-purchase-guide.md)
- **OpenAI Models**: Supports GPT-4V and other VLM models, plus OpenAI Embedding models
- **Other Custom Model Services**: Supports model services compatible with OpenAI API format

## Configuration

### Configuration File Template

Create a configuration file `~/.openviking/ov.conf`:

```json
{
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

For complete examples for each model provider, see [Configuration Guide - Examples](../guides/01-configuration.md#configuration-examples).

### Environment Variables

When the config file is at the default path `~/.openviking/ov.conf`, no additional setup is needed — OpenViking loads it automatically.

If the config file is at a different location, specify it via environment variable:

```bash
export OPENVIKING_CONFIG_FILE=/path/to/your/ov.conf
```

## Run Your First Example

### Create Python Script

Create `example.py`:

```python
import openviking as ov

# Initialize OpenViking client with data directory
client = ov.OpenViking(path="./data")

try:
    # Initialize the client
    client.initialize()

    # Add resource (supports URL, file, or directory)
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = add_result['root_uri']

    # Explore the resource tree structure
    ls_result = client.ls(root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Use glob to find markdown files
    glob_result = client.glob(pattern="**/*.md", uri=root_uri)
    if glob_result['matches']:
        content = client.read(glob_result['matches'][0])
        print(f"Content preview: {content[:200]}...\n")

    # Wait for semantic processing to complete
    print("Wait for semantic processing...")
    client.wait_processed()

    # Get abstract and overview of the resource
    abstract = client.abstract(root_uri)
    overview = client.overview(root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Perform semantic search
    results = client.find("what is openviking", target_uri=root_uri)
    print("Search results:")
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

    # Close the client
    client.close()

except Exception as e:
    print(f"Error: {e}")
```

### Run the Script

```bash
python example.py
```

### Expected Output

```
Directory structure:
...

Content preview: ...

Wait for semantic processing...
Abstract:
...

Overview:
...

Search results:
  viking://resources/... (score: 0.8523)
  ...
```

Congratulations! You have successfully run OpenViking.

## Server Mode

Want to run OpenViking as a shared service? See [Quick Start: Server Mode](03-quickstart-server.md).

## Next Steps

- [Configuration Guide](../guides/01-configuration.md) - Detailed configuration options
- [API Overview](../api/01-overview.md) - API reference
- [Resource Management](../api/02-resources.md) - Resource management API
