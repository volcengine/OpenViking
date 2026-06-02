Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# register-ov-autosave.ps1
# Registers autosave as a Windows Scheduled Task (every 30 min).
# Commits active sessions and starts new ones to ensure memories are captured.
# Requires Admin elevation.

$PY     = "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe"
$SCRIPT = "$env:USERPROFILE\.claude-memory\hooks\ov_session.py"
$TASK   = "OpenViking-AutoSave"

Write-Host "=== OpenViking AutoSave Task Registration ===" -ForegroundColor Cyan
Write-Host ""

$oldNames = @("OpenViking-AutoSave","OpenViking-SessionCommit")
foreach ($t in $oldNames) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
        Write-Host "  Removed old task: $t" -ForegroundColor Yellow
    }
}

$action = New-ScheduledTaskAction `
    -Execute $PY `
    -Argument ('"' + $SCRIPT + '" autosave') `
    -WorkingDirectory "$env:USERPROFILE\.claude-memory\hooks"

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
$repTrigger   = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 30) -Once -At (Get-Date)
$logonTrigger.Repetition = $repTrigger.Repetition

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

$reg = Register-ScheduledTask `
    -TaskName $TASK `
    -Action $action `
    -Trigger $logonTrigger `
    -Settings $settings `
    -Principal $principal `
    -Description "OpenViking autosave every 30min - commits session or starts new one." `
    -Force

if ($reg) {
    Write-Host "  [OK] Task '$TASK' registered" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] Registration failed" -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}

Write-Host ""
Write-Host "  Running autosave now..." -ForegroundColor Yellow
& $PY $SCRIPT autosave
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to close"
