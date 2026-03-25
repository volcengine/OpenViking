# OpenClaw + OpenViking Context-Engine Plugin

Use [OpenViking](https://github.com/volcengine/OpenViking) as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw). In OpenClaw, this plugin is registered as the `openviking` context engine. Once installed, OpenClaw will automatically **remember** important information from conversations and **recall** relevant context before responding.

> **ℹ️ Historical Compatibility Note**
>
> Legacy OpenViking/OpenClaw integrations had a known issue around OpenClaw `2026.3.12` where conversations could hang after the plugin loaded.
> That issue affected the legacy plugin path; the current context-engine Plugin 2.0 described in this document is not affected, so new installations do not need to downgrade OpenClaw for this reason.
> Plugin 2.0 is also not backward-compatible with the legacy `memory-openviking` plugin and its configuration, so upgrades must replace the old setup instead of mixing the two versions.
> Plugin 2.0 also depends on OpenClaw's context-engine capability and does not support older OpenClaw releases; upgrade OpenClaw first before using this plugin.
> If you are troubleshooting a legacy deployment, see [#591](https://github.com/volcengine/OpenViking/issues/591) and upstream fix PRs: openclaw/openclaw#34673, openclaw/openclaw#33547.

> **🚀 Plugin 2.0 (Context-Engine Architecture)**
>
> This document covers the current OpenViking Plugin 2.0 built on the context-engine architecture, which is the recommended integration path for AI coding assistants.
> For design background and earlier discussion, see:
> https://github.com/volcengine/OpenViking/discussions/525

---

## Table of Contents

- [One-Click Installation](#one-click-installation)
- [Manual Setup](#manual-setup)
  - [Prerequisites](#prerequisites)
  - [Local Mode (Personal Use)](#local-mode-personal-use)
  - [Remote Mode (Team Sharing)](#remote-mode-team-sharing)
  - [Volcengine ECS Deployment](#volcengine-ecs-deployment)
- [Starting & Verification](#starting--verification)
- [Configuration Reference](#configuration-reference)
- [Daily Usage](#daily-usage)
- [Web Console (Visualization)](#web-console-visualization)
- [Troubleshooting](#troubleshooting)
- [Uninstallation](#uninstallation)

---

## One-Click Installation

For users who want a quick local experience. The setup helper handles environment detection, dependency installation, and config file generation automatically.

### Method A: npm Install (Recommended, Cross-platform)

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

### Method B: curl One-Click (Linux / macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash
```

The setup helper will walk you through:

1. **Environment check** — Detects Python >= 3.10, Node.js, cmake, etc.
2. **Select OpenClaw instance** — If multiple instances are installed locally, lists them for you to choose
3. **Select deployment mode** — Local or Remote (see below)
4. **Generate config** — Writes `~/.openviking/ov.conf` and `~/.openclaw/openviking.env` automatically

<details>
<summary>Setup helper options</summary>

```
ov-install [options]

  -y, --yes              Non-interactive, use defaults
  --workdir <path>       OpenClaw config directory (default: ~/.openclaw)
  -h, --help             Show help

Env vars:
  OPENVIKING_PYTHON       Python path
  OPENVIKING_CONFIG_FILE  ov.conf path
  OPENVIKING_REPO         Local OpenViking repo path
  OPENVIKING_ARK_API_KEY  Volcengine API Key (skip prompt in -y mode)
```

</details>

---

## Manual Setup

### Prerequisites

| Component | Version | Purpose |
|-----------|---------|---------|
| **Python** | >= 3.10 | OpenViking runtime (Local mode) |
| **Node.js** | >= 22 | OpenClaw runtime |
| **Volcengine Ark API Key** | — | Embedding + VLM model calls |

```bash
python3 --version   # >= 3.10
node -v              # >= v22
openclaw --version   # installed
```

- Python: https://www.python.org/downloads/
- Node.js: https://nodejs.org/
- OpenClaw: `npm install -g openclaw && openclaw onboard`

---

### Local Mode (Personal Use)

The simplest option — nearly zero configuration. The memory service runs alongside your OpenClaw agent locally. You only need a Volcengine Ark API Key.

#### Step 1: Install OpenViking

```bash
python3 -m pip install openviking --upgrade
```

Verify: `python3 -c "import openviking; print('ok')"`

> Hit `externally-managed-environment`? Use the one-click installer (handles venv automatically) or create one manually:
> ```bash
> python3 -m venv ~/.openviking/venv && ~/.openviking/venv/bin/pip install openviking
> ```

#### Step 2: Run the Setup Helper

```bash
# Method A: npm install (recommended, cross-platform)
npm install -g openclaw-openviking-setup-helper
ov-install

# Method B: curl one-click (Linux / macOS)
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash
```

Select **local** mode, keep defaults, and enter your Ark API Key.

Generated config files:
- `~/.openviking/ov.conf` — OpenViking service config
- `~/.openclaw/openviking.env` — Environment variables (Python path, etc.)

#### Step 3: Start

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

> In Local mode you must `source` the env file first — the plugin auto-starts an OpenViking subprocess.

#### Step 4: Verify

```bash
openclaw status
# ContextEngine row should show: enabled (plugin openviking)
```

---

### Remote Mode (Team Sharing)

For multiple OpenClaw instances or team use. Deploy a standalone OpenViking service that is shared across agents. **No Python/OpenViking needed on the client side.**

#### Step 1: Deploy the OpenViking Service

Edit `~/.openviking/ov.conf` — set `root_api_key` to enable multi-tenancy:

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "root_api_key": "<your-root-api-key>",
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "~/.openviking/data",
    "vectordb": {
      "name": "context",
      "backend": "local"
    },
    "agfs": {
      "log_level": "warn",
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-ark-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-ark-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
```

Start the service:

```bash
openviking-server
```

#### Step 2: Create Team & Users

```bash
# Create team + admin
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-api-key>" \
  -d '{
    "account_id": "my-team",
    "admin_user_id": "admin"
  }'

# Add member
curl -X POST http://localhost:1933/api/v1/admin/accounts/my-team/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{
    "user_id": "xiaomei",
    "role": "user"
  }'
```

#### Step 3: Configure the OpenClaw Plugin

```bash
openclaw plugins enable openviking
openclaw config set gateway.mode local
openclaw config set plugins.slots.contextEngine openviking
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.openviking.config.apiKey "<user-api-key>"
openclaw config set plugins.entries.openviking.config.agentId "<agent-id>"
openclaw config set plugins.entries.openviking.config.autoRecall true --json
openclaw config set plugins.entries.openviking.config.autoCapture true --json
```

#### Step 4: Start & Verify

```bash
# Remote mode — no env sourcing needed
openclaw gateway restart
openclaw status
```

---

### Volcengine ECS Deployment

Deploy OpenClaw + OpenViking on Volcengine ECS. See [Volcengine docs](https://www.volcengine.com/docs/6396/2249500?lang=zh) for details.

> ECS instances restrict global pip installs under root to protect system Python. Create a venv first.

```bash
# 1. Install
npm install -g openclaw-openviking-setup-helper
ov-install

# 2. Load environment
source /root/.openclaw/openviking.env

# 3. Start OpenViking server
python -m openviking.server.bootstrap

# 4. Start Web Console (ensure security group allows TCP 8020 inbound)
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```

Access `http://<public-ip>:8020` to use the Web Console.

---

## Starting & Verification

### Local Mode

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

### Remote Mode

```bash
openclaw gateway restart
```

### Check Plugin Status

```bash
openclaw status
# ContextEngine row should show: enabled (plugin openviking)
```

### View Plugin Config

```bash
openclaw config get plugins.entries.openviking.config
```

---

## Configuration Reference

### `~/.openviking/ov.conf` (Local Mode)

```json
{
  "root_api_key": null,
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "~/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local", "port": 1833 }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-ark-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-ark-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

> `root_api_key`: When set, all HTTP requests must include the `X-API-Key` header. Defaults to `null` in Local mode (auth disabled).

### Plugin Config Options

| Option | Default | Description |
|--------|---------|-------------|
| `mode` | `remote` | `local` (start local server) or `remote` (connect to remote) |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking server URL (Remote mode) |
| `apiKey` | — | OpenViking API Key (optional) |
| `agentId` | auto-generated | Agent identifier, distinguishes OpenClaw instances. Auto-generates `openclaw-<hostname>-<random>` if unset |
| `configPath` | `~/.openviking/ov.conf` | Config file path (Local mode) |
| `port` | `1933` | Local server port (Local mode) |
| `targetUri` | `viking://user/memories` | Default memory search scope |
| `autoCapture` | `true` | Auto-extract memories after conversations |
| `captureMode` | `semantic` | Extraction mode: `semantic` (full semantic) / `keyword` (trigger-word only) |
| `captureMaxLength` | `24000` | Max text length per capture |
| `autoRecall` | `true` | Auto-recall relevant memories before conversations |
| `recallLimit` | `6` | Max memories injected during auto-recall |
| `recallScoreThreshold` | `0.01` | Minimum relevance score for recall |
| `ingestReplyAssist` | `true` | Add reply guidance when multi-party conversation text is detected |

### `~/.openclaw/openviking.env`

Auto-generated by the setup helper, stores environment variables like the Python path:

```bash
export OPENVIKING_PYTHON='/usr/local/bin/python3'
```

---

## Daily Usage

```bash
# Start (Local mode — source env first)
source ~/.openclaw/openviking.env && openclaw gateway

# Start (Remote mode — no env needed)
openclaw gateway

# Disable the context engine
openclaw config set plugins.slots.contextEngine legacy

# Re-enable OpenViking as the context engine
openclaw config set plugins.slots.contextEngine openviking
```

> Restart the gateway after changing the context-engine slot.

---

## Web Console (Visualization)

OpenViking provides a Web Console for debugging and inspecting stored memories.

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933 \
  --write-enabled
```

Open http://127.0.0.1:8020 in your browser.

---

## Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Conversation hangs, no response | Usually a legacy pre-2.0 integration affected by the historical OpenClaw `2026.3.12` issue | If you are on the legacy path, see [#591](https://github.com/volcengine/OpenViking/issues/591) and temporarily downgrade to `2026.3.11`; for current installs, migrate to Plugin 2.0 |
| `registerContextEngine is unavailable` in logs | OpenClaw version is too old and does not expose the context-engine API required by Plugin 2.0 | Upgrade OpenClaw to a current release, then restart the gateway and verify `openclaw status` shows `openviking` as the ContextEngine |
| Agent hangs silently, no output | auto-recall missing timeout protection | Disable auto-recall temporarily: `openclaw config set plugins.entries.openviking.config.autoRecall false --json`, or apply the patch in [#673](https://github.com/volcengine/OpenViking/issues/673) |
| ContextEngine is not `openviking` | Plugin slot not configured | `openclaw config set plugins.slots.contextEngine openviking` |
| `memory_store failed: fetch failed` | OpenViking not running | Check `ov.conf` and Python path; verify service is up |
| `health check timeout` | Port held by stale process | `lsof -ti tcp:1933 \| xargs kill -9`, then restart |
| `extracted 0 memories` | Wrong API Key or model name | Check `api_key` and `model` in `ov.conf` |
| `port occupied` | Port used by another process | Change port: `openclaw config set plugins.entries.openviking.config.port 1934` |
| Plugin not loaded | Env file not sourced | Run `source ~/.openclaw/openviking.env` before starting |
| `externally-managed-environment` | Python PEP 668 restriction | Use venv or the one-click installer |
| `TypeError: unsupported operand type(s) for \|` | Python < 3.10 | Upgrade Python to 3.10+ |

### Viewing Logs

```bash
# OpenViking logs
cat ~/.openviking/data/log/openviking.log

# OpenClaw gateway logs
cat ~/.openclaw/logs/gateway.log
cat ~/.openclaw/logs/gateway.err.log

# Check if OpenViking process is alive
lsof -i:1933

# Quick connectivity check
curl http://localhost:1933
# Expected: {"detail":"Not Found"}
```

---

## Uninstallation

```bash
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9
python3 -m pip uninstall openviking -y && rm -rf ~/.openviking
```

---

**See also:** [INSTALL-ZH.md](./INSTALL-ZH.md) (中文详细安装指南) · [INSTALL.md](./INSTALL.md) (English Install Guide) · [INSTALL-AGENT.md](./INSTALL-AGENT.md) (Agent Install Guide)
