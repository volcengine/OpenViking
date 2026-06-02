# Troubleshooting Guide

## Server won't start

**Symptom:** `restart-openviking.ps1` shows "FAILED: Unable to connect"

Check the error log:
```powershell
Get-Content "$env:USERPROFILE\.claude-memory\logs\openviking-server-$(Get-Date -Format 'yyyyMMdd')-err.log" -Tail 20
```

Common causes:

| Error | Fix |
|-------|-----|
| `Unknown config field '...'` | Remove invalid field from ov.conf. Only `storage/log/embedding/vlm/server` are valid |
| `ModuleNotFoundError: openviking` | Run `pip install openviking openviking-cli` |
| `Address already in use: 1933` | Kill old process: `Get-Process python | Stop-Process` |
| `Invalid api_key` | Check `server.root_api_key` in ov.conf matches `OV_API_KEY` in scripts |
| `Unknown config field 'claude_code'` | Remove the `claude_code` key from ov.conf entirely |

---

## Search returns 0 results

**Symptom:** `verify-ov-hooks.ps1` shows search returning 0 resources

Causes:
1. **No data added yet** — add files via `ov_add_resource` or place `.md` files in the workspace directory
2. **Wrong response path** — items are at `result.resources[]` not `result.items[]`
3. **Missing headers** — all 4 headers required on every request:
   `Authorization`, `x-api-key`, `x-openviking-user`, `x-openviking-account`
4. **Embedding not working** — check Jina API key or Ollama model availability

---

## API returns 400 INVALID_ARGUMENT

**Symptom:**
```
ROOT requests to tenant-scoped APIs must include X-OpenViking-Account and X-OpenViking-User headers
```

Fix: Send all 4 required headers with every request:
```
Authorization: Bearer YOUR_KEY
x-api-key: YOUR_KEY
x-openviking-user: default
x-openviking-account: default
```

---

## Claude Desktop MCP tools not appearing

**Symptom:** `ov_search` and other tools not visible in Claude Desktop

Steps:
1. Verify config syntax: `Get-Content "$env:APPDATA\Claude\claude_desktop_config.json"`
2. Confirm Python path is correct and `openviking-bridge.py` exists
3. Fully quit Claude Desktop (system tray) and reopen
4. Test bridge manually — run `python openviking-bridge.py` (should hang waiting for stdin)

---

## Watchdog keeps restarting

**Symptom:** Watchdog log shows repeated restarts, server not staying up

Check:
1. `ov.conf` has no invalid fields — only `storage`, `log`, `embedding`, `vlm`, `server`
2. Embedding API key is valid (Jina free tier has rate limits)
3. Anthropic API key is valid and has available credits
4. Port 1933 is not blocked by firewall or antivirus

---

## Session state shows `current_session: None`

**Symptom:** `ov_session.py` logs "no active session"

Normal after a commit. Create a new session:
```powershell
$PY = "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe"
& $PY "$env:USERPROFILE\.claude-memory\hooks\ov_session.py" autosave
```

---

## ov-recall.js not firing in Claude Code

**Symptom:** No `[OpenViking Memory - Auto-Recall]` blocks appearing in Claude Code

Checklist:
1. `settings.json` has `ov-recall.js` wired to `hooks.UserPromptSubmit`
2. Node.js installed: `node --version`
3. Hook file exists: `Test-Path "$env:USERPROFILE\.claude\hooks\ov-recall.js"`
4. Server is up: `Invoke-WebRequest http://localhost:1933/health -UseBasicParsing`
5. Check recall log: `Get-Content "$env:USERPROFILE\.claude-memory\logs\ov-recall.log" -Tail 20`

---

## ov-capture.js fails silently at session end

**Symptom:** Sessions not being committed, `ov-capture.log` shows no entries

Causes:
1. Node.js version too old for ES2020 syntax — this repo uses ES5 throughout, should be fine on any Node
2. `STATE_F` path wrong — confirm `.session_state.json` exists at `%USERPROFILE%\.claude-memory\`
3. Session already committed by autosave before Stop hook fired — check `last_commit` in state file

---

## Embedding selector timeout

**Symptom:** Watchdog log shows "Embedding selector timed out"

The selector runs PowerShell which takes 1-3 seconds to start.
The watchdog allows 45 seconds — if timing out, your machine may be under heavy load.

Fix: Edit `EMBED_TIMEOUT` in `openviking-watchdog.py` and increase to 60 or 90.

---

## Health alert not showing notifications

**Symptom:** Server goes down, no Windows balloon notification appears

The notification uses `System.Windows.Forms.NotifyIcon`.
Check:
1. Script is running as interactive user (not SYSTEM account)
2. Windows focus assist / do not disturb is off
3. Test manually: `& "$env:USERPROFILE\.openviking\ov-health-alert.ps1"`

---

## PowerShell 5.1 compatibility errors

This project targets **Windows PowerShell 5.1** (not PowerShell 7+).

Never use in `.ps1` files on PS5.1:

| Construct | Error | Fix |
|-----------|-------|-----|
| `$obj?.prop` | Unexpected token `?.` | Use `if ($obj) { $obj.prop }` |
| `$a ?? $b` | Unexpected token `??` | Use `if ($null -ne $a) { $a } else { $b }` |
| `"run & script"` | `&` not allowed in string | Use plain text or escape |
| `-RunOnlyIfNetworkAvailable $false` | Positional parameter error | Remove this parameter entirely |
