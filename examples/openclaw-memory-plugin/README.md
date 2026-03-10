# OpenClaw + OpenViking Memory Plugin

Use OpenViking as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw).

## Quick Start

```bash
cd /path/to/OpenViking
npx ./examples/openclaw-memory-plugin/setup-helper
openclaw gateway
```

The setup helper checks the environment, creates `~/.openviking/ov.conf`, deploys the plugin, and configures OpenClaw automatically. It supports both **local** and **remote** modes and auto-detects multiple OpenClaw instances.

## Manual Setup

### Local Mode

Prerequisites: **OpenClaw** (`npm install -g openclaw`), **Python >= 3.10** with `openviking` (`pip install openviking --upgrade --force-reinstall`).

```bash
# Install plugin
mkdir -p ~/.openclaw/extensions/memory-openviking
cp examples/openclaw-memory-plugin/{index.ts,config.ts,client.ts,process-manager.ts,memory-ranking.ts,text-utils.ts,openclaw.plugin.json,package.json,.gitignore} \
   ~/.openclaw/extensions/memory-openviking/
cd ~/.openclaw/extensions/memory-openviking && npm install

# Configure (local mode — plugin auto-starts OpenViking)
openclaw config set plugins.enabled true
openclaw config set plugins.slots.memory memory-openviking
openclaw config set plugins.entries.memory-openviking.config.mode "local"
openclaw config set plugins.entries.memory-openviking.config.configPath "~/.openviking/ov.conf"
openclaw config set plugins.entries.memory-openviking.config.targetUri "viking://user/memories"
openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json

# Start
source ~/.openclaw/openviking.env && openclaw gateway
```

### Remote Mode

Prerequisites: **OpenClaw** only. No Python/OpenViking needed.

```bash
openclaw config set plugins.enabled true
openclaw config set plugins.slots.memory memory-openviking
openclaw config set plugins.entries.memory-openviking.config.mode "remote"
openclaw config set plugins.entries.memory-openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.memory-openviking.config.apiKey "your-api-key"  # optional
openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json

openclaw gateway
```

## Setup Helper Options

```
npx openclaw-openviking-setup-helper [options]

  -y, --yes              Non-interactive, use defaults
  --workdir <path>       OpenClaw config directory (default: ~/.openclaw)
  -h, --help             Show help

Env vars:
  OPENVIKING_PYTHON       Python path
  OPENVIKING_CONFIG_FILE  ov.conf path
  OPENVIKING_REPO         Local OpenViking repo path
  OPENVIKING_ARK_API_KEY  Volcengine API Key (skip prompt in -y mode)
```

## ov.conf Example

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Memory shows `disabled` / `memory-core` | `openclaw config set plugins.slots.memory memory-openviking` |
| `memory_store failed: fetch failed` | Check OpenViking is running; verify `ov.conf` and Python path |
| `health check timeout` | `lsof -ti tcp:1933 \| xargs kill -9` then restart |
| `extracted 0 memories` | Ensure `ov.conf` has valid `vlm` and `embedding.dense` with API key |
