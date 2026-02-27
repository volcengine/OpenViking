---
name: install-openviking-memory
description: Install and configure OpenViking as the long-term memory backend for OpenClaw. Run this skill once to set up persistent memory that automatically remembers and recalls context across conversations. Trigger when the user asks to "install openviking", "set up memory", or "enable long-term memory".
compatibility: OpenClaw installed (npm install -g openclaw), Python >= 3.10, Node.js >= 22
---

# Install OpenViking Memory for OpenClaw

This skill installs [OpenViking](https://github.com/volcengine/OpenViking) as the memory backend for OpenClaw. After installation, OpenClaw will automatically **remember** facts from conversations and **recall** relevant context.

> **For AI Agents:** Follow the steps below **in order**. Run each command, check the output, and only proceed when the step passes. Ask the user for input only where indicated with **[ASK USER]**.

---

## Step 1: Check Prerequisites

Run **all** checks below. Every check must pass before continuing.

### 1.1 Check Node.js (>= 22)

```bash
node -v
```

- **Pass:** Output shows `v22.x.x` or higher.
- **Fail:** Tell the user: "Node.js >= 22 is required. Install it from https://nodejs.org/ or use nvm: `nvm install 22`"

### 1.2 Check Python (>= 3.10)

**Linux / macOS:**

```bash
python3 --version
```

**Windows:**

```powershell
python --version
```

- **Pass:** Output shows `Python 3.10.x` or higher.
- **Fail:** Tell the user: "Python >= 3.10 is required. See INSTALL.md for installation instructions, or build from source: https://www.python.org/downloads/"

### 1.3 Check Build Tools (cmake, g++)

```bash
cmake --version
g++ --version
```

- **Pass:** Both commands return version info.
- **Fail:** Tell the user which tool is missing and how to install:
  - RHEL/CentOS/openEuler: `sudo dnf install -y gcc gcc-c++ cmake make`
  - Ubuntu/Debian: `sudo apt install -y build-essential cmake`
  - **These tools are required** — OpenClaw's `node-llama-cpp` dependency compiles C++ during installation, and OpenViking also has C++ extensions.

### 1.4 Check OpenClaw

```bash
openclaw --version
```

- **Pass:** Shows version output.
- **Fail:** Tell the user: "OpenClaw is required. Install with: `npm install -g openclaw` (if downloads are slow: `npm install -g openclaw --registry=https://registry.npmmirror.com`). After installing, run `openclaw onboard` to configure your LLM."

### 1.5 Check Go (>= 1.25) — only for source install on Linux

> Skip this check if the user will install via `pip install openviking` (PyPI pre-built package).

```bash
go version
```

- **Pass:** Output shows `go1.25.x` or higher.
- **Fail:** Tell the user: "Go >= 1.25 is required for source installation on Linux (to compile AGFS). Download from https://go.dev/dl/ — see INSTALL.md for detailed steps."

---

## Step 2: Clone and Install OpenViking

### 2.1 Clone Repository

If the OpenViking repo is not already present:

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
```

### 2.2 Install Python Package

Two options — choose based on the user's needs:

#### Option A: Install from PyPI (recommended, no Go needed)

```bash
python3 -m pip install openviking
```

#### Option B: Install from Source (developer mode)

Requires Go >= 1.25 on Linux (check passed in Step 1.5).

**Linux / macOS:**

```bash
python3 -m pip install -e .
```

**Windows:**

```powershell
python -m pip install -e .
```

> If pip downloads are slow, suggest using a mirror:
> `python3 -m pip install openviking -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 2.3 Verify Installation

**Linux / macOS:**

```bash
python3 -c "import openviking; print('openviking module: ok')"
```

**Windows:**

```powershell
python -c "import openviking; print('openviking module: ok')"
```

- **Pass:** Prints `openviking module: ok`.
- **Fail — multiple Python versions:** Ask the user which Python to use, then install with that path: `/path/to/python3.11 -m pip install openviking`
- **Fail — `TypeError: unsupported operand type(s) for |`:** Python version is below 3.10. The user needs to upgrade.
- **Fail — `Go compiler not found`:** Go is not installed (source install only). See Step 1.5.

---

## Step 3: Run the Setup Helper

From the OpenViking repo root:

```bash
npx ./examples/openclaw-memory-plugin/setup-helper
```

The helper will interactively prompt for:

1. **Workspace path** — data storage location (default: absolute path of `~/.openviking/data`, auto-detected)
2. **Volcengine Ark API Key** — **[ASK USER]** Direct them to https://console.volcengine.com/ark if they don't have one
3. **VLM model** — default `doubao-seed-1-8-251228`, press Enter to accept
4. **Embedding model** — default `doubao-embedding-vision-250615`, press Enter to accept
5. **Server ports** — default 1933 (HTTP) and 1833 (AGFS), press Enter to accept

The helper will automatically:
- Create `~/.openviking/ov.conf`
- Deploy the `memory-openviking` plugin into OpenClaw
- Configure OpenClaw to use local mode
- Write `~/.openclaw/openviking.env` (Linux/macOS) or `openviking.env.bat` (Windows)

Wait for `Setup complete!` before proceeding.

---

## Step 4: Start OpenClaw with Memory

**Always load the env file first**, then start the gateway:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

Wait a few seconds. Verify this line appears in the output:

```
[gateway] memory-openviking: local server started (http://127.0.0.1:1933, ...)
```

- **Pass:** Tell the user: "OpenViking memory is now active. I will automatically remember important facts from our conversations and recall them when relevant."
- **Fail — `health check timeout`:** A stale process is blocking the port. Fix with:
  ```bash
  lsof -ti tcp:1933 tcp:1833 | xargs kill -9
  source ~/.openclaw/openviking.env && openclaw gateway
  ```

---

## Step 5: Verify (Optional)

```bash
openclaw status
```

The **Memory** line should show: `enabled (plugin memory-openviking)`

---

## Troubleshooting Quick Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `cmake not found` during npm install | Missing build tools | `sudo dnf install -y gcc gcc-c++ cmake make` |
| `Python.h: No such file or directory` | Missing Python dev headers | `sudo dnf install -y python3-devel` (or `python3.11-devel`) |
| `Go compiler not found` | Go not installed | Install Go >= 1.25 from https://go.dev/dl/ |
| `dial tcp: i/o timeout` (Go modules) | Network issue | `go env -w GOPROXY=https://goproxy.cn,direct` |
| `ERR_INVALID_URL` (npm) | Proxy missing `http://` prefix | `export https_proxy=http://host:port` |
| `extracted 0 memories` | Wrong API key or model name | Check `api_key` and `model` in `~/.openviking/ov.conf` |
| `health check timeout` | Stale process on port | Kill with `lsof -ti tcp:1933 tcp:1833 \| xargs kill -9` |
| Plugin not loaded | Env file not sourced | Run `source ~/.openclaw/openviking.env` before gateway |

---

## Daily Usage

Each time the user wants to start OpenClaw with memory:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

> Suggest adding an alias for convenience:
> ```bash
> echo 'alias openclaw-start="source ~/.openclaw/openviking.env && openclaw gateway"' >> ~/.bashrc
> ```

---

## Uninstall

**Linux / macOS:**

```bash
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9
npm uninstall -g openclaw
rm -rf ~/.openclaw
python3 -m pip uninstall openviking -y
rm -rf ~/.openviking
```

**Windows (cmd):**

```cmd
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833 :18789"') do taskkill /PID %a /F
npm uninstall -g openclaw
rmdir /s /q "%USERPROFILE%\.openclaw"
python -m pip uninstall openviking -y
rmdir /s /q "%USERPROFILE%\.openviking"
```
