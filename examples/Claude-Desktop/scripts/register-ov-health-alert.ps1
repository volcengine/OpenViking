Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# register-ov-health-alert.ps1
# Registers health check as a Windows Scheduled Task (every 15 min).
# Shows Windows notification and auto-restarts if server is down.
# Requires Admin elevation.

$TASK   = "OpenViking-HealthAlert"
$SCRIPT = "$env:USERPROFILE\.openviking\ov-health-alert.ps1"

Write-Host "=== Register OpenViking Health Alert Task ===" -ForegroundColor Cyan
Write-Host ""

if (Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TASK -Confirm:$false
    Write-Host "  Removed existing task" -ForegroundColor Yellow
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SCRIPT`""

$trigger = New-ScheduledTaskTrigger `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -Once -At (Get-Date)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

$reg = Register-ScheduledTask `
    -TaskName $TASK `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Every 15 min: checks OpenViking health, alerts and auto-restarts if down." `
    -Force

if ($reg) {
    Write-Host "  [OK] Task '$TASK' registered" -ForegroundColor Green
    Write-Host "  Checks every 15 min, auto-restarts if server down" -ForegroundColor Gray
    Write-Host "  Log: $env:USERPROFILE\.claude-memory\logs\health-alert.log" -ForegroundColor Gray
} else {
    Write-Host "  [FAIL] Registration failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}

Write-Host ""
Write-Host "  Running health check now..." -ForegroundColor Yellow
& $SCRIPT
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to close"
