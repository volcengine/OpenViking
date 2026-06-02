Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

$PY  = "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe"
$OV  = "$env:USERPROFILE\.openviking"
$LOG = "$env:USERPROFILE\.claude-memory\logs"

Write-Host "=== OpenViking Restart ===" -ForegroundColor Cyan

# Kill any existing stuck processes
Write-Host "Killing old processes..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdLine = ""
    try {
        $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" -EA SilentlyContinue
        if ($wmi) { $cmdLine = $wmi.CommandLine }
    } catch {}
    if ($cmdLine -like "*openviking*" -or $cmdLine -like "*uvicorn*") {
        $_.Kill()
        Write-Host "  Killed PID $($_.Id)" -ForegroundColor Yellow
    }
}
Start-Sleep -Seconds 3

# Select embedding provider
Write-Host "Selecting embedding provider..." -ForegroundColor Yellow
& "$OV\select-embedding.ps1"

# Test entry point
Write-Host ""
Write-Host "Testing entry point (openviking_cli.server_bootstrap)..." -ForegroundColor Yellow
$test = & $PY -c "from openviking_cli.server_bootstrap import main; print('OK')" 2>&1
if ($test -match "OK") {
    Write-Host "  Module import: OK" -ForegroundColor Green
} else {
    Write-Host "  Module import FAILED: $test" -ForegroundColor Red
}

# Start server
Write-Host ""
Write-Host "Starting server on port 1933..." -ForegroundColor Yellow
$logFile    = "$LOG\openviking-server-$(Get-Date -Format 'yyyyMMdd').log"
$logFileErr = "$LOG\openviking-server-$(Get-Date -Format 'yyyyMMdd')-err.log"

if (-not (Test-Path $LOG)) { New-Item -ItemType Directory -Path $LOG -Force | Out-Null }

Start-Process -FilePath $PY -ArgumentList @("-m", "openviking_cli.server_bootstrap") `
    -WorkingDirectory $OV `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError  $logFileErr `
    -WindowStyle Hidden

Write-Host "  Waiting 15 seconds for server to initialize..."
Start-Sleep -Seconds 15

# Health check
Write-Host ""
Write-Host "Health check..." -ForegroundColor Yellow
$OV_KEY = (Get-Content "$OV\ovcli.conf" -Raw | ConvertFrom-Json).api_key
try {
    $r = Invoke-WebRequest -Uri "http://localhost:1933/health" `
         -Headers @{ Authorization = "Bearer $OV_KEY" } `
         -TimeoutSec 5 -UseBasicParsing
    Write-Host "  STATUS: $($r.StatusCode) - OpenViking is UP" -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Last 10 lines of server log:" -ForegroundColor Yellow
    foreach ($f in @($logFile, $logFileErr)) {
        if (Test-Path $f) {
            Write-Host "  [$f]" -ForegroundColor DarkGray
            Get-Content $f -Tail 10 | ForEach-Object { Write-Host "    $_" }
        }
    }
}

# Restart watchdog task
Write-Host ""
Write-Host "Restarting watchdog task..." -ForegroundColor Yellow
$WATCHDOG_TASK = "OpenViking-Watchdog"
Stop-ScheduledTask  -TaskName $WATCHDOG_TASK -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$task = Get-ScheduledTask -TaskName $WATCHDOG_TASK -ErrorAction SilentlyContinue
if ($task) {
    Start-ScheduledTask -TaskName $WATCHDOG_TASK -ErrorAction SilentlyContinue
    Write-Host "  Watchdog task: $((Get-ScheduledTask -TaskName $WATCHDOG_TASK).State)" -ForegroundColor Green
} else {
    Write-Host "  Watchdog task not found - run register-ov-watchdog.ps1" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Restart Claude Desktop to reconnect openviking-memory MCP." -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to close"
