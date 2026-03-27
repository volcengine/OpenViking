# Installing OpenViking for OpenClaw

Provide long-term memory capabilities for [OpenClaw](https://github.com/openclaw/openclaw) via [OpenViking](https://github.com/volcengine/OpenViking). After installing, OpenClaw will automatically **remember** important information from conversations and **recall** relevant content before replying. The latest version of OpenViking includes a [WebConsole](https://github.com/volcengine/OpenViking/tree/main/openviking/console) for debugging and operations. Method 3 in this document also provides instructions on how to verify that memories are written via the WebConsole interface. We welcome you to try it out and provide feedback.

> **ℹ️ Historical Compatibility Note**
>
> Legacy OpenViking/OpenClaw integrations had a known issue around OpenClaw `2026.3.12` where conversations could hang after the plugin loaded.
> That issue affected the legacy plugin path; the current context-engine Plugin 2.0 described in this document is not affected, so new installations do not need to downgrade OpenClaw for this reason.
> Plugin 2.0 is also not backward-compatible with the legacy `memory-openviking` plugin and its configuration, so upgrades must replace the old setup instead of mixing the two versions.
> Plugin 2.0 also depends on OpenClaw's context-engine capability and does not support older OpenClaw releases; upgrade OpenClaw first before following this guide.
> If you are troubleshooting a legacy deployment, see [#591](https://github.com/volcengine/OpenViking/issues/591) and upstream fix PRs: openclaw/openclaw#34673, openclaw/openclaw#33547.

> **🚀 Plugin 2.0 (Context-Engine Architecture)**
>
> This document covers the current OpenViking Plugin 2.0 built on the context-engine architecture, which is the recommended integration path for AI coding assistants.
> For design background and earlier discussion, see:
> https://github.com/volcengine/OpenViking/discussions/525

---

## One-Click Installation

**Prerequisites:** Python >= 3.10, Node.js >= 22. The setup helper will automatically check and prompt you to install any missing components.

### Prerequisite Steps for Upgrading from Legacy `memory-openviking` to New `openviking`

- If the current environment already has the legacy `memory-openviking` plugin installed, complete the following prerequisite steps before installing the new version. Plugin 2.0 is not backward-compatible with the legacy plugin/configuration, so do not keep both versions active at the same time.

- If you have never installed the legacy plugin before, you can skip this section and go straight to installation.

- Plugin 2.0 is not backward-compatible with the legacy plugin/configuration, so do not keep both versions active at the same time.

#### Method A: Download and Run the Legacy Plugin Cleanup Script (Recommended)

```bash
curl -fsSLO https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

#### Method B: Manually Clean Up the Legacy Plugin Configuration
1. Stop the OpenClaw gateway:

```bash
openclaw gateway stop
```

2. Back up the legacy configuration and plugin directory:

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-openviking-upgrade.bak
mkdir -p ~/.openclaw/disabled-extensions
mv ~/.openclaw/extensions/memory-openviking ~/.openclaw/disabled-extensions/memory-openviking-upgrade-backup
```

3. Update the OpenClaw configuration and remove legacy settings:

Edit `~/.openclaw/openclaw.json`, remove `"memory-openviking"` from `plugins.allow`, remove `plugins.entries.memory-openviking`, change `plugins.slots.memory` to `"none"`, and remove the legacy `memory-openviking` plugin path from `plugins.load.paths`.

After cleaning up the legacy plugin configuration using either approach above, install the new plugin by following Method A or Method B below.

Preserve and migrate legacy runtime settings into the new configuration if needed (the new version works with defaults; legacy parameters are optional to migrate):

If the legacy plugin was using `plugins.entries.memory-openviking.config`, migrate `mode`, `configPath`, `port`, `baseUrl`, `apiKey`, `agentId`, and any other needed parameters from the backup `openclaw.json` file created in Step 2 into `plugins.entries.openviking.config`.

Run the prerequisite steps above only if they apply to your environment. Once finished, continue with the Plugin 2.0 installation flow. For now, we do not recommend direct natural-language installation; the npm one-click installer is the preferred path.

### Method A: npm Installation (Recommended, Including Windows)

**Windows:** use the same `npm`, `npx`, and `ov-install` commands in **PowerShell** or **cmd** (Node.js ≥ 22 required). For a non-default OpenClaw state directory, pass a real path to `--workdir` (e.g. `%USERPROFILE%\.openclaw-second`).

```bash
npm install -g openclaw-openviking-setup-helper
ov-install

```

If the installation fails because the system is missing tools to create a virtual environment, operate these commands below and re-run `ov-install`:

```bash
apt update
apt install -y software-properties-common
add-apt-repository universe
apt update
apt install -y python3-venv
```

Non-interactive mode (uses default configuration):

```bash
ov-install -y

```

Install to a specific OpenClaw instance:

```bash
ov-install --workdir ~/.openclaw-second

```

#### Pinning versions (npm / `ov-install`)

The published helper package is [`openclaw-openviking-setup-helper`](https://www.npmjs.com/package/openclaw-openviking-setup-helper). Install globally (latest by default), or append `@VERSION` with a published dist-tag or version from npm when you need to pin the installer:

```bash
npm install -g openclaw-openviking-setup-helper
ov-install -y
```

Run **without** global install (same optional `@VERSION` pin):

```bash
npx -y -p openclaw-openviking-setup-helper ov-install -y
```

Use **`ov-install`** to install a **specific archived plugin version** or pin the **OpenViking PyPI version**:

| Flag | Meaning |
| --- | --- |
| `--github-repo owner/repo` | GitHub repo for plugin raw downloads (default: `volcengine/OpenViking`) |
| `--plugin-version REF` | Git branch, tag, or commit for plugin files (default: `main`) |
| `--openviking-version VER` | Pin `pip install openviking==VER` (omit for latest PyPI release) |

Examples:

```bash
# Install a specific tagged plugin version (e.g. v0.2.9)
ov-install -y --plugin-version v0.2.9

# Pin OpenViking on PyPI, plugin from main
ov-install -y --openviking-version 0.2.9

# Pin both plugin tag and OpenViking PyPI version
ov-install -y --plugin-version v0.2.9 --openviking-version 0.2.9

# Legacy plugin line (use a release in the v0.2.3-v0.2.6 range if you need the old plugin line)
ov-install -y --plugin-version <legacy-version>
```

Environment variables (same semantics): `REPO` (same as `--github-repo`), `PLUGIN_VERSION` / `BRANCH`, `OPENVIKING_VERSION`.

#### Upgrade and rollback (`ov-install`)

Use `--update` / `--upgrade-plugin` to upgrade **only the plugin** to a specific plugin ref. This mode:

- keeps the existing OpenViking service version
- keeps the existing `~/.openviking/ov.conf`
- preserves the current plugin runtime settings
- for local mode, keeps `configPath` and `port`
- for remote mode, keeps `baseUrl`, `apiKey`, and `agentId`
- cleans only the OpenViking plugin entries from `openclaw.json`, without touching unrelated plugins
- backs up `openclaw.json` and the previous plugin directory before replacing the plugin
- records the upgrade path and rollback audit file in the command output

Do **not** combine `--update` with `--openviking-version`; changing the OpenViking service version still requires a full install flow.

```bash
# Upgrade only the plugin to a tagged release
ov-install --update --plugin-version v0.2.9

# Upgrade only the plugin to a branch
ov-install --update --plugin-version dev-branch

# Upgrade only the plugin from a specific GitHub repo + branch
ov-install --update --github-repo yourname/OpenViking --plugin-version dev-branch

# Roll back the last plugin upgrade
ov-install --rollback
```

Upgrade backups are stored under:

- `~/.openclaw/.openviking-upgrade-backup/openclaw.json.bak`
- `~/.openclaw/.openviking-upgrade-backup/last-upgrade.json`
- `~/.openclaw/disabled-extensions/<pluginId>-upgrade-backup-*`

`--rollback` restores the latest saved `openclaw.json` snapshot and the previous plugin directory created by the last `--update`.

**Windows:** the same `ov-install` upgrade and rollback commands work in PowerShell or cmd.

### Method B: curl One-Click Installation (Linux / macOS)

On **Windows**, prefer **Method A** (`npm` / `ov-install`). `curl | bash` needs a Bash environment (**Git Bash** or **WSL**) if you use this section.

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash

```

Non-interactive mode:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- -y

```

Install to a specific OpenClaw instance:

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- --workdir ~/.openclaw-second -y

```

#### Pinning versions (`install.sh`)

| Flag / env | Meaning |
| --- | --- |
| `--repo owner/repo` / `REPO` | GitHub repo for plugin raw files (default: `volcengine/OpenViking`) |
| `--plugin-version REF` / `PLUGIN_VERSION` | Git branch, tag, or commit (default: `main`; legacy: `BRANCH`) |
| `--openviking-version VER` / `OPENVIKING_VERSION` | `pip install openviking==VER` (omit for latest) |

Examples:

```bash
# Install a specific tagged plugin version (e.g. v0.2.9)
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --plugin-version v0.2.9 -y

# Pin OpenViking on PyPI
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --openviking-version 0.2.9 -y

# Pin both plugin tag and OpenViking PyPI version
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --plugin-version v0.2.9 --openviking-version 0.2.9 -y
```

#### Upgrade and rollback (`install.sh`)

The `curl | bash` entry point now runs the same upgrade and rollback logic natively in `install.sh`. The target machine does not need a preinstalled global installer helper for plugin-only upgrade or rollback.

```bash
# Upgrade only the plugin to a tagged release
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --update --plugin-version v0.2.9

# Upgrade only the plugin to a branch
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --update --plugin-version dev-branch

# Upgrade only the plugin from a specific GitHub repo + branch
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --update --repo yourname/OpenViking --plugin-version dev-branch

# Roll back the last plugin upgrade
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash -s -- \
  --rollback
```

`--update` only changes the plugin release. Do not pass `--openviking-version` in upgrade mode.

Native shell upgrade behavior:

- keeps the current local or remote plugin settings
- does not rewrite `~/.openviking/ov.conf`
- backs up `openclaw.json` to `~/.openclaw/.openviking-upgrade-backup/openclaw.json.bak`
- writes rollback audit data to `~/.openclaw/.openviking-upgrade-backup/last-upgrade.json`
- keeps only the latest plugin directory backup per plugin under `~/.openclaw/disabled-extensions/`
- prints `Upgrade path: <from> -> <to>` after a successful upgrade

The script will automatically detect multiple OpenClaw instances and let you choose. During a fresh install it also prompts you to select local or remote mode. During plugin-only upgrade it reuses the existing mode and config instead of prompting again.

### Start OpenClaw + OpenViking

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

On Windows PowerShell:

```powershell
. "$HOME/.openclaw/openviking.env.ps1"
openclaw gateway restart
```

Seeing `openviking: registered context-engine` indicates the plugin was loaded.

Then verify:

```bash
openclaw config get plugins.slots.contextEngine
```

If it shows `openviking`, the startup is successful.

### Verify Read and Write

Use OpenClaw logs to verify memory capture and recall:

```bash
openclaw logs --follow
```

Look for:

```
openviking: auto-captured 2 new messages, extracted 1 memories
```

You can also check a specific log file:

```bash
cat <your-log-file> | grep auto-capture
cat <your-log-file> | grep inject
```

Example:

```bash
cat /tmp/openclaw/openclaw-2026-03-20.log | grep auto-capture
cat /tmp/openclaw/openclaw-2026-03-20.log | grep inject
```

### View Memories with `ov tui`

In your OpenViking directory, activate the virtual environment and open the TUI:

```bash
source venv/bin/activate
ov --help
ov tui
```

Press `.` to expand folders, use arrow keys to navigate, and press `q` to quit.

---

## Prerequisites

| Component | Version Requirement | Purpose |
| --- | --- | --- |
| **Python** | >= 3.10 | OpenViking Runtime |
| **Node.js** | >= 22 | OpenClaw Runtime |
| **Volcengine Ark API Key** | — | Embedding + VLM model calls |

Quick check:

```bash
python3 --version   # >= 3.10
node -v              # >= v22
openclaw --version   # Installed

```

* Python: [https://www.python.org/downloads/](https://www.python.org/downloads/)
* Node.js: [https://nodejs.org/](https://nodejs.org/)
* OpenClaw: `npm install -g openclaw && openclaw onboard`

---

## Method 1: Local Deployment (Recommended)

Start the OpenViking service locally, suitable for personal use.

### Step 1: Install OpenViking

```bash
python3 -m pip install openviking --upgrade

```

Verification: `python3 -c "import openviking; print('ok')"`

> Encountered `externally-managed-environment`? Use the one-click installation script (which handles venv automatically) or create it manually:
> `python3 -m venv ~/.openviking/venv && ~/.openviking/venv/bin/pip install openviking`

### Step 2: Run the Setup Helper

```bash
# Method A: npm install (recommended, cross-platform)
npm install -g openclaw-openviking-setup-helper
ov-install

# Method B: curl one-click (Linux / macOS)
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash
```

The setup helper will prompt you to enter your Ark API Key and automatically generate a configuration file.

### Step 3: Start

```bash
source ~/.openclaw/openviking.env && openclaw gateway

```

Seeing `openviking: local server started` indicates success.

### Step 4: Verify

```bash
openclaw status
# The ContextEngine row should display: enabled (plugin openviking)

```

---

## Method 2: Connecting to Remote OpenViking

Already have a running OpenViking service? Simply configure the OpenClaw plugin to point to the remote address; **no Python / OpenViking installation is required**.

**Prerequisites:** An existing OpenViking service address + API Key (if authentication is enabled on the server side).

### Step 1: Install Plugin

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
# Select remote mode, enter your OpenViking server URL and API Key
```

### Step 2: Start and Verify

```bash
openclaw gateway restart
openclaw status
```

<details>
<summary>Manual configuration (without setup helper)</summary>

```bash
openclaw plugins enable openviking
openclaw config set gateway.mode local
openclaw config set plugins.slots.contextEngine openviking
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.openviking.config.apiKey "your-api-key"
openclaw config set plugins.entries.openviking.config.agentId "your-agent-id"
openclaw config set plugins.entries.openviking.config.autoRecall true --json
openclaw config set plugins.entries.openviking.config.autoCapture true --json
```

</details>

## Method 3: Integrating Openclaw with OpenViking on Volcengine ECS

This section primarily introduces how to connect Openclaw to OpenViking on Volcengine ECS and use the WebConsole to verify the data write. For details, please refer to the [documentation](https://www.volcengine.com/docs/6396/2249500?lang=zh).

Please note that to protect the system Python from being corrupted, the ECS instance has restrictions on deployments in the root directory and does not allow installing global packages directly using `pip`. It is recommended to create a virtual environment first and complete the following steps within it.

**Prerequisites:** An existing ECS OpenClaw instance.

### Step 1: npm Installation

```bash
npm install -g openclaw-openviking-setup-helper
ov-install

```

This installation mode already includes built-in VLM and embedding models in OpenViking. If no modifications are needed, simply press Enter and follow the prompts to enter your API key. After the installation is complete, a configuration file will be automatically generated. To modify it, enter `vim ~/.openviking/ov.conf`, press `i` to enter edit mode, press the `Esc` key to exit edit mode, then type `:wq` and press Enter to save and exit the file.

Load the OpenClaw environment variables in the terminal:

```bash
source /root/.openclaw/openviking.env

```

### Step 2: Start OpenViking

First, start the OpenViking Server:

```bash
python -m openviking.server.bootstrap

```

Next, start the web console. Before starting, you need to confirm whether the instance's security group has opened TCP port 8020 in the inbound rules. If not, please configure the instance security group first:

```bash
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933

```

In the instance, find your server's public IP, and use it to access: `http://<your-server-public-ip>:8020`

You can now start experiencing the web console 🎉

You can directly query file information on the web interface to verify whether the openclaw-plugin memory write is effective; you can also verify if openclaw-plugin is reading memories in the OpenClaw logs. The verification method is as follows:

```bash
grep -i inject /tmp/openclaw/openclaw-2026-03-13.log | awk -F'"' '{for(i=1;i<=NF;i++) if($i ~ /^[0-9]{2}:[0-9]{2}:[0-9]{2}/) {time=$i; break}} /injecting [0-9]+ memories/ {print time, "openviking:", gensub(/.*(injecting [0-9]+ memories).*/, "\\1", "1")}'

```

Alternatively, you can directly run `grep "inject" /tmp/openclaw/openclaw-2026-03-13.log` to view all the information.

---

## Configuration Reference

### `~/.openviking/ov.conf` (Local Mode)

```json
{
  "root_api_key": null,
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "/home/yourname/.openviking/data",
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

> `root_api_key`: Once set, all HTTP requests must carry the `X-API-Key` header. Defaults to `null` in local mode (authentication disabled).

### `agentId` Configuration (Plugin Configuration)

The Agent identifier passed to the server via the `X-OpenViking-Agent` header, used to distinguish different OpenClaw instances.

Customization method:

```bash
# Specify in the plugin configuration
openclaw config set plugins.entries.openviking.config.agentId "my-agent"

```

If not configured, the plugin auto-generates a unique ID in the format `openclaw-<hostname>-<random>`.

### `~/.openclaw/openviking.env`

Automatically generated by the installer, recording environment variables such as the Python path:

```bash
export OPENVIKING_PYTHON='/usr/local/bin/python3'

```

On Windows PowerShell, the installer writes `~/.openclaw/openviking.env.ps1` instead.

---

## Daily Usage

```bash
# Start
source ~/.openclaw/openviking.env && openclaw gateway

# Disable the context engine
openclaw config set plugins.slots.contextEngine legacy

# Enable OpenViking as the context engine
openclaw config set plugins.slots.contextEngine openviking

```

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `port occupied` | Port occupied by another process | Change port: `openclaw config set plugins.entries.openviking.config.port 1934` |
| `extracted 0 memories` | API Key or model name configured incorrectly | Check the `api_key` and `model` fields in `ov.conf` |
| Plugin not loaded | Environment variables not loaded | Execute `source ~/.openclaw/openviking.env` before starting |
| `externally-managed-environment` | Python PEP 668 restriction | Use venv or the one-click installation script |
| `TypeError: unsupported operand type(s) for｜` | Python < 3.10 | Upgrade Python to 3.10+ |

---

## Uninstallation

```bash
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9
python3 -m pip uninstall openviking -y && rm -rf ~/.openviking

```

---

**See also:** [INSTALL-ZH.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-ZH.md) (Chinese) · [INSTALL-AGENT.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) (Agent Install Guide)

---
