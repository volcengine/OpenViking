Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# verify-ov-hooks.ps1
# Full system verification for openviking-claude-desktop.
# Auto-detects Python, Node.js, and all paths.
# Run after installation to confirm everything works.
# Expected result: all checks pass.

$pass = 0; $fail = 0; $warn = 0

# ── Auto-detect Python ────────────────────────────────────────────────────
$PY = $null
$pythonCandidates = @(
    "python",
    "python3",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python311\python.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python310\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
)
foreach ($c in $pythonCandidates) {
    try {
        $v = & $c --version 2>&1
        if ($v -match "Python 3\.") { $PY = $c; break }
    } catch {}
}

# ── Auto-detect config paths ──────────────────────────────────────────────
$OV_DIR     = "$env:USERPROFILE\.openviking"
$MEM_DIR    = "$env:USERPROFILE\.claude-memory"
$HOOKS_DIR  = "$env:USERPROFILE\.claude\hooks"
$CONF_FILE  = "$OV_DIR\ov.conf"
$CLI_CONF   = "$OV_DIR\ovcli.conf"
$STATE_FILE = "$MEM_DIR\.session_state.json"

# ── Auto-detect API key from ovcli.conf ──────────────────────────────────
$OV_KEY = "local-key"
if (Test-Path $CLI_CONF) {
    try {
        $cli = Get-Content $CLI_CONF -Raw | ConvertFrom-Json
        if ($cli.api_key) { $OV_KEY = $cli.api_key }
    } catch {}
}

$OV_URL  = "http://127.0.0.1:1933"
$OV_USER = "default"
$OV_ACCT = "default"

$H = @{
    "Authorization"        = "Bearer $OV_KEY"
    "x-api-key"            = $OV_KEY
    "x-openviking-user"    = $OV_USER
    "x-openviking-account" = $OV_ACCT
}

function OV-Get($path) {
    try {
        return Invoke-RestMethod -Uri "$OV_URL$path" -Method GET `
               -Headers $H -TimeoutSec 10 -UseBasicParsing
    } catch { return $null }
}

function OV-Post($path, $bodyObj) {
    try {
        $body = $bodyObj | ConvertTo-Json -Compress
        return Invoke-RestMethod -Uri "$OV_URL$path" -Method POST `
               -Headers $H -ContentType "application/json" `
               -Body $body -TimeoutSec 30 -UseBasicParsing
    } catch { return $null }
}

function SafeGet($obj, $prop) {
    if ($obj -and $obj.PSObject.Properties.Name -contains $prop) {
        return $obj.$prop
    }
    return $null
}

function Show-OK($label, $detail) {
    Write-Host "  OK   $label" -ForegroundColor Green
    if ($detail) { Write-Host "       $detail" -ForegroundColor Gray }
    $script:pass++
}

function Show-FAIL($label, $detail) {
    Write-Host "  FAIL $label" -ForegroundColor Red
    if ($detail) { Write-Host "       $detail" -ForegroundColor Yellow }
    $script:fail++
}

function Show-WARN($label, $detail) {
    Write-Host "  WARN $label" -ForegroundColor Yellow
    if ($detail) { Write-Host "       $detail" -ForegroundColor Gray }
    $script:warn++
}

# =========================================================================
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  openviking-claude-desktop — System Verification" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# ── SECTION 1: Prerequisites ──────────────────────────────────────────────
Write-Host "1. PREREQUISITES" -ForegroundColor Yellow

# Python
if ($PY) {
    $pyVer = & $PY --version 2>&1
    Show-OK "Python found: $pyVer" "Path: $PY"
} else {
    Show-FAIL "Python not found" "Install Python 3.10+ from python.org"
}

# Node.js
try {
    $nodeVer = node --version 2>&1
    if ($nodeVer -match "v\d+") {
        $major = [int]($nodeVer -replace "v(\d+).*",'$1')
        if ($major -ge 16) {
            Show-OK "Node.js $nodeVer"
        } else {
            Show-WARN "Node.js $nodeVer (v16+ recommended)" "Upgrade from nodejs.org"
        }
    } else { Show-FAIL "Node.js not found" "Install from nodejs.org" }
} catch { Show-FAIL "Node.js not found" "Install from nodejs.org" }

# OpenViking Python package
if ($PY) {
    $ovVer = & $PY -c "import openviking; print(openviking.__version__)" 2>&1
    if ($ovVer -match "\d+\.\d+\.\d+") {
        Show-OK "openviking package v$ovVer"
    } else {
        Show-FAIL "openviking package not installed" "Run: pip install openviking openviking-cli"
    }
    $ovCliVer = & $PY -c "import openviking_cli; print('ok')" 2>&1
    if ($ovCliVer -match "ok") {
        Show-OK "openviking_cli package installed"
    } else {
        Show-FAIL "openviking_cli not installed" "Run: pip install openviking-cli"
    }
}

# ── SECTION 2: Files & Directories ───────────────────────────────────────
Write-Host ""
Write-Host "2. INSTALLATION FILES" -ForegroundColor Yellow

$requiredFiles = [ordered]@{
    "$OV_DIR\openviking-bridge.py"   = "MCP bridge (Claude Desktop)"
    "$OV_DIR\openviking-watchdog.py" = "Server watchdog"
    "$OV_DIR\restart-openviking.ps1" = "Manual restart script"
    "$OV_DIR\select-embedding.ps1"   = "Embedding provider selector"
    "$OV_DIR\ov.conf"                = "Server configuration"
    "$OV_DIR\ovcli.conf"             = "CLI configuration"
    "$HOOKS_DIR\ov-recall.js"        = "Claude Code auto-recall hook"
    "$HOOKS_DIR\ov-capture.js"       = "Claude Code auto-capture hook"
    "$MEM_DIR\hooks\ov_session.py"   = "Autosave session script"
}

foreach ($kv in $requiredFiles.GetEnumerator()) {
    if (Test-Path $kv.Key) {
        Show-OK (Split-Path $kv.Key -Leaf) $kv.Value
    } else {
        Show-FAIL (Split-Path $kv.Key -Leaf) "Missing: $($kv.Key)"
    }
}

# ── SECTION 3: Configuration ──────────────────────────────────────────────
Write-Host ""
Write-Host "3. CONFIGURATION" -ForegroundColor Yellow

if (Test-Path $CONF_FILE) {
    try {
        $conf = Get-Content $CONF_FILE -Raw | ConvertFrom-Json

        # Check no invalid fields
        $validFields = @("storage","log","embedding","vlm","server")
        $confFields  = $conf.PSObject.Properties.Name
        $invalidFields = $confFields | Where-Object { $validFields -notcontains $_ }

        if ($invalidFields) {
            Show-FAIL "ov.conf has invalid fields: $($invalidFields -join ', ')" `
                      "Remove them — any extra field crashes the server"
        } else {
            Show-OK "ov.conf — no invalid fields"
        }

        # Check required sections
        foreach ($f in $validFields) {
            if ($confFields -contains $f) {
                Show-OK "ov.conf.$f section present"
            } else {
                Show-FAIL "ov.conf.$f section missing"
            }
        }

        # Check port
        $port = SafeGet (SafeGet $conf "server") "port"
        if ($port -eq 1933) {
            Show-OK "server.port = 1933"
        } else {
            Show-WARN "server.port = $port (expected 1933)" "Update if intentional"
        }

        # Check API key not placeholder
        $key = SafeGet (SafeGet $conf "server") "root_api_key"
        if ($key -and $key -ne "YOUR_LOCAL_API_KEY" -and $key.Length -gt 4) {
            Show-OK "server.root_api_key configured"
        } else {
            Show-FAIL "server.root_api_key not set" "Replace YOUR_LOCAL_API_KEY in ov.conf"
        }

        # Check embedding
        $embProvider = SafeGet (SafeGet (SafeGet $conf "embedding") "dense") "provider"
        $embKey      = SafeGet (SafeGet (SafeGet $conf "embedding") "dense") "api_key"
        Show-OK "embedding.provider = $embProvider"
        if ($embKey -and $embKey -ne "YOUR_JINA_API_KEY" -and $embKey -ne "ollama") {
            Show-OK "embedding.api_key configured"
        } elseif ($embKey -eq "ollama") {
            Show-OK "embedding.api_key = ollama (local)"
        } else {
            Show-FAIL "embedding.api_key not set" "Replace YOUR_JINA_API_KEY in ov.conf"
        }

        # Check VLM
        $vlmKey = SafeGet (SafeGet $conf "vlm") "api_key"
        if ($vlmKey -and $vlmKey -ne "YOUR_ANTHROPIC_API_KEY" -and $vlmKey.Length -gt 10) {
            Show-OK "vlm.api_key (Anthropic) configured"
        } else {
            Show-FAIL "vlm.api_key not set" "Replace YOUR_ANTHROPIC_API_KEY in ov.conf"
        }

    } catch {
        Show-FAIL "ov.conf is not valid JSON" "Check syntax: $($_.Exception.Message)"
    }
} else {
    Show-FAIL "ov.conf not found" "Copy config\ov.conf.example to $OV_DIR\ov.conf"
}

# Check Claude Desktop config
$desktopConf = "$env:APPDATA\Claude\claude_desktop_config.json"
if (Test-Path $desktopConf) {
    $raw = Get-Content $desktopConf -Raw
    if ($raw -match "openviking") {
        Show-OK "Claude Desktop MCP config found"
    } else {
        Show-WARN "Claude Desktop config exists but no openviking entry" `
                  "Add mcpServers entry per README"
    }
} else {
    Show-WARN "Claude Desktop config not found" `
              "Create %APPDATA%\Claude\claude_desktop_config.json"
}

# Check Claude Code settings
$ccSettings = "$env:USERPROFILE\.claude\settings.json"
if (Test-Path $ccSettings) {
    $raw = Get-Content $ccSettings -Raw
    $hasRecall  = $raw -match "ov-recall"
    $hasCapture = $raw -match "ov-capture"
    if ($hasRecall)  { Show-OK "ov-recall.js wired in settings.json" }
    else             { Show-FAIL "ov-recall.js not found in settings.json" "Add UserPromptSubmit hook per README" }
    if ($hasCapture) { Show-OK "ov-capture.js wired in settings.json" }
    else             { Show-FAIL "ov-capture.js not found in settings.json" "Add Stop hook per README" }
} else {
    Show-WARN "Claude Code settings.json not found" "$ccSettings"
}

# ── SECTION 4: Server ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "4. SERVER STATUS" -ForegroundColor Yellow

$health = OV-Get "/health"
if ($health) {
    $ver     = SafeGet $health "version"
    $healthy = SafeGet $health "healthy"
    if ($healthy -eq $true) {
        Show-OK "Server running on port 1933  (v$ver)"
    } else {
        Show-FAIL "Server returned unhealthy status"
    }
} else {
    Show-FAIL "Server not responding on port 1933" `
              "Run: & '$OV_DIR\restart-openviking.ps1'"
}

$status = OV-Get "/api/v1/system/status"
if ($status) { Show-OK "System status endpoint OK" }
else         { Show-WARN "System status endpoint not responding" }

# ── SECTION 5: API Endpoints ──────────────────────────────────────────────
Write-Host ""
Write-Host "5. API ENDPOINTS" -ForegroundColor Yellow

if ($health) {
    # Search find
    $find = OV-Post "/api/v1/search/find" @{ query = "test"; limit = 3 }
    if ($find) {
        $res   = SafeGet $find "result"
        $items = if ($res) { SafeGet $res "resources" } else { $null }
        $count = if ($items) { @($items).Count } else { 0 }
        Show-OK "/api/v1/search/find  ($count results)"
    } else {
        Show-FAIL "/api/v1/search/find failed" "Check headers and server logs"
    }

    # Search search
    $search = OV-Post "/api/v1/search/search" @{ query = "test"; limit = 3 }
    if ($search) { Show-OK "/api/v1/search/search" }
    else         { Show-FAIL "/api/v1/search/search failed" }

    # Session lifecycle
    $sessCreate = OV-Post "/api/v1/sessions" @{}
    $sessId = $null
    if ($sessCreate) {
        $res    = SafeGet $sessCreate "result"
        $sessId = if ($res) { SafeGet $res "session_id" } else { $null }
        if (-not $sessId) { $sessId = SafeGet $sessCreate "session_id" }
    }

    if ($sessId) {
        Show-OK "Create session  (id: $($sessId.Substring(0,8))...)"

        $addMsg = OV-Post "/api/v1/sessions/$sessId/messages" @{
            role    = "user"
            content = "verify-ov-hooks.ps1 test message"
        }
        if ($addMsg) { Show-OK "Add message  (role+content format)" }
        else         { Show-FAIL "Add message failed" }

        $commit    = OV-Post "/api/v1/sessions/$sessId/commit" @{}
        $extracted = 0
        if ($commit) {
            $res = SafeGet $commit "result"
            if ($res) {
                $x = SafeGet $res "memories_extracted"
                if ($null -ne $x) { $extracted = $x }
            }
            Show-OK "Commit session  ($extracted memories extracted)"
        } else {
            Show-FAIL "Commit session failed"
        }
    } else {
        Show-FAIL "Create session failed" "Check API key and headers"
    }
} else {
    Show-WARN "Skipping API tests — server not running" ""
    $script:warn += 5
}

# ── SECTION 6: Session State ──────────────────────────────────────────────
Write-Host ""
Write-Host "6. SESSION STATE" -ForegroundColor Yellow

if (Test-Path $STATE_FILE) {
    try {
        $s   = Get-Content $STATE_FILE -Raw | ConvertFrom-Json
        $cur = SafeGet $s "current_session"
        $lc  = SafeGet $s "last_commit"
        Show-OK "State file exists"
        Write-Host "       current_session : $(if ($cur) { $cur } else { 'None (will be created on next autosave)' })" -ForegroundColor Gray
        if ($lc) {
            $lat = SafeGet $lc "committed_at"
            $mem = SafeGet $lc "memories_extracted"
            Write-Host "       last_commit     : $lat  ($mem memories)" -ForegroundColor Gray
        }
    } catch {
        Show-WARN "State file exists but could not be parsed" ""
    }
} else {
    Show-WARN "State file not found yet" "Will be created automatically on first autosave"
}

# ── SECTION 7: Data Store ─────────────────────────────────────────────────
Write-Host ""
Write-Host "7. DATA STORE" -ForegroundColor Yellow

$workspace = "$MEM_DIR\viking\default\resources"
if (Test-Path $workspace) {
    $dirs = @(Get-ChildItem $workspace -Directory -EA SilentlyContinue)
    if ($dirs.Count -gt 0) {
        $totalMd = @(Get-ChildItem $workspace -Recurse -File -Filter "*.md" -EA SilentlyContinue).Count
        Show-OK "Resources directory  ($($dirs.Count) folders, $totalMd .md files)"
        foreach ($d in $dirs | Select-Object -First 5) {
            $mds  = @(Get-ChildItem $d.FullName -Recurse -File -Filter "*.md" -EA SilentlyContinue).Count
            $subs = @(Get-ChildItem $d.FullName -Directory -EA SilentlyContinue).Count
            Write-Host "       $($d.Name): $subs sub-dirs, $mds files" -ForegroundColor Gray
        }
        if ($dirs.Count -gt 5) {
            Write-Host "       ... and $($dirs.Count - 5) more directories" -ForegroundColor DarkGray
        }
    } else {
        Show-WARN "Resources directory is empty" "Add data via ov_add_resource or place .md files here"
    }
} else {
    Show-WARN "Resources directory not found yet" "Will be created when you add first resource"
}

# ── SECTION 8: Scheduled Tasks ────────────────────────────────────────────
Write-Host ""
Write-Host "8. SCHEDULED TASKS" -ForegroundColor Yellow

$tasks = @{
    "OpenViking-Watchdog"     = "Server monitor (restarts on crash)"
    "OpenViking-AutoSave"     = "Session autosave (every 30 min)"
    "OpenViking-HealthAlert"  = "Health alert + auto-restart (every 15 min)"
}
foreach ($kv in $tasks.GetEnumerator()) {
    $t = Get-ScheduledTask -TaskName $kv.Key -EA SilentlyContinue
    if ($t) {
        Show-OK "$($kv.Key)  [$($t.State)]" $kv.Value
    } else {
        Show-WARN "$($kv.Key) not registered" "Run register-ov-*.ps1 scripts"
    }
}

# ── SECTION 9: Log Files ──────────────────────────────────────────────────
Write-Host ""
Write-Host "9. LOG FILES" -ForegroundColor Yellow

$logDir = "$MEM_DIR\logs"
if (Test-Path $logDir) {
    $logs = @(Get-ChildItem $logDir -File -EA SilentlyContinue)
    Show-OK "Log directory exists  ($($logs.Count) files)"

    # Show last line of key logs
    foreach ($lf in @("ov-recall.log","ov-capture.log","session-hooks.log","openviking-watchdog.log")) {
        $fullPath = "$logDir\$lf"
        if (Test-Path $fullPath) {
            $last = Get-Content $fullPath -Tail 1 -EA SilentlyContinue
            if ($last) {
                Write-Host "       $lf" -ForegroundColor Cyan
                Write-Host "         $last" -ForegroundColor DarkGray
            }
        }
    }
} else {
    Show-WARN "Log directory not found" "Will be created automatically"
}

# ── SUMMARY ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
$color = if ($fail -eq 0) { "Green" } elseif ($fail -le 3) { "Yellow" } else { "Red" }
Write-Host "  RESULT: $pass passed  |  $warn warnings  |  $fail failed" -ForegroundColor $color
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

if ($fail -eq 0 -and $warn -eq 0) {
    Write-Host "  Perfect. All checks passed. System fully operational." -ForegroundColor Green
} elseif ($fail -eq 0) {
    Write-Host "  All critical checks passed. Review warnings above." -ForegroundColor Yellow
} else {
    Write-Host "  $fail critical check(s) failed. Fix FAIL items above." -ForegroundColor Red
    Write-Host ""
    if (-not $health) {
        Write-Host "  Server is down. Start it:" -ForegroundColor Red
        Write-Host "  & '$OV_DIR\restart-openviking.ps1'" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "  Logs: $logDir" -ForegroundColor DarkGray
Write-Host "  Docs: https://github.com/YOUR_USERNAME/openviking-claude-desktop" -ForegroundColor DarkGray
Write-Host ""
Read-Host "Press Enter to close"
