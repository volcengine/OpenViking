# openviking-opencode

OpenViking plugin for [OpenCode](https://opencode.ai). Injects your indexed code repos into the AI's context and auto-starts the OpenViking server when needed.

## Prerequisites

Install the latest OpenViking and configure `~/.openviking/ov.conf`:

```bash
pip install openviking --upgrade
```

```json
{
  "storage": {
    "workspace": "/path/to/your/workspace"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "your-embedding-model",
      "api_key": "your-api-key",
      "api_base": "https://your-provider/v1",
      "dimension": 1024
    },
    "max_concurrent": 100
  },
  "vlm": {
    "provider": "openai",
    "model": "your-vlm-model",
    "api_key": "your-api-key",
    "api_base": "https://your-provider/v1"
  }
}
```

For other providers (Volcengine, Anthropic, DeepSeek, Ollama, etc.) see the [OpenViking docs](https://www.openviking.ai/docs).

Before starting OpenCode, make sure the OpenViking server is running. If it's not already started:

```bash
openviking-server --config ~/.openviking/ov.conf > /tmp/openviking.log 2>&1 &
```

## Usage in OpenCode

Add the plugin to `~/.config/opencode/opencode.json`:

```json
{
  "plugin": ["openviking-opencode"]
}
```

Restart OpenCode — the skill is installed automatically.

**Index a repo** (just ask in chat):
```
"Add https://github.com/tiangolo/fastapi to OpenViking"
```

**Search** — once repos are indexed, the AI searches them automatically when relevant. You can also trigger it explicitly:
```
"How does fastapi handle dependency injection?"
"Use openviking to find how JWT tokens are verified"
```

