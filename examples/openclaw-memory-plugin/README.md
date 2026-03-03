# OpenClaw + OpenViking Memory Plugin

Use OpenViking as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw).

## Quick Start

```bash
cd /path/to/OpenViking
npx ./examples/openclaw-memory-plugin/setup-helper
openclaw gateway
```

The setup helper checks the environment, creates `~/.openviking/ov.conf`, deploys the plugin, and configures OpenClaw automatically.

## Manual Setup

Prerequisites: **OpenClaw** (`npm install -g openclaw`), **Python >= 3.10** with `openviking` (`pip install openviking`).

```bash
# Install plugin
mkdir -p ~/.openclaw/extensions/memory-openviking
cp examples/openclaw-memory-plugin/{index.ts,config.ts,openclaw.plugin.json,package.json,.gitignore} \
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
openclaw gateway
```

## Setup Helper Options

```
npx openclaw-openviking-setup-helper [options]

  -y, --yes     Non-interactive, use defaults
  -h, --help    Show help

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
    "backend": "volcengine",
    "api_key": "<your-api-key>",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "backend": "volcengine",
      "api_key": "<your-api-key>",
      "model": "doubao-embedding-vision-250615",
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
