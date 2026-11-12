# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
[CmdletBinding()]
param(
    [ValidateSet('check', 'start', 'restart', 'stop', 'logs')][string]$Action = 'check',
    [string]$Config
)
$ErrorActionPreference = 'Stop'
$node = Join-Path $PSScriptRoot 'runtime\node.exe'
$pm2 = Join-Path $PSScriptRoot 'node_modules\pm2\bin\pm2'
function Invoke-BundledNode {
    param([string[]]$Arguments)
    & $node @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Bundled Node command failed (exit $LASTEXITCODE)" }
}
# The preview's apps, plus the names a pre-release build used, so an old copy
# never keeps running next to this one. A missing app is not an error.
$apps = @('superagent-primary', 'superagent-web')
$legacyApps = @('vika-primary', 'vika-web')
function Stop-Apps([string]$verb) {
    foreach ($app in $apps + $legacyApps) {
        & $node $pm2 $verb $app 2>$null | Out-Null
    }
    $global:LASTEXITCODE = 0
}
$previousPath = $env:PATH
$previousConfig = $env:OV_SUPERAGENT_CONFIG
Push-Location $PSScriptRoot
try {
    $env:PATH = "$(Join-Path $PSScriptRoot 'runtime');$(Join-Path $PSScriptRoot 'node_modules\.bin');$previousPath"
    if ($Action -eq 'check') {
        Invoke-BundledNode -Arguments @('--version')
        Invoke-BundledNode -Arguments @('packages/cli/bin/ov-superagent.mjs', '--version')
        & (Join-Path $PSScriptRoot 'node_modules\@larksuite\cli\bin\lark-cli.exe') --version
        if ($LASTEXITCODE -ne 0) { throw 'Bundled lark-cli check failed' }
        Invoke-BundledNode -Arguments @('packages/daemon/scripts/check-pi.mjs')
        Invoke-BundledNode -Arguments @('packages/daemon/scripts/check-durable.mjs')
        # Resolve PM2 without starting its daemon during install/CI.
        Invoke-BundledNode -Arguments @('scripts/windows/check-pm2.cjs')
        return
    }
    if (!$env:PM2_HOME) { throw 'Set PM2_HOME explicitly for your test instance before managing apps.' }
    if ($Action -eq 'start' -or $Action -eq 'restart') {
        if (!$Config -or ![IO.Path]::IsPathRooted($Config) -or !(Test-Path -LiteralPath $Config -PathType Leaf)) {
            throw 'Pass -Config with an absolute path to your configured Windows manifest.'
        }
        $env:OV_SUPERAGENT_CONFIG = $Config
        # Stop first, then start: a start never runs beside an old copy.
        Stop-Apps 'delete'
        Invoke-BundledNode -Arguments @($pm2, 'start', 'ecosystem.config.cjs', '--only', ($apps -join ','))
    } elseif ($Action -eq 'stop') {
        Stop-Apps 'stop'
    } else {
        Invoke-BundledNode -Arguments @($pm2, 'logs', 'superagent-primary', '--lines', '50', '--nostream')
    }
} finally {
    $env:PATH = $previousPath
    $env:OV_SUPERAGENT_CONFIG = $previousConfig
    Pop-Location
}
