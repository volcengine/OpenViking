<#
.SYNOPSIS
  Shared helpers for OpenViking task scripts: bounded waiting and timeout guards.

.DESCRIPTION
  Every ov add-resource returns as soon as the task is queued, so scripts that
  look like they finished may still have work running. Polling that output
  forever hangs the caller without adding information, so these helpers put a
  hard ceiling on every wait.

  Waiting never cancels a task. The server owns the task and may still finish
  it; the point is that the calling script stops blocking.
#>

$script:OvExe = 'C:\Python314\Scripts\ov.exe'

function Get-OvTask {
    <#
      .SYNOPSIS
        Reads a task record. Returns $null when the task is unknown to the server.
    #>
    param([Parameter(Mandatory = $true)][string]$TaskId)

    $json = & $script:OvExe task status $TaskId -o json 2>$null | Out-String
    if (-not $json -or $json -notmatch '"ok":\s*true') { return $null }

    $status = if ($json -match '"status":\s*"(\w+)"')    { $Matches[1] } else { 'unknown' }
    $stage  = if ($json -match '"stage":\s*"([\w_]+)"') { $Matches[1] } else { '-' }
    $error  = if ($json -match '"error":\s*"([^"]*)"')  { $Matches[1] } else { $null }

    [pscustomobject]@{
        TaskId  = $TaskId
        Status  = $status
        Stage   = $stage
        Error   = $error
        Raw     = $json
        Success = ($status -in @('completed', 'success'))
        Failed  = ($status -in @('failed', 'cancelled'))
    }
}

function Wait-OvTask {
    <#
      .SYNOPSIS
        Waits for a task to reach a terminal state, with a hard ceiling.

      .PARAMETER TimeoutMinutes
        Give up waiting after this many minutes. The task keeps running server-side.

      .PARAMETER IntervalSeconds
        Poll period.

      .OUTPUTS
        The task record from Get-OvTask, or $null on timeout.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$TaskId,
        [int]$TimeoutMinutes = 20,
        [int]$IntervalSeconds = 30,
        [string]$LogPrefix = ''
    )

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ($true) {
        $t = Get-OvTask -TaskId $TaskId
        if (-not $t) {
            if ($LogPrefix) { Write-Host "$LogPrefix task $TaskId is not visible to the server" }
            return $null
        }

        if ($LogPrefix) {
            Write-Host ("{0}[{1}] status={2,-10} stage={3}" -f $LogPrefix, (Get-Date -Format 'HH:mm:ss'), $t.Status, $t.Stage)
        }

        if ($t.Success -or $t.Failed) { return $t }

        if ((Get-Date) -gt $deadline) {
            if ($LogPrefix) {
                Write-Host ("{0}TIMEOUT: task {1} still {2}/{3} after {4} min; left running on the server." -f `
                    $LogPrefix, $TaskId, $t.Status, $t.Stage, $TimeoutMinutes)
            }
            return $null
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
}

function Get-OvRunningTasks {
    <#
      .SYNOPSIS
        Lists add_resource / session_commit tasks the server still reports as running.
    #>
    $json = & $script:OvExe task list -o json 2>$null | Out-String
    if (-not $json -or $json -notmatch '"ok":\s*true') { return @() }

    $out = @()
    foreach ($m in [regex]::Matches($json,
        '"task_id":\s*"([0-9a-f-]+)"[^}]*?"task_type":\s*"(\w+)"[^}]*?"status":\s*"(\w+)"')) {
        if ($m.Groups[3].Value -eq 'running') {
            $out += [pscustomobject]@{
                TaskId = $m.Groups[1].Value
                Type   = $m.Groups[2].Value
            }
        }
    }
    return $out
}

function Assert-OvIdle {
    <#
      .SYNOPSIS
        Refuses to submit while another add_resource is running.

      .DESCRIPTION
        Concurrent directory imports collide: each one packs its tree into
        data/temp/upload/upload_<id>.zip, and the first one to finish or abort can
        leave its archive open, so every later submit fails with
        [PERMISSION_DENIED] [WinError 32] "another process is using this file".
        That looks like a permission problem but is a concurrency one, and
        retrying blindly just accumulates more orphaned archives.

        add_resource and session_commit do not share the same archive path, so only
        add_resource is treated as blocking.
    #>
    $busy = @(Get-OvRunningTasks | Where-Object { $_.Type -eq 'add_resource' })
    if ($busy.Count -eq 0) { return $null }

    $msg = ("{0} add_resource task(s) still running: {1}. " +
            "Wait for them, or stop the server to clear them, before importing.") -f `
            $busy.Count, (($busy | ForEach-Object { $_.TaskId.Substring(0, 8) }) -join ', ')
    return $msg
}

function Invoke-OvAddResource {
    <#
      .SYNOPSIS
        Submits a resource and waits for it, bounded by TimeoutMinutes.

      .DESCRIPTION
        Fixes a real bug in the original sync script: it recorded the project as
        imported as soon as the task was accepted, so a task that later failed
        still left a "synced" fingerprint on disk and was never retried.

      .OUTPUTS
        A record with Success, TimedOut, RootUri, FailedFiles and the raw result.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Parent = 'viking://resources',
        [string]$Include,
        [int]$TimeoutMinutes = 30,
        [int]$IntervalSeconds = 30,
        [switch]$NoWait
    )

    $args = @('add-resource', $Path, '--parent', $Parent, '--no-progress')
    if ($Include) { $args += @('--include', $Include) }

    $out = & $script:OvExe @args 2>&1 | Out-String
    $taskId = if ($out -match 'task_id\s+(\S+)') { $Matches[1] } else { $null }

    if (-not $taskId) {
        return [pscustomobject]@{
            Success = $false; TimedOut = $false; RootUri = $null
            FailedFiles = @(); Error = (($out -replace '\s+', ' ').Trim())
            Raw = $out
        }
    }

    if ($NoWait) {
        return [pscustomobject]@{
            Success = $true; TimedOut = $false; RootUri = $null
            FailedFiles = @(); TaskId = $taskId; Error = $null; Raw = $out
        }
    }
    $t = Wait-OvTask -TaskId $taskId -TimeoutMinutes $TimeoutMinutes -IntervalSeconds $IntervalSeconds
    if (-not $t) {
        return [pscustomobject]@{
            Success = $false; TimedOut = $true; RootUri = $null
            FailedFiles = @(); TaskId = $taskId
            Error = "task $taskId did not finish within $TimeoutMinutes min; still running"
            Raw = $null
        }
    }

    $rootUri  = if ($t.Raw -match '"root_uri":\s*"([^"]+)"') { $Matches[1] } else { $null }
    $failed   = @()
    if ($t.Raw -match '"failed_files":\s*\[([^\]]*)\]') {
        $failed = @([regex]::Matches($Matches[1], '"path":\s*"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
    }

    return [pscustomobject]@{
        Success     = ($t.Success -and $failed.Count -eq 0)
        TimedOut    = $false
        RootUri     = $rootUri
        FailedFiles = $failed
        TaskId      = $taskId
        Error       = $t.Error
        Raw         = $t.Raw
    }
}
