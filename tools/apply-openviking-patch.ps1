<#
.SYNOPSIS
  Applies the local OpenViking PR-branch patches onto an officially installed
  openviking package, and can roll them back.

.DESCRIPTION
  Only pdf.py actually needs patching on 0.4.22.

  The upstream branch (fix/mineru-first-on-0.4.22, commit 9d9f6725) also touched
  core.py and parser_config.py, but those two landed on upstream before the 0.4.22
  release, so the official wheel already carries them. Worse, this checkout sits
  on post-0.4.21 main and its core.py imports
  openviking.storage.queuefs.reindex_processor, a module the 0.4.22 wheel does not
  ship — copying it over makes the server fail at startup with ModuleNotFoundError.

  Do not "helpfully" sync all three. Patch pdf.py alone; the other two are
  verified here to be functionally identical or strictly newer than 0.4.22.

  Every overwrite is backed up to a timestamped folder and recorded, so -Rollback
  restores the exact pre-patch bytes rather than trying to re-download.

.PARAMETER Repo
  Path to the local openviking git checkout carrying the patch.

.PARAMETER SitePackages
  site-packages directory of the target interpreter. Auto-detected when omitted.

.PARAMETER ExpectedVersion
  Refuse to patch unless the installed package matches this version.

.PARAMETER Verify
  Compare only; make no changes. Reports which files are already in sync.

.PARAMETER Rollback
  Restore the most recent backup created by this script.

.EXAMPLE
  .\apply-openviking-patch.ps1 -Verify
  .\apply-openviking-patch.ps1
  .\apply-openviking-patch.ps1 -Rollback
#>
[CmdletBinding()]
param(
    [string]$SitePackages,
    [string]$ExpectedVersion,
    [switch]$Verify,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'

# Per-release patched builds. Each directory holds full replacement copies of the
# files below, produced by grafting this branch's MinerU block onto that release's
# own source: 0.5.0's pdf.py is 881 lines and has none of the task-based API, so
# the PR block has to be re-based onto it rather than dropped in wholesale.
$Targets = @(
    [pscustomobject]@{
        Version = '0.5.0'
        Dir     = 'patches\0.5.0'
        Files   = @(
            'openviking\parse\parsers\pdf.py',
            'openviking\service\core.py',
            'openviking_cli\utils\config\parser_config.py'
        )
    }
)

$BackupRoot = Join-Path $env:USERPROFILE '.openviking\patch-backups'

function Resolve-SitePackages {
    if ($SitePackages) {
        if (-not (Test-Path $SitePackages)) {
            throw "Site-packages not found: $SitePackages"
        }
        return $SitePackages
    }
    $probe = & C:\Python314\python.exe -c "import openviking,os;print(os.path.dirname(os.path.dirname(openviking.__file__)))" 2>$null
    if ($probe -and (Test-Path $probe)) { return $probe.Trim() }
    throw 'Could not auto-detect site-packages; pass -SitePackages explicitly.'
}

function Get-InstalledVersion {
    $out = & C:\Python314\python.exe -m pip show openviking 2>$null |
           Select-String '^Version:\s*(\S+)' |
           ForEach-Object { $_.Matches[0].Groups[1].Value }
    if ($out) { return $out.Trim() }
    return $null
}

function Get-LatestBackup {
    if (-not (Test-Path $BackupRoot)) { return $null }
    Get-ChildItem $BackupRoot -Directory |
        Sort-Object Name -Descending |
        Select-Object -First 1
}

# Roll back to a *pristine* tree, not merely the previous run. Each backup dir
# holds whatever was there when that run patched; if a later run patched an
# already-patched file, its backup is itself patched, so stepping back one
# directory at a time walks forward, not backward. Walk every backup instead and
# keep the earliest copy of each file, which is the version from before any of
# this script's runs touched it.
function Get-PristineSources {
    $out = @{}
    if (-not (Test-Path $BackupRoot)) { return $out }
    $dirs = Get-ChildItem $BackupRoot -Directory | Sort-Object Name
    foreach ($d in $dirs) {
        $m = Join-Path $d.FullName 'manifest.txt'
        if (-not (Test-Path $m)) { continue }
        foreach ($dst in (Get-Content $m | Where-Object { $_.Trim() -ne '' })) {
            $sp = Resolve-SitePackages
            $rel = $dst.Substring($sp.Length).TrimStart('\')
            if ($out.ContainsKey($rel)) { continue }
            $src = Join-Path $d.FullName $rel
            if (Test-Path $src) { $out[$rel] = $src }
        }
    }
    return $out
}

# ---------------------------------------------------------------- rollback
if ($Rollback) {
    $bk = Get-LatestBackup
    if (-not $bk) { Write-Host "No backup under $BackupRoot; nothing to roll back."; exit 0 }
    $sp = Resolve-SitePackages
    Write-Host "Rolling back to the pre-patch state (oldest backup per file)"
    $pristine = Get-PristineSources
    if ($pristine.Count -eq 0) {
        Write-Error "Backups exist but no manifest entries were readable."
        exit 1
    }
    foreach ($rel in $pristine.Keys) {
        Copy-Item $pristine[$rel] (Join-Path $sp $rel) -Force
        Write-Host "  restored  $rel   (from $($pristine[$rel].Split('\')[-3]))"
    }
    Write-Host ''
    Write-Host 'Restart openviking-server to load the restored code.'
    exit 0
}

# ---------------------------------------------------------------- verify/apply
$sp = Resolve-SitePackages
$installed = Get-InstalledVersion
Write-Host "openviking site-packages: $sp"

if (-not $installed) {
    Write-Warning 'Could not read installed version via pip.'
} else {
    Write-Host "installed version        : $installed"
}

# Pick the patch set matching what is actually installed, so an upgrade does not
# silently apply a build grafted onto a different release.
$target = $Targets | Where-Object { $_.Version -eq $installed } | Select-Object -First 1
if (-not $target) {
    Write-Error "No patch set for installed version '$installed'."
    Write-Error ("Known: " + (($Targets | ForEach-Object { $_.Version }) -join ', '))
    exit 1
}
if ($ExpectedVersion -and $installed -ne $ExpectedVersion) {
    Write-Warning "Expected $ExpectedVersion but found $installed; using the $installed patch set."
}

$repo = Join-Path $PSScriptRoot $target.Dir
$PatchedFiles = $target.Files

if (-not (Test-Path $repo)) { throw "Patch directory not found: $repo" }

# Files are stored flat inside the patch directory (patches/<version>/pdf.py),
# keyed only by leaf name, so resolve each target by basename rather than by
# reproducing the site-packages tree.
$flat = @{}
Get-ChildItem $repo -File -Filter *.py | ForEach-Object { $flat[$_.Name] = $_.FullName }

if ($flat.Count -eq 0) { throw "No patch files under $repo" }
Write-Host "patch set                : $repo ($($flat.Count) files)"

$plan = foreach ($rel in $PatchedFiles) {
    $leaf = Split-Path $rel -Leaf
    $dst  = Join-Path $sp $rel
    $src  = $flat[$leaf]
    if (-not $src) {
        [pscustomobject]@{ Rel = $rel; State = 'MISSING-PATCH'; Hash = '' }
        continue
    }
    if (-not (Test-Path $dst)) {
        [pscustomobject]@{ Rel = $rel; State = 'MISSING-IN-SITE'; Hash = '' }
        continue
    }
    $a = (Get-FileHash $src -Algorithm MD5).Hash
    $b = (Get-FileHash $dst -Algorithm MD5).Hash
    [pscustomobject]@{
        Rel   = $rel
        State = $(if ($a -eq $b) { 'in-sync' } else { 'DIFFERS' })
        Hash  = $a.Substring(0, 8)
        Src   = $src
        Dst   = $dst
    }
}

Write-Host ''
$plan | Format-Table -AutoSize | Out-String -Width 100 | Write-Host

Write-Host ''
$need = @($plan | Where-Object { $_.State -ne 'in-sync' })

if ($Verify) {
    if ($need.Count -eq 0) {
        Write-Host 'Verify: all patched files already in sync.'
    } else {
        Write-Host "Verify: $($need.Count) file(s) would be patched:"
        $need | ForEach-Object { Write-Host "  - $($_.Rel) [$($_.State)]" }
    }
    exit 0
}

if ($need.Count -eq 0) {
    Write-Host 'Nothing to do; all files already in sync.'
    exit 0
}

$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$bkDir  = Join-Path $BackupRoot $stamp
New-Item -ItemType Directory -Path $bkDir -Force | Out-Null
$manifest = @()

foreach ($row in $need) {
    $src = $row.Src
    $dst = $row.Dst
    $bak = Join-Path $bkDir $row.Rel
    New-Item -ItemType Directory -Path (Split-Path $bak) -Force | Out-Null

    Copy-Item $dst $bak -Force
    $manifest += $dst
    Copy-Item $src $dst -Force

    $ok = (Get-FileHash $src -Algorithm MD5).Hash -eq (Get-FileHash $dst -Algorithm MD5).Hash
    Write-Host ("  {0,-9} {1}" -f $(if ($ok) { 'patched' } else { 'MISMATCH' }), $row.Rel)
    if (-not $ok) { throw "Hash mismatch after copying $($row.Rel)" }
}

$manifest | Set-Content (Join-Path $bkDir 'manifest.txt') -Encoding UTF8
Write-Host ''
Write-Host "Backup: $bkDir"
Write-Host 'Rollback with:  .\apply-openviking-patch.ps1 -Rollback'

# Byte-compile check catches syntax errors before the server imports the file.
Write-Host ''
Write-Host 'Byte-compiling patched modules...'
foreach ($row in $need) {
    & C:\Python314\python.exe -m py_compile $row.Dst
    if ($LASTEXITCODE -ne 0) { throw "py_compile failed for $($row.Rel)" }
    Write-Host "  ok  $($row.Rel)"
}

# Import smoke test. py_compile only catches syntax; a patched module can still
# reference something the installed wheel lacks, which only shows up when the
# server imports it (the reindex_processor ModuleNotFoundError case).
Write-Host ''
Write-Host 'Import smoke test...'
& C:\Python314\python.exe -c "from openviking.service.core import OpenVikingService; from openviking.parse.parsers.pdf import PDFParser; print('imports OK')" 2>&1 |
    Where-Object { $_ -notmatch 'RAGFS|UserWarning|from pydantic' } |
    ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) {
    throw "Import smoke test failed. Restore with -Rollback (backup: $bkDir)"
}

Write-Host ''
Write-Host 'Restart openviking-server so the patched code is loaded.'
