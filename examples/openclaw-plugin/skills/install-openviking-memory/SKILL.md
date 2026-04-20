---
name: openviking-memory
description: OpenViking long-term memory plugin guide. Once installed, the plugin automatically remembers important facts from conversations and recalls relevant context before responding.
---

# OpenViking Memory Guide

## How It Works

- **Auto-Capture**: At `afterTurn` (end of one user turn run), automatically extracts memories from user/assistant messages
  - `semantic` mode: captures all qualifying user text, relying on OpenViking's extraction pipeline to filter
  - `keyword` mode: only captures text matching trigger words (e.g. "remember", "preference", etc.)
- **Auto-Recall**: At `before_prompt_build`, automatically searches for relevant memories and injects them into context

## Available Tools

### memory_recall — Search Memories

Searches long-term memories in OpenViking, returns relevant results.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Search query text |
| `limit` | No | Maximum number of results (defaults to plugin config) |
| `scoreThreshold` | No | Minimum relevance score 0-1 (defaults to plugin config) |
| `targetUri` | No | Search scope URI (defaults to plugin config) |

Example: User asks "What programming language did I say I like?"

### memory_store — Manual Store

Writes text to an OpenViking session and runs memory extraction.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `text` | Yes | Information text to store |
| `role` | No | Session role (default `user`) |
| `sessionId` | No | Existing OpenViking session ID |

Example: User says "Remember my email is xxx@example.com"

### memory_forget — Delete Memories

Delete by exact URI, or search and delete.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `uri` | No | Exact memory URI (direct delete) |
| `query` | No | Search query (find then delete) |
| `targetUri` | No | Search scope URI |
| `limit` | No | Search limit (default 5) |
| `scoreThreshold` | No | Minimum relevance score |

Example: User says "Forget my phone number"

## Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `mode` | `remote` | `local` (start local server) or `remote` (connect to remote) |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking server URL (remote mode) |
| `apiKey` | — | OpenViking API Key (optional) |
| `agentId` | `default` | Identifies this agent to OpenViking |
| `configPath` | `~/.openviking/ov.conf` | Config file path (local mode) |
| `port` | `1933` | Local server port (local mode) |
| `targetUri` | `viking://user/memories` | Default search scope |
| `autoCapture` | `true` | Automatically capture memories |
| `captureMode` | `semantic` | Capture mode: `semantic` / `keyword` |
| `captureMaxLength` | `24000` | Maximum text length per capture |
| `autoRecall` | `true` | Automatically recall and inject context |
| `recallLimit` | `6` | Maximum memories injected during auto-recall |
| `recallScoreThreshold` | `0.01` | Minimum relevance score for recall |

## Daily Operations

```bash
# Start (local mode: source env first)
source ~/.openclaw/openviking.env && openclaw gateway

# Start (remote mode: no env needed)
openclaw gateway

# Check status
openclaw status
openclaw config get plugins.slots.contextEngine

# Disable memory
openclaw config set plugins.slots.contextEngine legacy

# Enable memory
openclaw config set plugins.slots.contextEngine openviking
```

Restart the gateway after changing the slot.

## Multi-Instance Support

If you have multiple OpenClaw instances, use `--workdir` to target a specific one:

```bash
# Install script
curl -fsSL ... | bash -s -- --workdir ~/.openclaw-openclaw-second

# Setup helper
npx ./examples/openclaw-plugin/setup-helper --workdir ~/.openclaw-openclaw-second

# Manual config (prefix openclaw commands)
OPENCLAW_STATE_DIR=~/.openclaw-openclaw-second openclaw config set ...
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `extracted 0 memories` | Wrong API Key or model name | Check `api_key` and `model` in `ov.conf` |
| `port occupied` | Port used by another process | Change port: `openclaw config set plugins.entries.openviking.config.port 1934` |
| Plugin not loaded | Env file not sourced or slot not configured | Check `openclaw status` output |
| Inaccurate recall | recallScoreThreshold too low | Increase threshold or adjust recallLimit |
