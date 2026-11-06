# Native Windows source installation. Does not download or publish Vika releases.
[CmdletBinding()]
param([switch]$SkipSetup)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this installer on Windows.' }
$node = (Get-Command node.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$nodeVersion = (& $node --version).TrimStart('v')
if ([int]($nodeVersion.Split('.')[0]) -lt 24) { throw 'Install Node.js 24 LTS first.' }
$source = Split-Path $PSScriptRoot -Parent
$base = Join-Path $env:USERPROFILE '.vika'
$current = Join-Path $base 'current'
if (Test-Path $current) { throw "An installation already exists at $current. Stop it and move its current junction before reinstalling. Configuration is retained." }
$release = Join-Path $base ('releases\windows-source-' + (Get-Date -Format 'yyyyMMddHHmmss'))
New-Item -ItemType Directory -Path $release -Force | Out-Null
# Copy source only; Linux node_modules cannot be reused on Windows.
& robocopy.exe $source $release /E /XD node_modules .git /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Source copy failed: $LASTEXITCODE" }
Push-Location $release
try {
    & $npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed. Check Node version and native dependency installation.' }
    & $node packages/daemon/scripts/check-pi.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Bundled Pi validation failed.' }
} finally { Pop-Location }
# Directory junctions work without Administrator/Developer Mode privileges.
New-Item -ItemType Junction -Path $current -Target $release | Out-Null
$bin = Join-Path $base 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
Copy-Item (Join-Path $release 'scripts\windows-launch.mjs') (Join-Path $bin 'vika-launch.mjs')
# Launch through a Node bootstrap so HOME, PATH and PM2_HOME also exist at login.
$launcher = '@echo off' + "`r`n" + '"' + $node + '" "%~dp0vika-launch.mjs" %*' + "`r`n"
[IO.File]::WriteAllText((Join-Path $bin 'vika.cmd'), $launcher)
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $bin) {
    [Environment]::SetEnvironmentVariable('Path', ($bin + ';' + $userPath), 'User')
}
$env:Path = $bin + ';' + $env:Path
Write-Host 'Installed native Windows source preview. Open a new terminal to use vika.'
Write-Host 'Auto-upgrade is disabled. Slack requires slack.exe on PATH or SLACK_CLI_BIN.'
if (-not $SkipSetup) {
    & $node (Join-Path $bin 'vika-launch.mjs') setup
    if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete; run vika setup to retry.' }
}
