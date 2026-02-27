# Install OpenViking Memory for OpenClaw

Give [OpenClaw](https://github.com/openclaw/openclaw) long-term memory powered by [OpenViking](https://github.com/volcengine/OpenViking). After setup, OpenClaw will automatically **remember** facts from conversations and **recall** relevant context before responding.

---

## 1. Quick Start (Let OpenClaw Install It)

Copy the skill file into OpenClaw's skill directory, then let OpenClaw handle the rest:

**Linux / macOS:**

```bash
mkdir -p ~/.openclaw/skills/install-openviking-memory
cp examples/openclaw-memory-plugin/skills/install-openviking-memory/SKILL.md \
   ~/.openclaw/skills/install-openviking-memory/
```

**Windows (cmd):**

```cmd
mkdir "%USERPROFILE%\.openclaw\skills\install-openviking-memory"
copy examples\openclaw-memory-plugin\skills\install-openviking-memory\SKILL.md ^
     "%USERPROFILE%\.openclaw\skills\install-openviking-memory\"
```

Then tell OpenClaw: **"Install OpenViking memory"** — it will read the skill and complete the setup.

For manual installation, continue reading.

---

## 2. Prerequisites

### Overview

| Component | Version | Purpose | Required? |
|-----------|---------|---------|-----------|
| **Python** | >= 3.10 | OpenViking runtime | Yes |
| **Node.js** | >= 22 | OpenClaw runtime + setup helper | Yes |
| **cmake** | — | Compile C++ extensions (OpenViking + OpenClaw's node-llama-cpp) | Yes |
| **g++ (gcc-c++)** | — | C++ compiler | Yes |
| **Go** | >= 1.25 | Compile AGFS server (Linux source install only) | Source install only |
| **Volcengine Ark API Key** | — | Embedding + VLM model calls | Yes |

> **PyPI vs Source install:**
> - `pip install openviking` (pre-built package): needs Python, cmake, g++ — **no Go required**
> - `pip install -e .` (source install): needs Python, cmake, g++ **and Go >= 1.25** (to compile AGFS on Linux)
> - **Windows** users can use pre-built wheel packages without Go

### Quick Check

```bash
python3 --version     # >= 3.10
node -v               # >= v22
cmake --version       # installed
g++ --version         # installed
go version            # >= go1.25 (source install only)
```

If all commands pass, skip ahead to [Section 4: Installation Steps](#4-installation-steps).

---

## 3. Environment Setup (Linux)

> Skip this section if your system already meets the prerequisites above.

### 3.1 Install Build Tools

> Already installed? Run `cmake --version && g++ --version` — if both show output, skip this step.

**RHEL / CentOS / openEuler / Fedora:**

```bash
sudo dnf install -y gcc gcc-c++ cmake make
```

**Ubuntu / Debian:**

```bash
sudo apt update
sudo apt install -y build-essential cmake
```

### 3.2 Install Python 3.10+

> Already installed? Run `python3 --version` — if it shows >= 3.10, skip this step.

Many Linux distributions (e.g. openEuler 22.03, CentOS 7/8) ship with Python 3.9 or older, and their repositories often do not include Python 3.10+ packages. Building from source is recommended.

#### Option A: Build from Source (recommended)

```bash
# 1. Install build dependencies
# RHEL / CentOS / openEuler / Fedora:
sudo dnf install -y gcc make openssl-devel bzip2-devel libffi-devel \
    zlib-devel readline-devel sqlite-devel xz-devel tk-devel

# Ubuntu / Debian:
# sudo apt install -y build-essential libssl-dev libbz2-dev libffi-dev \
#     zlib1g-dev libreadline-dev libsqlite3-dev liblzma-dev tk-dev

# 2. Download and build
cd /tmp
curl -O https://www.python.org/ftp/python/3.11.12/Python-3.11.12.tgz
tar xzf Python-3.11.12.tgz
cd Python-3.11.12
./configure --prefix=/usr/local --enable-optimizations --enable-shared \
    LDFLAGS="-Wl,-rpath /usr/local/lib"
make -j$(nproc)
sudo make altinstall

# 3. Create symlinks so python3 / pip3 point to the new version
sudo ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3
sudo ln -sf /usr/local/bin/pip3.11 /usr/local/bin/pip3

# 4. Verify
python3 --version   # Any version >= 3.10 is acceptable
```

> **Tip:** Use `altinstall` instead of `install` to avoid overwriting the system default Python. `/usr/local/bin` typically has higher priority in `PATH`, so the symlinks make `python3` point to the new version.

#### Option B: Install via Package Manager (available on some distros)

```bash
# RHEL / CentOS / openEuler / Fedora (may not be available)
sudo dnf install -y python3.11 python3.11-devel python3.11-pip

# Ubuntu 22.04+ ships with Python 3.10
sudo apt install -y python3 python3-dev python3-pip python3-venv

# Ubuntu 20.04 or older, add the deadsnakes PPA first
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install -y python3.11 python3.11-dev python3.11-venv
```

> If `dnf install python3.11` reports `No match for argument`, your repository does not have this package. Please use the source build method above.

After installation, upgrade pip:

```bash
python3 -m pip install --upgrade pip
```

> Downloads slow? See [Appendix: Network Acceleration](#8-network-acceleration-mirrors--proxies) to configure pip mirrors.

### 3.3 Install Node.js >= 22

> Already installed? Run `node -v` — if it shows >= v22, skip this step.

OpenClaw requires Node.js >= 22. The setup helper script also needs Node.js.

#### Option A: Install via NodeSource (recommended)

```bash
# RHEL / CentOS / openEuler / Fedora
curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
sudo dnf install -y nodejs

# Ubuntu / Debian
# curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
# sudo apt install -y nodejs
```

#### Option B: Install via nvm (no root required)

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
nvm use 22
```

#### Option C: Download binary package manually

```bash
wget https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz
sudo tar -C /usr/local -xJf node-v22.14.0-linux-x64.tar.xz
echo 'export PATH=$PATH:/usr/local/node-v22.14.0-linux-x64/bin' >> ~/.bashrc
source ~/.bashrc
```

> For ARM architecture, replace `linux-x64` with `linux-arm64`.

Verify:

```bash
node -v   # >= v22
npm -v
```

### 3.4 Install Go >= 1.25 (source install only)

> Already installed? Run `go version` — if it shows >= go1.25, skip this step.
> Also skippable if using `pip install openviking` (pre-built package).

Go is required on Linux to compile the AGFS server when installing from source.

```bash
# Download (for ARM use go1.25.6.linux-arm64.tar.gz)
wget https://go.dev/dl/go1.25.6.linux-amd64.tar.gz

# Extract
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.25.6.linux-amd64.tar.gz

# Configure environment variables
cat >> ~/.bashrc << 'EOF'
export GOROOT=/usr/local/go
export GOPATH=$HOME/go
export PATH=$PATH:$GOROOT/bin:$GOPATH/bin
EOF
source ~/.bashrc

# Verify
go version   # >= go1.25
```

> Go module downloads slow? See [Appendix: Network Acceleration](#8-network-acceleration-mirrors--proxies) to configure GOPROXY.

### 3.5 Verify Environment

```bash
python3 --version     # >= 3.10
node -v               # >= v22
cmake --version       # installed
g++ --version         # installed
go version            # >= go1.25 (source install only)
```

All checks pass? Proceed to installation.

---

## 4. Installation Steps

### 4.1 Install OpenClaw

> **Prerequisite:** cmake and g++ must be installed (OpenClaw depends on `node-llama-cpp`, which compiles C++ code during installation).

```bash
npm install -g openclaw
```

> Downloads slow? See [Appendix: Network Acceleration](#8-network-acceleration-mirrors--proxies) to configure npm mirrors.

Run the onboarding wizard to configure your LLM:

```bash
openclaw onboard
```

Verify:

```bash
openclaw --version
```

### 4.2 Install OpenViking

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
```

#### Option A: Install from PyPI (recommended, no Go needed)

```bash
python3 -m pip install openviking
```

#### Option B: Install from Source (developer mode, requires Go)

**Linux / macOS:**

```bash
go version && cmake --version && g++ --version   # Confirm tools are installed
python3 -m pip install -e .
```

**Windows:**

```powershell
python -m pip install -e .
```

> **Note:** Go >= 1.25 is **required** on Linux for source install (to compile AGFS). To force-skip (advanced users only):
> ```bash
> OPENVIKING_SKIP_AGFS_BUILD=1 python3 -m pip install -e .
> ```

Verify:

```bash
python3 -c "import openviking; print('ok')"
```

### 4.3 Run the Setup Helper

From the OpenViking repo root:

```bash
npx ./examples/openclaw-memory-plugin/setup-helper
```

The helper will walk you through:

1. **Environment check** — verifies cmake, g++, Python, Go, OpenClaw
2. **Install OpenViking** (if not already installed)
3. **Interactive configuration** — prompts for:
   - Data storage path (defaults to absolute path, e.g. `/home/yourname/.openviking/data`)
   - Volcengine Ark API Key
   - VLM model name (default: `doubao-seed-1-8-251228`)
   - Embedding model name (default: `doubao-embedding-vision-250615`)
   - Server ports (default: 1933 / 1833)
4. **Generate config** — creates `~/.openviking/ov.conf`
5. **Deploy plugin** — registers `memory-openviking` with OpenClaw
6. **Write env file** — generates `~/.openclaw/openviking.env`

> Non-interactive mode: `npx ./examples/openclaw-memory-plugin/setup-helper -y`

### 4.4 Start and Verify

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

You should see:

```
[gateway] listening on ws://127.0.0.1:18789
[gateway] memory-openviking: local server started (http://127.0.0.1:1933, config: ...)
```

Check plugin status:

```bash
openclaw status
# Memory line should show: enabled (plugin memory-openviking)
```

Test memory:

```bash
openclaw tui
```

Say: "Please remember: my favorite programming language is Python."

In a later conversation, ask: "What is my favorite programming language?"

OpenClaw should recall the answer from OpenViking memory.

---

## 5. Daily Usage

Each time you want to use OpenClaw with memory:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

> **Convenience (Linux/macOS):** Add to `~/.bashrc`:
> ```bash
> alias openclaw-start='source ~/.openclaw/openviking.env && openclaw gateway'
> ```

The plugin automatically starts and stops the OpenViking server.

---

## 6. Configuration Reference

### `~/.openviking/ov.conf`

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  },
  "storage": {
    "workspace": "/home/yourname/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local", "port": 1833 }
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
  },
  "vlm": {
    "backend": "volcengine",
    "api_key": "<your-api-key>",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
```

> **Note:** `workspace` must be an **absolute path** (e.g. `/home/yourname/.openviking/data`). Tilde (`~`) and relative paths are not supported. The setup helper fills this in automatically.

### `~/.openclaw/openviking.env`

Auto-generated by the setup helper:

```bash
export OPENVIKING_PYTHON='/usr/local/bin/python3'
export OPENVIKING_GO_PATH='/usr/local/go/bin'  # optional
```

Windows version (`openviking.env.bat`):

```cmd
set OPENVIKING_PYTHON=C:\path\to\python.exe
set OPENVIKING_GO_PATH=C:\path\to\go\bin
```

### Setup Helper Options

```
npx ./examples/openclaw-memory-plugin/setup-helper [options]

  -y, --yes     Non-interactive, use defaults
  -h, --help    Show help

Environment variables:
  OPENVIKING_PYTHON       Python interpreter path
  OPENVIKING_CONFIG_FILE  Custom ov.conf path
  OPENVIKING_REPO         Local repo path (auto-detected when run from repo)
  OPENVIKING_ARK_API_KEY  Skip API key prompt (for CI/scripts)
```

---

## 7. Troubleshooting

### Installation Issues

#### `cmake not found` / `g++ not found`

OpenClaw depends on `node-llama-cpp` (compiles C++), and OpenViking's C++ extensions also need cmake/g++.

```bash
# RHEL / CentOS / openEuler
sudo dnf install -y gcc gcc-c++ cmake make

# Ubuntu / Debian
sudo apt install -y build-essential cmake
```

#### `No matching distribution found for python-multipart>=0.0.22`

pip is using Python 3.9. Make sure you're using Python 3.10+:

```bash
python3 --version        # Confirm >= 3.10
python3 -m pip install -e .
```

#### `fatal error: Python.h: No such file or directory`

Missing Python development headers:

```bash
# RHEL / CentOS / openEuler
sudo dnf install -y python3-devel   # or python3.11-devel

# Ubuntu / Debian
sudo apt install -y python3-dev     # or python3.11-dev
```

> If Python was built from source, development headers are already included.

#### `Go compiler not found` / AGFS build failure

Go >= 1.25 is **required** on Linux for source install. See [3.4 Install Go](#34-install-go--125-source-install-only).

```bash
go version              # Confirm >= 1.25
python3 -m pip install -e .
```

#### Go module download timeout (`dial tcp: i/o timeout`)

Configure Go proxy. See [Appendix: Network Acceleration](#8-network-acceleration-mirrors--proxies).

#### npm `ERR_INVALID_URL`

Usually caused by malformed proxy environment variables. Proxy URLs **must** include the `http://` prefix:

```bash
# Wrong
export https_proxy=192.168.1.1:7897

# Correct
export https_proxy=http://192.168.1.1:7897
```

Or clear proxies entirely:

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
```

#### npm `ENOTEMPTY`

Previous install was interrupted. Clean up and retry:

```bash
rm -rf $(npm root -g)/openclaw $(npm root -g)/.openclaw-*
npm install -g openclaw
```

### Runtime Issues

#### Plugin not showing in gateway output

- Did you load the env file before `openclaw gateway`?
  - Linux/macOS: `source ~/.openclaw/openviking.env`
  - Windows: `call "%USERPROFILE%\.openclaw\openviking.env.bat"`
- Run `openclaw status` to check plugin state
- Re-run setup: `npx ./examples/openclaw-memory-plugin/setup-helper`

#### `health check timeout at http://127.0.0.1:1933`

A stale process is occupying the port. Kill it and restart:

```bash
# Linux / macOS
lsof -ti tcp:1933 tcp:1833 | xargs kill -9
source ~/.openclaw/openviking.env && openclaw gateway
```

```cmd
REM Windows
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833"') do taskkill /PID %a /F
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

#### `extracted 0 memories`

Model configuration in `ov.conf` is incorrect. Check:

- `embedding.dense.api_key` is a valid Volcengine Ark API key
- `vlm.api_key` is set (usually the same key)
- `vlm.model` is a model name (e.g. `doubao-seed-1-8-251228`), **not** the API key

### Python Version Issues

#### `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`

Python version is below 3.10 (`X | None` syntax requires 3.10+). Upgrade Python — see [3.2 Install Python](#32-install-python-310).

#### `pip install -e .` installs to the wrong Python

Use an explicit Python path:

```bash
python3.11 -m pip install -e .
export OPENVIKING_PYTHON=python3.11
npx ./examples/openclaw-memory-plugin/setup-helper
```

---

## 8. Network Acceleration (Mirrors & Proxies)

> Use these if package downloads are slow in your network environment.

### pip Mirror

```bash
# Permanent (recommended)
python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
python3 -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# Alternatives
# Alibaba Cloud: https://mirrors.aliyun.com/pypi/simple/
# Huawei Cloud:  https://repo.huaweicloud.com/repository/pypi/simple/
# Tencent Cloud: https://mirrors.cloud.tencent.com/pypi/simple/
```

Single-use:

```bash
python3 -m pip install openviking -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### npm Mirror

```bash
# Permanent (recommended)
npm config set registry https://registry.npmmirror.com

# Single-use
npm install -g openclaw --registry=https://registry.npmmirror.com
```

### Go Proxy

```bash
# goproxy.cn (recommended)
go env -w GOPROXY=https://goproxy.cn,direct

# Alibaba Cloud
# go env -w GOPROXY=https://mirrors.aliyun.com/goproxy/,direct

# Disable checksum verification (may be needed for some modules)
go env -w GONOSUMCHECK=*

# Verify
go env GOPROXY
```

> Takes effect globally for the current user. Subsequent `pip install -e .` builds that compile AGFS will use this automatically.

---

## 9. Uninstall

**Linux / macOS:**

```bash
# Stop services
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9

# Remove OpenClaw
npm uninstall -g openclaw
rm -rf ~/.openclaw

# Remove OpenViking
python3 -m pip uninstall openviking -y
rm -rf ~/.openviking
```

**Windows (cmd):**

```cmd
REM Stop services
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833 :18789"') do taskkill /PID %a /F

REM Remove OpenClaw
npm uninstall -g openclaw
rmdir /s /q "%USERPROFILE%\.openclaw"

REM Remove OpenViking
python -m pip uninstall openviking -y
rmdir /s /q "%USERPROFILE%\.openviking"
```

---

**See also:** [INSTALL-ZH.md](./INSTALL-ZH.md) (Chinese version)
