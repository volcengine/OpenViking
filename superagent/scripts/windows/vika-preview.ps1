# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
[CmdletBinding()]
param(
    [ValidateSet('check', 'start', 'stop', 'logs')][string]$Action = 'check',
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
$previousPath = $env:PATH
$previousConfig = $env:VIKA_CONFIG
Push-Location $PSScriptRoot
try {
    $env:PATH = "$(Join-Path $PSScriptRoot 'runtime');$(Join-Path $PSScriptRoot 'node_modules\.bin');$previousPath"
    if ($Action -eq 'check') {
        Invoke-BundledNode -Arguments @('--version')
        Invoke-BundledNode -Arguments @('packages/cli/bin/vika.mjs', '--version')
        & (Join-Path $PSScriptRoot 'node_modules\@larksuite\cli\bin\lark-cli.exe') --version
        if ($LASTEXITCODE -ne 0) { throw 'Bundled lark-cli check failed' }
        Invoke-BundledNode -Arguments @('packages/daemon/scripts/check-pi.mjs')
        Invoke-BundledNode -Arguments @('packages/daemon/scripts/check-durable.mjs')
        # Resolve PM2 without starting its daemon during install/CI.
        Invoke-BundledNode -Arguments @('scripts/windows/check-pm2.cjs')
        return
    }
    if (!$env:PM2_HOME) { throw 'Set PM2_HOME explicitly for your test instance before managing apps.' }
    if ($Action -eq 'start') {
        if (!$Config -or ![IO.Path]::IsPathRooted($Config) -or !(Test-Path -LiteralPath $Config -PathType Leaf)) {
            throw 'Pass -Config with an absolute path to your configured Windows manifest.'
        }
        $env:VIKA_CONFIG = $Config
        Invoke-BundledNode -Arguments @($pm2, 'start', 'ecosystem.config.cjs', '--only', 'vika-primary,vika-web')
    } elseif ($Action -eq 'stop') {
        Invoke-BundledNode -Arguments @($pm2, 'stop', 'vika-primary', 'vika-web')
    } else {
        Invoke-BundledNode -Arguments @($pm2, 'logs', 'vika-primary', '--lines', '50', '--nostream')
    }
} finally {
    $env:PATH = $previousPath
    $env:VIKA_CONFIG = $previousConfig
    Pop-Location
}
