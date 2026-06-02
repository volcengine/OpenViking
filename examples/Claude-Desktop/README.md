# OpenViking for Claude Desktop & Claude Code

A custom integration connecting [OpenViking](https://github.com/volcengine/OpenViking)
as a persistent memory layer for **Claude Desktop** (via MCP bridge) and
**Claude Code** (via hook-based auto-recall and auto-capture).

Works with **any data** — documents, notes, code snippets, research, transcripts,
or any content you store in OpenViking. No domain-specific assumptions.

---

## What It Does

**Claude Desktop** gets 12 OpenViking MCP tools (`ov_search`, `ov_find`, `ov_read`, etc.)
so it can search and write to your memory store directly from any conversation.

**Claude Code** automatically:
- Searches your memory before every prompt (`UserPromptSubmit` hook)
- Injects the top 6 most relevant results as context
- Captures new knowledge at the end of every session (`Stop` hook)

**Background services** keep everything running:
- Watchdog monitors port 1933, restarts on crash, never permanently exits
- Health alert checks every 15 min, shows Windows notification + auto-restarts
- Autosave commits sessions every 30 min so no memory is ever lost
- Embedding selector picks Ollama (local/free) first, falls back to Jina cloud

---

## Architecture

```
Claude Desktop
    MCP bridge (openviking-bridge.py) — 12 ov_* tools over stdio
        REST API -> localhost:1933

Claude Code
    UserPromptSubmit -> hooks/ov-recall.js
        Searches all your data semantically
        Injects top 6 results as system context before Claude responds
    Stop -> hooks/ov-capture.js
        Commits session -> OpenViking extracts and stores memories

Background
    openviking-watchdog.py       15s checks, auto-restart, never exits
    scripts/ov-health-alert.ps1  15min task, Windows notification + restart
    hooks/ov_session.py autosave 30min task, commit + new session
```

---

## Prerequisites

- Windows 10/11
- Python 3.10+ (tested on 3.13 embeddable)
- Node.js 16+
- [OpenViking](https://github.com/volcengine/OpenViking) installed:
  ```
  pip install openviking openviking-cli
  ```
- Claude Desktop (latest)
- Claude Code CLI

**Embedding (choose one):**
- [Ollama](https://ollama.ai) with `nomic-embed-text` — free, local, no API key
- [Jina AI](https://jina.ai) API key — cloud, free tier available

**VLM for memory extraction:**
- Anthropic API key (Claude Sonnet used to extract structured memories from sessions)

---

## Installation

### 1. Clone

```
git clone https://github.com/YOUR_USERNAME/openviking-claude-desktop.git
cd openviking-claude-desktop
```

### 2. Create directories

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.openviking"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude-memory\logs"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude-memory\hooks"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\hooks"
```

### 3. Configure

```powershell
copy config\ov.conf.example   "$env:USERPROFILE\.openviking\ov.conf"
copy config\ovcli.conf.example "$env:USERPROFILE\.openviking\ovcli.conf"
```

Edit `ov.conf` — replace all `YOUR_*` placeholders:
- `YOUR_JINA_API_KEY` — from jina.ai (or switch provider to ollama)
- `YOUR_ANTHROPIC_API_KEY` — from console.anthropic.com
- `YOUR_LOCAL_API_KEY` — any string you choose (e.g. `my-local-key`)
- `YOUR_USERNAME` — your Windows username

### 4. Copy server files

```powershell
copy server\openviking-bridge.py   "$env:USERPROFILE\.openviking\"
copy server\openviking-mcp.py      "$env:USERPROFILE\.openviking\"
copy server\openviking-watchdog.py "$env:USERPROFILE\.openviking\"
```

### 5. Copy scripts

```powershell
copy scripts\*.ps1 "$env:USERPROFILE\.openviking\"
```

### 6. Copy hooks

```powershell
copy hooks\ov-recall.js  "$env:USERPROFILE\.claude\hooks\"
copy hooks\ov-capture.js "$env:USERPROFILE\.claude\hooks\"
copy hooks\ov_session.py "$env:USERPROFILE\.claude-memory\hooks\"
```

### 7. Configure Claude Desktop MCP

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "openviking-memory": {
      "command": "python",
      "args": ["C:\\Users\\YOUR_USERNAME\\.openviking\\openviking-bridge.py"],
      "env": {
        "OV_API_KEY": "YOUR_LOCAL_API_KEY"
      }
    }
  }
}
```

### 8. Wire Claude Code hooks

Add to `%USERPROFILE%\.claude\settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "node \"C:/Users/YOUR_USERNAME/.claude/hooks/ov-recall.js\""
        }]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "node \"C:/Users/YOUR_USERNAME/.claude/hooks/ov-capture.js\""
        }]
      }
    ]
  }
}
```

### 9. Register scheduled tasks (run PowerShell as Administrator)

```powershell
& "$env:USERPROFILE\.openviking\register-ov-watchdog.ps1"
& "$env:USERPROFILE\.openviking\register-ov-autosave.ps1"
& "$env:USERPROFILE\.openviking\register-ov-health-alert.ps1"
```

### 10. Start and verify

```powershell
& "$env:USERPROFILE\.openviking\restart-openviking.ps1"
& "$env:USERPROFILE\.openviking\verify-ov-hooks.ps1"
```

All checks should pass.

---

## Adding Your Data

OpenViking stores data as `.md` files in `%USERPROFILE%\.claude-memory\viking\default\resources\`.

You can add data via:

**MCP tool in Claude Desktop:**
```
ov_add_resource with your file path
```

**API directly:**
```powershell
$h = @{
  "Authorization" = "Bearer YOUR_LOCAL_API_KEY"
  "x-api-key" = "YOUR_LOCAL_API_KEY"
  "x-openviking-user" = "default"
  "x-openviking-account" = "default"
}
Invoke-RestMethod -Uri "http://localhost:1933/api/v1/resources" -Method POST `
  -Headers $h -ContentType "application/json" `
  -Body '{"path": "C:/your/file.md", "wait": true}' -UseBasicParsing
```

**Direct file placement:**
Place `.md` files in `%USERPROFILE%\.claude-memory\viking\default\resources\YOUR_DIR\`
then trigger re-indexing via `ov_add_resource`.

---

## File Reference

### Server (`server/`)

| File | Purpose |
|------|---------|
| `openviking-bridge.py` | MCP stdio bridge — 12 `ov_*` tools for Claude Desktop |
| `openviking-mcp.py` | Alternative MCP — 5 simpler `memory_*` tools |
| `openviking-watchdog.py` | Monitors port 1933, auto-restart, never permanently exits |

### Scripts (`scripts/`)

| File | Purpose |
|------|---------|
| `restart-openviking.ps1` | Kill, reselect embedding, start server |
| `select-embedding.ps1` | Ollama-first selector, Jina cloud fallback |
| `register-ov-watchdog.ps1` | Watchdog as scheduled task (at logon) |
| `register-ov-autosave.ps1` | 30-min autosave task (Admin required) |
| `register-ov-health-alert.ps1` | 15-min health check + auto-restart (Admin required) |
| `ov-health-alert.ps1` | Health check — Windows balloon notification if down |
| `verify-ov-hooks.ps1` | Full system verification (26 checks) |

### Hooks (`hooks/`)

| File | Install location | Purpose |
|------|-----------------|---------|
| `ov-recall.js` | `~/.claude/hooks/` | Auto-recall: searches all data, injects top 6 results |
| `ov-capture.js` | `~/.claude/hooks/` | Auto-capture: commits session at Stop |
| `ov_session.py` | `~/.claude-memory/hooks/` | Autosave: commit + new session every 30 min |

---

## Configuration Notes

### ov.conf — valid fields only

OpenViking v0.3.16 rejects any unrecognised config fields.
Only these top-level keys are valid:

```
storage | log | embedding | vlm | server
```

Adding any other key (e.g. `claude_code`, `hooks`, `app`) crashes the server on startup.

### API message format

```json
{ "role": "user", "content": "your text" }
```

Not `{ "role": "user", "parts": [...] }` — that returns an error.

### All 4 headers required

Every request to tenant-scoped APIs needs:

```
Authorization: Bearer YOUR_KEY
x-api-key: YOUR_KEY
x-openviking-user: default
x-openviking-account: default
```

### Search response path

Items are at `result.resources[]` and `result.memories[]`, not `result.items[]`.

---

## PowerShell 5.1 Compatibility

All scripts target **Windows PowerShell 5.1**. These constructs are not supported:
- `?.` null-conditional operator
- `??` null-coalescing operator
- `&` inside double-quoted `Write-Host` strings
- `-RunOnlyIfNetworkAvailable $false` in `New-ScheduledTaskSettingsSet`

---

## License

MIT

---

## Related

- [OpenViking (official)](https://github.com/volcengine/OpenViking)
- [Castor6/openviking-plugins](https://github.com/Castor6/openviking-plugins)
- [Claude Desktop](https://claude.ai/download)
- [Claude Code](https://docs.anthropic.com/claude-code)

---

## Author

**Sameh Khalifa**
Email: chinasameh@gmail.com
GitHub Issues: preferred for bug reports and feature requests
