Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# register-ov-watchdog.ps1
# Registers OpenViking watchdog as a Windows Scheduled Task.
# Runs at logon for the current user. Does NOT require Admin.

$PYTHON = "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe"
$SCRIPT = "$env:USERPROFILE\.openviking\openviking-watchdog.py"
$TASK   = "OpenViking-Watchdog"

Write-Host "Registering OpenViking Watchdog as scheduled task..." -ForegroundColor Cyan

$existing = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TASK -Confirm:$false
    Write-Host "  Removed existing task." -ForegroundColor Yellow
}

$action = New-ScheduledTaskAction `
    -Execute $PYTHON `
    -Argument "`"$SCRIPT`"" `
    -WorkingDirectory "$env:USERPROFILE\.openviking"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TASK `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Auto-restarts OpenViking memory server on failure." `
    -Force | Out-Null

Write-Host "  [OK] Task '$TASK' registered." -ForegroundColor Green
Write-Host "  Trigger: At logon for $env:USERNAME" -ForegroundColor Green
Write-Host ""
Write-Host "  To start now without rebooting:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName '$TASK'"
Write-Host ""
Read-Host "Press Enter to close"
