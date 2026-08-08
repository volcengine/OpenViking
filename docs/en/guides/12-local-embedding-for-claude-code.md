# Local Embedding Setup for Claude Code Subscribers

This guide is for Claude Pro/Max subscribers who use Claude Code and want OpenViking memory with a **small local embedding model** — no embedding API key, and vector search that never leaves your machine.

**One thing to know up front:** unlike Codex (which has an OAuth VLM backend), OpenViking has **no Anthropic-subscription VLM backend** — your Claude subscription covers Claude Code itself, not OpenViking's internal extraction/summarization calls. So you pick one of two VLM strategies:

- **Fully local (recommended here):** Ollama serves both the embedding model *and* a small VLM. Zero API keys, zero cloud calls.
- **Hybrid:** local embedding + a cloud VLM API key (VolcEngine, OpenAI, Kimi, GLM, …) for higher-quality memory extraction.

## What you end up with (fully local)

```
Claude Code
├── UserPromptSubmit hook ── auto-recall (memories injected each turn)
├── Stop hook ────────────── auto-capture (session extracted to memory)
└── MCP tools ─────────────► openviking-server :1933
                             ├── embedding: ollama/qwen3-embedding:0.6b
                             └── vlm: ollama/qwen3.5:2b (via litellm)
```

## Prerequisites

- Python 3.10+
- Claude Code installed
- Node.js (the plugin bootstraps its own runtime on first session start)
- RAM sizing — the fully-local path runs *two* models, so size both together:

| System RAM | Embedding | VLM | Total download |
|---|---|---|---|
| ≤ 8 GB | `qwen3-embedding:0.6b` (1024d) | `qwen3.5:2b` | ~3.3 GB |
| 8–16 GB | `qwen3-embedding:0.6b` (1024d) | `qwen3.5:4b` | ~4.1 GB |
| 16–32 GB | `qwen3-embedding:8b` (1024d) | `qwen3.5:9b` | ~11 GB |
| 32–64 GB | `qwen3-embedding:8b` (1024d) | `gemma4:e4b` | ~14 GB |

These are the same tiers `openviking-server init` auto-recommends from detected RAM.

> **Language note:** for a lighter embedding option, `embeddinggemma:300m` (768d, ~622 MB) also works well for English. The tiny built-in GGUF preset (`bge-small-zh-v1.5`) is Chinese-optimized — prefer the Ollama models for English memories.

## Step 1 — Install OpenViking

```bash
pip install openviking
```

macOS users who prefer isolated installs:

```bash
brew install pipx && pipx ensurepath
pipx install openviking
```

## Step 2 — Run the setup wizard

The fully-local path is a first-class wizard flow:

```bash
openviking-server init
```

1. Choose **Local models via Ollama** (option 3 — recommended for macOS / Apple Silicon).
2. The wizard checks for Ollama, offers to install and start it if missing.
3. It detects your RAM and pre-selects the recommended embedding + VLM presets from the table above (marked with `*`) — accept or override.
4. It offers to `ollama pull` anything not yet downloaded.
5. Choose **Local (127.0.0.1)** for server binding.
6. Save. Config lands in `~/.openviking/ov.conf` (existing config is backed up as `.bak`).

The generated config looks like:

```json
{
  "server": { "host": "127.0.0.1" },
  "storage": { "workspace": "~/.openviking/data" },
  "embedding": {
    "dense": {
      "provider": "ollama",
      "model": "qwen3-embedding:0.6b",
      "api_base": "http://localhost:11434/v1",
      "dimension": 1024,
      "input": "text"
    }
  },
  "vlm": {
    "provider": "litellm",
    "model": "ollama/qwen3.5:2b",
    "api_key": "no-key",
    "api_base": "http://localhost:11434",
    "temperature": 0.0,
    "max_retries": 2
  }
}
```

**Hybrid variant:** choose **Local embedding via llama.cpp** (option 2) instead, then pick **Use Cloud API for VLM** when prompted — you'll enter one VLM API key but embedding stays local. Or edit the `vlm` block of the config above to point at any supported cloud provider.

> **Pick your embedding model once.** The vector dimension is baked into the index. Switching models (or dimensions) later means your existing memories must be re-embedded/re-imported.

## Step 3 — Validate and start

```bash
openviking-server doctor
openviking-server
curl http://localhost:1933/health
```

Keep the server running while you use Claude Code (or register it as a login service — see the deployment guide).

## Step 4 — Install the Claude Code plugin

Inside Claude Code:

```
/plugin marketplace add Castor6/openviking-plugins
/plugin install claude-code-memory-plugin@openviking-plugin
```

Then start a new session (`claude`). On first `SessionStart` the plugin bootstraps its own Node runtime (into `${CLAUDE_PLUGIN_DATA}/runtime`, falling back to `~/.openviking/claude-code-memory-plugin/runtime`) — no manual `npm install`.

The plugin reads the same `~/.openviking/ov.conf`: it derives `baseUrl` from `server.host` + `server.port` and, if set, uses `server.root_api_key` as its API key.

What you get automatically:

- **Auto-recall** — every prompt triggers a semantic search over `viking://user/memories` and `viking://agent/memories`; relevant memories are injected as a system message.
- **Auto-capture** — when a turn ends, the transcript is captured and the server extracts long-term memories from it (this is where the local VLM does its work).
- **MCP tools** — explicit memory operations when you want them.

Tune behavior in an optional `claude_code` section of `ov.conf` (`autoRecall`, `recallLimit`, `scoreThreshold`, `autoCapture`, `captureMode`, `agentId`, …).

## Step 5 — Smoke test

1. In a session: "Remember that my staging server is deploy@10.0.0.5."
2. End the turn, start a **new** session.
3. Ask "how do I reach staging?" — the answer should arrive with a recalled-memories system message.

If recall is empty, give extraction a moment (capture runs on the `Stop` hook and extraction is asynchronous on the server), then check the logs below.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` fails on embedding or VLM | Ollama not running (`ollama serve`) or model not pulled (`ollama pull <model>`) |
| Hooks seem to do nothing | Enable `claude_code.debug` or `OPENVIKING_DEBUG=1`, then check `~/.openviking/logs/cc-hooks.log` |
| Recall slow / prompt submission lags | `UserPromptSubmit` hook has an 8s budget; lower `recallLimit` or use a smaller embedding model |
| Capture times out | `Stop` hook allows 45s; keep `claude_code.captureTimeoutMs` below that, or use a smaller / faster VLM |
| Memories extracted poorly | Small local VLMs trade quality for privacy — move up a VLM tier or switch to the hybrid variant |
| Port 1933 in use | Change `server.port` in `ov.conf`; the plugin picks it up from the same file |
