# Local Embedding Setup for Codex Subscribers

This guide is for ChatGPT Plus/Pro subscribers who use the Codex CLI and want OpenViking memory **without any extra API keys**:

- **VLM (extraction / summarization)** → covered by your ChatGPT subscription via the `openai-codex` OAuth provider
- **Embedding (vector search)** → a small model running entirely on your machine

Nothing you index ever leaves your machine for search; only VLM calls go to your existing Codex backend.

## What you end up with

```
Codex CLI ──MCP──► openviking-memory server (Node)
                        │
                        ▼
                openviking-server :1933
                ├── embedding: local (Ollama or llama.cpp GGUF)
                └── vlm: openai-codex (OAuth, no API key)
```

## Prerequisites

- Python 3.10+
- Codex CLI installed and signed in (`~/.codex/auth.json` exists)
- Node.js 22+ (for the Codex MCP plugin)
- Disk: ~700 MB for the recommended embedding model
- RAM guidance for the embedding model:

| System RAM | Recommended embedding model | Size | Dimension |
|---|---|---|---|
| ≤ 16 GB | `qwen3-embedding:0.6b` | ~639 MB | 1024 |
| 16–32 GB | `qwen3-embedding:8b` | ~4.7 GB | 1024 |
| Any (lightest) | `embeddinggemma:300m` | ~622 MB | 768 |
| Any (CPU-only, Chinese text) | `bge-small-zh-v1.5-f16` (GGUF) | ~24 MB | 512 |

> **Language note:** the built-in GGUF preset (`bge-small-zh-v1.5`) is optimized for Chinese. If your memories are mostly English, use the Ollama path with `qwen3-embedding:0.6b` or `embeddinggemma:300m`.

## Step 1 — Install OpenViking

```bash
pip install openviking
```

macOS users who prefer isolated installs:

```bash
brew install pipx && pipx ensurepath
pipx install openviking
```

## Step 2 — Choose your local embedding runtime

Two options:

- **Path A: Ollama (recommended).** Best model selection for English, GPU-accelerated on Apple Silicon, one extra background service.
- **Path B: llama.cpp GGUF (no daemon).** `pip install "openviking[local-embed]"`, model auto-downloads on first start. Currently only the Chinese-optimized `bge-small-zh-v1.5` preset ships.

### Path A: Ollama

```bash
# Install from https://ollama.com/download, then:
ollama pull qwen3-embedding:0.6b
```

### Path B: llama.cpp GGUF

```bash
pip install "openviking[local-embed]"
```

The model (~24 MB) downloads automatically to `~/.cache/openviking/models` on first server start. Note this path may compile `llama-cpp-python` from source if no wheel matches your platform (requires a C/C++ toolchain).

## Step 3 — Configure `~/.openviking/ov.conf`

> **Current wizard limitation:** `openviking-server init` offers Codex OAuth only in its *Cloud API* flow, and its two local flows only offer Ollama or API-key VLMs. The **local embedding + Codex VLM** combination this guide targets must be configured manually today. (This is the gap the planned installer profiles close.)

First, import your Codex auth. The easiest way is to run `openviking-server init`, pick **Cloud API**, choose **OpenAI Codex** as VLM provider when prompted, and accept the offer to import auth from `~/.codex/auth.json` — then cancel before saving and write the config below manually. OpenViking stores its own token copy at `~/.openviking/codex_auth.json` and refreshes it independently.

Then write `~/.openviking/ov.conf`:

**Ollama embedding + Codex VLM:**

```json
{
  "server": { "host": "127.0.0.1", "port": 1933 },
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
    "provider": "openai-codex",
    "model": "gpt-5.4",
    "api_base": "https://chatgpt.com/backend-api/codex",
    "temperature": 0.0,
    "max_retries": 2
  }
}
```

**GGUF embedding + Codex VLM** — replace the `embedding` block with:

```json
"embedding": {
  "dense": {
    "provider": "local",
    "model": "bge-small-zh-v1.5-f16",
    "dimension": 512
  }
}
```

Useful environment overrides for the Codex provider:

| Variable | Purpose |
|---|---|
| `OPENVIKING_CODEX_AUTH_PATH` | Where OpenViking stores its Codex token (default `~/.openviking/codex_auth.json`) |
| `OPENVIKING_CODEX_BOOTSTRAP_PATH` | Alternate Codex CLI `auth.json` to import from |
| `OPENVIKING_CODEX_BASE_URL` | Override the Codex backend URL |

> **Pick your embedding model once.** The vector dimension is baked into the index. Switching models (or dimensions) later means your existing memories must be re-embedded/re-imported.

## Step 4 — Validate and start

```bash
openviking-server doctor
openviking-server
curl http://localhost:1933/health
```

## Step 5 — Wire up Codex

Build the MCP memory server and register it:

```bash
cd examples/codex-memory-plugin
npm install
npm run build

codex mcp add openviking-memory -- \
  node /ABS/PATH/TO/OpenViking/examples/codex-memory-plugin/servers/memory-server.js
```

Codex gains four explicit tools: `openviking_recall`, `openviking_store`, `openviking_forget`, `openviking_health`. Note this integration is intentionally MCP-only — there is no automatic recall/capture on every turn (unlike the Claude Code plugin); the model calls the tools when it decides to.

Connection settings are read from the same `~/.openviking/ov.conf`. Optional env overrides: `OPENVIKING_AGENT_ID` (default `codex`), `OPENVIKING_RECALL_LIMIT` (default 6), `OPENVIKING_SCORE_THRESHOLD` (default 0.01).

## Step 6 — Smoke test

In a Codex session:

1. Ask it to run `openviking_health` — expect a reachable server.
2. "Remember that I prefer tabs over spaces" → should invoke `openviking_store`.
3. New session: "What are my formatting preferences?" → should invoke `openviking_recall` and find the memory.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` fails on embedding | Ollama not running (`ollama serve`), or model not pulled (`ollama pull qwen3-embedding:0.6b`) |
| `llama-cpp-python is not installed` | `pip install "openviking[local-embed]"` |
| llama-cpp-python build fails | Install a C/C++ toolchain (Xcode CLT on macOS; GCC 9+/Clang 11+ elsewhere) or switch to the Ollama path |
| Codex VLM auth errors | Re-run the auth import; check `~/.openviking/codex_auth.json` exists and Codex CLI is still signed in |
| Port 1933 in use | Change `server.port` in `ov.conf` (the MCP plugin picks up the new value from the same file) |
| Recall returns nothing | Memory extraction is VLM-dependent — check server logs for `openai-codex` call failures |
