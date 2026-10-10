# Waits for an OpenViking task with a hard ceiling instead of polling forever.
# Usage: .\wait-ov-task.ps1 -TaskId <id> [-TimeoutMinutes 20] [-IntervalSeconds 30]
param(
    [Parameter(Mandatory = $true)][string]$TaskId,
    [int]$TimeoutMinutes = 20,
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = 'Continue'
$ov = 'C:\Python314\Scripts\ov.exe'
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$tick = 0

while ($true) {
    $json = & $ov task status $TaskId -o json 2>$null | Out-String
    $status = if ($json -match '"status":\s*"(\w+)"')       { $Matches[1] } else { 'unknown' }
    $stage  = if ($json -match '"stage":\s*"([\w_]+)"')    { $Matches[1] } else { '-' }
    $secs   = if ($json -match '"processing_seconds":\s*([\d.]+)') { [math]::Round([double]$Matches[1],1) } else { 0 }

    Write-Host ("[{0}] status={1,-10} stage={2,-16} elapsed={3}s" -f (Get-Date -Format 'HH:mm:ss'), $status, $stage, $secs)

    if ($status -in @('completed','success','failed','cancelled')) {
        $err = if ($json -match '"error":\s*"([^"]*)"') { $Matches[1] } else { '' }
        if ($err) { Write-Host "error: $err" }
        exit $(if ($status -in @('failed','cancelled')) { 1 } else { 0 })
    }

    if ((Get-Date) -gt $deadline) {
        # Report, do not kill: the server owns the task and may still finish it.
        # The point is that this script exits instead of hanging the caller.
        Write-Host "TIMEOUT after $TimeoutMinutes min; task $TaskId left running on the server."
        exit 2
    }

    Start-Sleep -Seconds $IntervalSeconds
    $tick++
}
