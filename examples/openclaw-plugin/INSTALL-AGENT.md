# OpenViking Memory Plugin — Agent Install Guide

> For AI Agents (Claude Code, Cursor, etc.) to follow step-by-step.
> Principle: auto-detect what you can; only ask the user when detection fails.

---

## Step 0: Choose Target OpenClaw Instance

Check if the user has multiple OpenClaw instances:

```bash
ls -d ~/.openclaw* 2>/dev/null
```

If multiple directories exist (e.g. `~/.openclaw`, `~/.openclaw-openclaw-second`), ask the user which instance to install to. Pass the chosen directory via `--workdir`:

```bash
npx ./examples/openclaw-plugin/setup-helper --workdir ~/.openclaw-openclaw-second
```

If only `~/.openclaw` exists, proceed with the default.

## Step 1: Choose Deployment Mode

Ask the user: "How do you want to use OpenViking?"

- **A) Local** — Run OpenViking on this machine (requires Python >= 3.10)
- **B) Remote** — Connect to an existing OpenViking server (only needs the server URL and API Key)

→ A: Go to [Local Deployment Path](#local-deployment-path)
→ B: Go to [Remote Connection Path](#remote-connection-path)

---

## Local Deployment Path

### Step L1: Check Environment

Run each check. Every check must pass before continuing.

**1. Python**

```bash
python3 --version
```

- Pass: >= 3.10
- Fail: Tell user "Python >= 3.10 is required. Install from https://www.python.org/downloads/"
- Multiple versions: Ask user which Python path to use

**2. Node.js**

```bash
node -v
```

- Pass: >= v22
- Fail: Tell user "Node.js >= 22 is required. Install from https://nodejs.org/"

**3. OpenClaw**

```bash
openclaw --version
```

- Pass: Version output present
- Fail: Tell user to run `npm install -g openclaw && openclaw onboard`

### Step L2: Install OpenViking

```bash
python3 -m pip install openviking --upgrade
```

- Pass: Continue
- Fail with `externally-managed`:
  ```bash
  python3 -m venv ~/.openviking/venv
  ~/.openviking/venv/bin/pip install openviking --upgrade
  ```
  Set `OPENVIKING_PYTHON=~/.openviking/venv/bin/python` for later steps
- Fail with `No matching distribution`: Python is below 3.10, tell user to upgrade
- Other failure: Print full error, ask user

Verify:

```bash
python3 -c "import openviking; print('ok')"
```

### Step L3: Configure

Check if `~/.openviking/ov.conf` already exists:

- **Exists**: Ask user "Found existing config at ~/.openviking/ov.conf. Keep it?"
  - Yes: Skip to Step L4
  - No: Continue with configuration below

- **Does not exist**: Collect configuration

**Ask user for API Key:**

> "Please provide your Volcengine Ark API Key (used for Embedding and VLM model calls).
> Get one at https://console.volcengine.com/ark if you don't have one."

Run the setup helper:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

At the interactive prompts:
- Workspace: Press Enter for default path
- API Key: Enter the user's key
- VLM model: Press Enter for default `doubao-seed-2-0-pro-260215`
- Embedding model: Press Enter for default `doubao-embedding-vision-251215`
- Ports: Press Enter for default 1933/1833

Wait for `Setup complete!`

### Step L4: Start and Verify

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

- Pass: Output contains `openviking: local server started`
- Fail with `port occupied`:
  The port is used by another process. Change port:
  ```bash
  openclaw config set plugins.entries.openviking.config.port 1934
  source ~/.openclaw/openviking.env && openclaw gateway
  ```
- Fail with `subprocess exited`: Check stderr for Python errors — usually wrong API Key or openviking not installed properly

Verify:

```bash
openclaw status
```

ContextEngine line should show `enabled (plugin openviking)`.

Tell user: "OpenViking memory is active. I'll automatically remember important facts from our conversations and recall them when relevant."

---

## Remote Connection Path

### Step R1: Collect Connection Info

Ask user for:

1. **OpenViking server URL** (e.g. `http://10.0.0.1:1933`)
   > This is the OpenViking HTTP API address.

2. **OpenViking API Key** (optional)
   > Required if the server has `root_api_key` configured. This authenticates to the OpenViking server — it is NOT a Volcengine Ark API Key.

### Step R2: Check Environment

**1. Node.js**

```bash
node -v
```

- Pass: >= v22
- Fail: Tell user to install Node.js >= 22

**2. OpenClaw**

```bash
openclaw --version
```

- Pass: Version output present
- Fail: `npm install -g openclaw && openclaw onboard`

> Remote mode does **not** require Python — OpenViking runs on the remote server.

### Step R3: Install Plugin and Configure

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
# Select remote mode, enter OpenViking server URL and API Key
```

Alternatively, configure manually (substitute user-provided values). If targeting a non-default instance, prefix each command with `OPENCLAW_STATE_DIR=<workdir>`:

```bash
openclaw config set plugins.enabled true --json
openclaw config set plugins.slots.contextEngine openviking
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl "<user's server URL>"
openclaw config set plugins.entries.openviking.config.apiKey "<user's API Key>"
openclaw config set plugins.entries.openviking.config.autoRecall true --json
openclaw config set plugins.entries.openviking.config.autoCapture true --json
```

If user has no API Key (server auth not enabled), skip the apiKey line.

### Step R4: Start and Verify

```bash
openclaw gateway
```

- Pass: Output contains `openviking: initialized`
- Fail with connection error: Verify server is reachable — `curl <baseUrl>/health` should return `{"status":"ok"}`

```bash
openclaw status
```

ContextEngine line should show `enabled (plugin openviking)`.

Tell user: "OpenViking memory is connected to the remote server. I'll automatically remember important facts and recall them when relevant."

---

## Field Reference

| Field | Meaning | Required For |
|-------|---------|-------------|
| Volcengine Ark API Key | Embedding + VLM model access | Local |
| OpenViking API Key | Server authentication key | Remote (if server has auth enabled) |
| agentId | Identifies this agent to OpenViking | Both (auto-generated if not set) |
| baseUrl | OpenViking HTTP address | Remote |
| workspace | Data storage directory | Local |
| server port | OpenViking HTTP port (default 1933) | Local |
| VLM model | Memory extraction model | Local |
| Embedding model | Text vectorization model | Local |

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `port occupied` | Port used by another process | Change port in config, e.g. `openclaw config set plugins.entries.openviking.config.port 1934` |
| `extracted 0 memories` | Wrong API Key or model name | Check `api_key` and `model` in `~/.openviking/ov.conf` |
| `externally-managed-environment` | Python PEP 668 restriction | Install via venv |
| `ECONNREFUSED` | Remote server unreachable | Verify baseUrl and network connectivity |
| Plugin not loaded | Env file not sourced | `source ~/.openclaw/openviking.env` (local mode) |
