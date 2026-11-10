# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
# Read function definitions only; never run the installer's main entrypoint.
$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install-preview.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($definition in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}
$work = Join-Path ([IO.Path]::GetTempPath()) ('vika-transactions-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
$realMove = ${function:Move-Link}
$realNew = ${function:New-Link}
$UI = 'en'
$Fancy = 0
$passed = 0
function Assert([bool]$value, [string]$message) { if (-not $value) { throw $message } }
function Expect-Throw([scriptblock]$action, [string]$pattern) {
    $caught = $null
    try { & $action } catch { $caught = $_.Exception.Message }
    Assert ([bool]$caught) "Expected failure: $pattern"
    Assert ($caught -match $pattern) "Wrong failure: $caught"
}
try {
    # File pointers on every OS; junctions additionally on real Windows.
    $modes = @($true)
    if ($env:OS -eq 'Windows_NT') { $modes += $false }
    foreach ($asFile in $modes) {
        foreach ($fault in @('none', 'prepare', 'current', 'rollback', 'restore')) {
            $case = Join-Path $work ("mode-$asFile-$fault")
            $old = Join-Path $case 'old'
            $older = Join-Path $case 'older'
            $next = Join-Path $case 'next'
            foreach ($dir in @($old, $older, $next)) { [void][IO.Directory]::CreateDirectory($dir) }
            Set-Content -LiteralPath (Join-Path $old 'sentinel') -Value 'keep'
            $current = Join-Path $case 'current'
            $rollback = Join-Path $case 'rollback'
            & $realNew $current $old -AsFile:$asFile
            & $realNew $rollback $older -AsFile:$asFile
            function New-Link([string]$path, [string]$value, [switch]$AsFile) {
                if ($fault -eq 'prepare' -and $path.StartsWith("$rollback.new-")) { throw 'injected prepare failure' }
                & $realNew $path $value -AsFile:$AsFile
            }
            function Move-Link([string]$from, [string]$to) {
                if (($fault -in @('current', 'restore')) -and $from.StartsWith("$current.new-")) { throw 'injected current failure' }
                if ($fault -eq 'rollback' -and $from.StartsWith("$rollback.new-")) { throw 'injected rollback failure' }
                if ($fault -eq 'restore' -and $from.StartsWith("$current.old-")) { throw 'injected recovery failure' }
                & $realMove $from $to
            }
            if ($fault -eq 'none') {
                Switch-ReleaseLinks $current $rollback $next $old -AsFile:$asFile
                Assert ((Get-Link $current) -eq $next) 'current did not switch'
                Assert ((Get-Link $rollback) -eq $old) 'rollback did not advance'
            } else {
                $pattern = if ($fault -eq 'restore') { 'recovery incomplete' } else { 'injected' }
                Expect-Throw { Switch-ReleaseLinks $current $rollback $next $old -AsFile:$asFile } $pattern
                Assert ((Get-Link $rollback) -eq $older) 'previous rollback was lost'
                if ($fault -eq 'restore') {
                    $backups = @(Get-ChildItem -LiteralPath $case -Filter 'current.old-*' -Force)
                    Assert ($backups.Count -eq 1) 'recovery backup was lost'
                    Assert ((Get-Link $backups[0].FullName) -eq $old) 'backup points to wrong release'
                    & $realMove $backups[0].FullName $current
                } else {
                    Assert ((Get-Link $current) -eq $old) 'old current was not restored'
                }
            }
            Assert ((Get-Content -LiteralPath (Join-Path $old 'sentinel') -Raw).Trim() -eq 'keep') 'release contents changed'
            Assert (@(Get-ChildItem -LiteralPath $case -Filter '*.new-*' -Force).Count -eq 0) 'temporary link was left behind'
            $passed++
        }
    }
    Set-Item Function:Move-Link $realMove
    Set-Item Function:New-Link $realNew

    $releaseRoot = Join-Path $work 'releases'
    [void][IO.Directory]::CreateDirectory($releaseRoot)
    foreach ($version in @('.', '..', '../outside', '..\outside', 'C:\outside', 'v1:ads', 'CON', 'NUL.txt', 'v1.')) {
        Expect-Throw { Release-Path $releaseRoot $version } 'Invalid release version'
        $passed++
    }
    $safe = Release-Path $releaseRoot '0.4.0-windows-preview-abcdef'
    Assert ([IO.Path]::GetDirectoryName($safe) -eq $releaseRoot) 'valid version escaped root'

    # A junction at a release destination must not be reused or traversed.
    if ($env:OS -eq 'Windows_NT') {
        $external = Join-Path $work 'external'
        [void][IO.Directory]::CreateDirectory($external)
        New-Item -ItemType Junction -Path (Join-Path $releaseRoot '1.0.0') -Value $external | Out-Null
        Expect-Throw { Release-Path $releaseRoot '1.0.0' } 'linked release directory'
        Remove-Link (Join-Path $releaseRoot '1.0.0')
        Assert (Test-Path -LiteralPath $external) 'external directory was removed'
        $passed++
    }

    # Same failure code as a previous native command must still fail self-check.
    $checkRoot = Join-Path $work 'check'
    [void][IO.Directory]::CreateDirectory($checkRoot)
    Set-Content -LiteralPath (Join-Path $checkRoot 'vika-preview.ps1') -Value 'param($Action); exit 7'
    $global:LASTEXITCODE = 7
    Expect-Throw { Test-Release $checkRoot } 'exited 7'
    Set-Content -LiteralPath (Join-Path $checkRoot 'vika-preview.ps1') -Value 'param($Action)'
    $global:LASTEXITCODE = 7
    Test-Release $checkRoot
    $passed += 2
    Write-Host "Transaction regression tests passed: $passed (junction coverage requires Windows)."
} finally {
    Set-Item Function:Move-Link $realMove
    Set-Item Function:New-Link $realNew
    # Unlink known junctions before removing test directories, including PS 5.1.
    foreach ($entry in @(Get-ChildItem -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { $_.LinkType })) {
        try { Remove-Link $entry.FullName } catch { }
    }
    Remove-Item -LiteralPath $work -Recurse -Force
}
