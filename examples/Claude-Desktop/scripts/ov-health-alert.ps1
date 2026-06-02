Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# ov-health-alert.ps1
# Checks OpenViking health every 15 min (via scheduled task).
# Shows a Windows notification and auto-restarts if server is down.
# Register using: register-ov-health-alert.ps1

$OV_KEY  = (Get-Content "$env:USERPROFILE\.openviking\ovcli.conf" -Raw | ConvertFrom-Json).api_key
$OV_URL  = "http://127.0.0.1:1933/health"
$LOG     = "$env:USERPROFILE\.claude-memory\logs\health-alert.log"
$RESTART = "$env:USERPROFILE\.openviking\restart-openviking.ps1"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG -Value "[$ts] $msg" -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Show-Notification($title, $msg) {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Warning
        $notify.BalloonTipTitle = $title
        $notify.BalloonTipText  = $msg
        $notify.BalloonTipIcon  = "Warning"
        $notify.Visible = $true
        $notify.ShowBalloonTip(8000)
        Start-Sleep -Seconds 9
        $notify.Visible = $false
        $notify.Dispose()
    } catch {}
}

$up = $false
try {
    $r  = Invoke-WebRequest -Uri $OV_URL `
          -Headers @{ Authorization = "Bearer $OV_KEY" } `
          -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    $up = ($r.StatusCode -eq 200)
} catch {}

if ($up) {
    Write-Log "Server UP"
} else {
    Write-Log "Server DOWN - alerting and auto-restarting"
    Show-Notification "OpenViking DOWN" "Memory server offline. Auto-restarting now."
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RESTART`"" -WindowStyle Hidden
    Write-Log "Restart launched."
}
