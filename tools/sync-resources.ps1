# OpenViking Resources Sync — incremental-by-fingerprint
#
# WHY THIS DESIGN (the previous version was broken):
#   `ov add-resource` is CREATE-ONLY. There is no --update/--overwrite/upsert, and
#   `--watch-interval` explicitly "only applies to remote-URL invocations". So every
#   run of `add-resource --parent` created a NEW numbered sibling (AGV, AGV_1, AGV_2,
#   ...), each fully re-vectorised -> 735 entries for 45 projects.
#
# THIS DESIGN:
#   1. health-gate   : skip cleanly when the server is down (the old script burned
#                      20 min failing every project)
#   2. fingerprint   : hash of (relpath, size, mtime) per project, cached in state.json
#   3. skip unchanged: no embedding work at all when nothing moved
#   4. changed       : delete the existing copy FIRST (so the base name is freed and
#                      no numbered sibling is created), then import once
#   5. correct result parsing: `root_uri` / `status` come from the command; the old
#                      script matched `task_id` against output that `SilentlyContinue`
#                      had already swallowed, so every project logged a bogus WARN.
#   6. wait for the task: `add-resource` returns once the task is QUEUED, so the old
#                      script recorded a fingerprint before the import ran and never
#                      retried a project whose import failed. Each task is now awaited
#                      with a ceiling ($TaskTimeoutMinutes, default 30); a project that
#                      times out keeps its old fingerprint and is retried next run.
#
# Caveat: a changed project is re-embedded in full (OpenViking has no delta mode for
# local paths). The fingerprint keeps that cost to *changed* projects only.

param(
    [switch]$Verify,
    [int]$TaskTimeoutMinutes = 30
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'ov-task-common.ps1')
$Home2   = Join-Path $env:USERPROFILE '.openviking'
$logFile = Join-Path $Home2 'logs\sync-resources.log'
$stateFile = Join-Path $Home2 'sync-state.json'
$ov = 'C:\Python314\Scripts\ov.exe'

New-Item -ItemType Directory -Force -Path (Split-Path $logFile -Parent) | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "{0} - {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    Write-Output $Message
}

# ---------------------------------------------------------------- 1. health gate
$health = & $ov health 2>&1 | Out-String
if ($health -notmatch 'Connected' -or $health -notmatch '(?i)healthy|ok') {
    Write-Log "SKIP: OpenViking server not healthy — nothing done."
    Write-Log "      (start it with: openviking-server --host 127.0.0.1 --port 1933)"
    exit 0
}
Write-Log "=== OV sync start (server healthy) ==="

# ---------------------------------------------------------------- projects
    # NOTE: *.pdf is deliberately NOT whitelisted. OpenViking aborts the entire directory
    # import when any single file fails to parse, and scanned/malformed PDFs fail with
    # "Parse failed: no content generated", which silently destroyed whole projects.
    # Text/code formats are reliable; PDFs go through MinerU via the parser, not --include.
    $INC = '*.md,*.markdown,*.txt,*.rst,*.adoc,*.ipynb,' +
       '*.py,*.pyi,*.js,*.mjs,*.cjs,*.ts,*.tsx,*.jsx,*.vue,*.svelte,' +
       '*.java,*.kt,*.kts,*.scala,*.go,*.rs,*.rb,*.php,*.c,*.h,*.cc,*.cpp,*.cxx,*.hpp,*.hh,' +
       '*.cs,*.swift,*.m,*.mm,*.jl,*.f90,*.f95,*.f,*.for,*.cu,*.cuh,' +
       '*.sh,*.bash,*.zsh,*.ps1,*.bat,*.cmd,*.sql,*.graphql,' +
       '*.json,*.jsonc,*.yaml,*.yml,*.toml,*.ini,*.cfg,*.conf,*.env,*.xml,' +
       '*.html,*.htm,*.css,*.scss,*.sass,*.less,*.csv,*.tsv,*.tex,*.bib'

# ---------------------------------------------------------------- verify mode
# Report what a real run would do and exit. Without this the switch was accepted
# and then ignored, so "-Verify" started importing everything — which is how a
# dry run once turned into a 15-minute import.
if ($Verify) {
    $state = @{}
    if (Test-Path $stateFile) {
        try {
            $raw = Get-Content $stateFile -Raw | ConvertFrom-Json
            foreach ($p in $raw.PSObject.Properties) { $state[$p.Name] = [string]$p.Value }
        } catch { $state = @{} }
    }
    Write-Log "=== VERIFY (no changes will be made) ==="
    Write-Log ("known projects in state: " + $state.Count)
    $wouldChange = 0
    foreach ($path in $directories) {
        $name = Split-Path $path -Leaf
        if (-not (Test-Path -LiteralPath $path)) { Write-Log "  MISSING   $name"; continue }
        $fp = Get-Fingerprint $path
        if (-not $fp) { Write-Log "  NO-FILES   $name"; continue }
        if ($state.ContainsKey($name) -and $state[$name] -eq $fp) {
            Write-Log "  unchanged  $name"
        } else {
            Write-Log "  WOULD-REIMPORT  $name"
            $wouldChange++
        }
    }
    Write-Log "=== verify done: $wouldChange project(s) would be reimported ==="
    exit 0
}

# ---------------------------------------------------------------- projects
$directories = @(
    'D:\Research\References',
    'D:\Workspace\waveguide-atom-interface\docs',
    'D:\Workspace\AGV', 'D:\Workspace\AstraDIY', 'D:\Workspace\CangjieSkills',
    'D:\Workspace\codegraph', 'D:\Workspace\DHEM', 'D:\Workspace\DocFlow',
    'D:\Workspace\ECC', 'D:\Workspace\HEMY', 'D:\Workspace\MindMemOS',
    'D:\Workspace\Mountzilla', 'D:\Workspace\Noctiluca', 'D:\Workspace\OnStepX',
    'D:\Workspace\openarm', 'D:\Workspace\openclaw', 'D:\Workspace\openscience',
    'D:\Workspace\optiland', 'D:\Workspace\PPT', 'D:\Workspace\SolarPanelProject',
    'D:\Workspace\tidybot', 'D:\Workspace\Trinus-3D-printer-hotbed',
    'D:\Workspace\Zero-to-CAD', 'D:\Workspace\text-to-cad', 'D:\Workspace\prime-agent',
    'D:\Workspace\headscale-console', 'D:\Workspace\kk-ai', 'D:\Workspace\LLMs-from-scratch',
    'D:\Workspace\md-reader', 'D:\Workspace\deepseek-harness', 'D:\Workspace\DeepSeek-Reasonix',
    'D:\Workspace\DeepSeek-TUI', 'D:\Workspace\deveco-toolbox', 'D:\Workspace\dtl-skills',
    'D:\Workspace\fireworks-tech-graph', 'D:\Workspace\oh-my-ppt',
    'D:\Workspace\openscience-mcp-server', 'D:\Workspace\mineru-mcp-server'
)

# ---------------------------------------------------------------- fingerprint
function Get-Fingerprint {
    param([string]$Path)
    $files = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -notmatch '\\(node_modules|\.git|dist|build|\.venv|__pycache__)\\' }
    if (-not $files) { return $null }
    $sb = New-Object System.Text.StringBuilder
    foreach ($f in $files) {
        [void]$sb.Append($f.FullName.Substring($Path.Length))
        [void]$sb.Append('|'); [void]$sb.Append($f.Length)
        [void]$sb.Append('|'); [void]$sb.Append($f.LastWriteTimeUtc.Ticks); [void]$sb.Append("`n")
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sb.ToString()))
    return [BitConverter]::ToString($bytes).Replace('-', '').Substring(0, 32)
}

# ---------------------------------------------------------------- state
# PS 5.1 has no ConvertFrom-Json -AsHashtable; using it silently threw, the catch
# reset $state to @{}, and EVERY project then looked "changed" -> mass re-import.
$state = @{}
if (Test-Path $stateFile) {
    try {
        $raw = Get-Content $stateFile -Raw | ConvertFrom-Json
        foreach ($p in $raw.PSObject.Properties) { $state[$p.Name] = [string]$p.Value }
        Write-Log "state loaded: $($state.Count) project(s) known"
    } catch {
        Write-Log "WARN could not parse $stateFile ($($_.Exception.Message)); treating all as changed"
        $state = @{}
    }
}

# ---------------------------------------------------------------- helpers
function Get-ExistingCopies {
    param([string]$Name)
    $json = & $ov ls 'viking://resources' -o json --limit 5000 --node-limit 20000 --fields uri,name 2>$null | Out-String
    $i = $json.IndexOf('{')
    if ($i -lt 0) { return @() }
    $res = @(($json.Substring($i) | ConvertFrom-Json).result)
    $esc = [regex]::Escape($Name)
    return @($res | Where-Object { $_.name -match "^$esc(_\d+)?$" } | ForEach-Object { $_.uri })
}

$added = 0; $skipped = 0; $changed = 0; $failed = 0; $timedOut = 0

foreach ($path in $directories) {
    $name = Split-Path $path -Leaf
    if (-not (Test-Path -LiteralPath $path)) { Write-Log "SKIP  $name (missing)"; continue }

    $fp = Get-Fingerprint $path
    if (-not $fp) { Write-Log "SKIP  $name (no indexable files)"; continue }

    if ($state.ContainsKey($name) -and $state[$name] -eq $fp) {
        Write-Log "SKIP  $name (unchanged)"
        $skipped++
        continue
    }

    $changed++
    Write-Log "CHANGE $name — refreshing"

    # free the base name so the import does NOT create a numbered sibling
    $copies = Get-ExistingCopies $name
    foreach ($c in $copies) {
        $rm = & $ov rm $c --recursive 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { Write-Log "       removed old $c" }
        else { Write-Log "       WARN could not remove $c ($(if ($rm -match '(?i)CONFLICT|being processed') {'still processing'} else {'error'}))" }
    }
    Start-Sleep -Seconds 2

    # Wait for the task instead of trusting the submit. ov add-resource returns once
    # the task is queued, so recording the fingerprint here without waiting marked
    # projects as synced even when the import later failed, and they were never retried.
    # Bounded so one stuck project cannot hang the whole run.
    $r = Invoke-OvAddResource -Path $path -Include $INC -TimeoutMinutes $TaskTimeoutMinutes
    if ($r.TimedOut) {
        Write-Log ("       TIMEOUT {0} after {1} min; not recording fingerprint so it retries next run" -f $name, $TaskTimeoutMinutes)
        $failed++
        $timedOut++
        continue
    }
    if ($r.Success) {
        Write-Log ("       OK   imported -> " + $r.RootUri)
        $state[$name] = $fp
        $added++
    } else {
        $detail = if ($r.FailedFiles.Count) { "failed files: " + ($r.FailedFiles -join ', ') }
                  elseif ($r.Error) { $r.Error } else { 'unknown error' }
        Write-Log ("       FAIL {0}: {1}" -f $name, $detail)
        $failed++
    }
    Start-Sleep -Seconds 2
}

# Only persist fingerprints for projects that actually imported. A timed-out or
# failed project keeps its old fingerprint so the next run picks it up again.
$state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
Write-Log "=== OV sync done: $added imported, $changed changed, $skipped unchanged, $failed failed ($timedOut timed out) ==="
if ($timedOut -gt 0) { Write-Log "=== $timedOut project(s) will be retried on the next run ===" }
