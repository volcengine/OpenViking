<#
.SYNOPSIS
  Re-imports specific OpenViking projects, waiting for each task with a ceiling.

.DESCRIPTION
  Replaces the ad-hoc reimport scripts that were used while recovering from the
  duplicate-resource mess. Those had two defects worth not repeating:

    - they matched `root_uri` in the output of `ov add-resource`, which only
      returns once the task is queued. That pattern never matched, so every
      project was logged FAIL no matter what actually happened, and no result was
      ever confirmed;
    - they removed the old copy first and then never checked the import, so a
      failed import left the project missing from the index entirely.

  Here the old copy is removed, the import is awaited through the shared task
  helper, and the outcome comes from the task result. A project that times out is
  reported as such and is left for the next run rather than silently lost.

.PARAMETER Project
  One or more project directory paths to re-import.

.PARAMETER TaskTimeoutMinutes
  Ceiling on a single import. The task keeps running server-side past this.

.EXAMPLE
  .\reimport-projects.ps1 -Project D:\Workspace\AGV,D:\Workspace\HEMY
  .\reimport-projects.ps1 -Project D:\Workspace\AGV -TaskTimeoutMinutes 45 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [Alias('Path', 'Dir')]
    # Accepts space- or comma-separated values. Start-Process -ArgumentList splits on
    # whitespace and has no array syntax, so "A,B" arrives as the single literal path
    # "A,B"; normalising here means the script behaves the same under both launchers.
    [string[]]$Project,
    [string]$Parent = 'viking://resources',
    [int]$TaskTimeoutMinutes = 45,
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'ov-task-common.ps1')

$Project = @(
    foreach ($p in $Project) {
        $p -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
    }
)

# *.pdf is excluded on purpose: OpenViking aborts an entire directory import when
# any single file fails to parse, and scanned or malformed PDFs fail that way.
$INC = '*.md,*.markdown,*.txt,*.rst,*.adoc,*.ipynb,' +
       '*.py,*.pyi,*.js,*.mjs,*.cjs,*.ts,*.tsx,*.jsx,*.vue,*.svelte,' +
       '*.java,*.kt,*.kts,*.scala,*.go,*.rs,*.rb,*.php,*.c,*.h,*.cc,*.cpp,*.cxx,*.hpp,*.hh,' +
       '*.cs,*.swift,*.m,*.mm,*.jl,*.f90,*.f95,*.f,*.for,*.cu,*.cuh,' +
       '*.sh,*.bash,*.zsh,*.ps1,*.bat,*.cmd,*.sql,*.graphql,' +
       '*.json,*.jsonc,*.yaml,*.yml,*.toml,*.ini,*.cfg,*.conf,*.env,*.xml,' +
       '*.html,*.htm,*.css,*.scss,*.sass,*.less,*.csv,*.tsv,*.tex,*.bib'

function Write-Step { param([string]$m) Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

$health = & $script:OvExe health 2>&1 | Out-String
if ($health -notmatch 'Connected') {
    Write-Step 'OpenViking is not reachable; aborting.'
    exit 1
}

# Concurrent imports collide on the shared temp/upload archive and fail with
# WinError 32, which reads like a permissions problem but is really a collision.
$busy = Assert-OvIdle
if ($busy) {
    Write-Step 'REFUSING TO START:'
    Write-Step "  $busy"
    Write-Step '  Nothing was submitted. Check again with: ov task list'
    exit 2
}

$ok = 0; $bad = 0; $slow = 0

foreach ($path in $Project) {
    $name = Split-Path $path -Leaf
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Step "SKIP   $name (path missing)"
        $bad++
        continue
    }

    if ($PSCmdlet.ShouldProcess($path, "re-import into $Parent")) {
        Write-Step "START  $name"

        # Free the base name so the import cannot create a numbered sibling.
        $json = & $script:OvExe ls $Parent -o json --limit 5000 --node-limit 20000 --fields uri,name 2>$null | Out-String
        if ($json -and $json.Contains('{')) {
            $res = @(($json.Substring($json.IndexOf('{'))) | ConvertFrom-Json).result
            $esc = [regex]::Escape($name)
            foreach ($r in ($res | Where-Object { $_.name -match "^$esc(_\d+)?$" })) {
                & $script:OvExe rm $r.uri --recursive 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Step "       removed stale $($r.name)" }
                else { Write-Step "       WARN  could not remove $($r.name); a numbered sibling may appear" }
            }
        }

        $r = Invoke-OvAddResource -Path $path -Parent $Parent -Include $INC `
                                 -TimeoutMinutes $TaskTimeoutMinutes -IntervalSeconds $IntervalSeconds

        if ($r.TimedOut) {
            Write-Step ("TIMEOUT $name after {0} min; still running server-side, rerun to retry" -f $TaskTimeoutMinutes)
            $slow++
        } elseif ($r.Success) {
            Write-Step ("OK     $name -> {0}" -f $r.RootUri)
            $ok++
        } else {
            $detail = if ($r.FailedFiles.Count) { 'failed files: ' + ($r.FailedFiles -join ', ') }
                      elseif ($r.Error) { $r.Error } else { 'unknown error' }
            Write-Step ("FAIL   $name : {0}" -f $detail)
            $bad++
        }
    } else {
        Write-Step "WOULD  $name"
    }
    Start-Sleep -Seconds 2
}

Write-Step ("=== done: {0} ok, {1} failed, {2} timed out ===" -f $ok, $bad, $slow)
if ($bad -gt 0 -or $slow -gt 0) { exit 1 }
exit 0
