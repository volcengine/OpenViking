# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$installer = Join-Path $PSScriptRoot 'install-preview.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ('vika-installer-test-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($work) | Out-Null
function New-Fixture {
    param([string]$Name, [string]$Check = 'param($Action)', [switch]$Traversal, [string]$Platform = 'win32-x64')
    $file = Join-Path $work "$Name.zip"
    $zip = [IO.Compression.ZipFile]::Open($file, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entries = @{
            'windows-preview.json' = "{`"platform`":`"$Platform`",`"channel`":`"windows-preview`"}"
            'vika-preview.ps1' = $Check
        }
        if ($Traversal) { $entries['../escaped.txt'] = 'must not escape' }
        foreach ($name in $entries.Keys) {
            $writer = New-Object IO.StreamWriter($zip.CreateEntry($name).Open())
            try { $writer.Write($entries[$name]) } finally { $writer.Dispose() }
        }
    } finally { $zip.Dispose() }
    Set-Content -LiteralPath "$file.sha256" -Value "$((Get-FileHash $file -Algorithm SHA256).Hash)  $([IO.Path]::GetFileName($file))" -Encoding ascii
    return $file
}
function Expect-Failure {
    param([scriptblock]$Operation, [string]$Pattern)
    try { & $Operation } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw }
        return
    }
    throw "Expected failure: $Pattern"
}
try {
    $valid = New-Fixture 'valid'
    $destination = Join-Path $work 'relocated package'
    & $installer -File $valid -Destination $destination
    if (!(Test-Path "$destination/windows-preview.json")) { throw 'Install did not finish' }
    Expect-Failure { & $installer -File $valid -Destination $destination } 'already exists'
    $bad = New-Fixture 'bad-hash'
    Add-Content -LiteralPath $bad -Value 'corruption'
    Expect-Failure { & $installer -File $bad -Destination "$work/bad" } 'SHA-256 mismatch'
    $escape = New-Fixture 'traversal' -Traversal
    Expect-Failure { & $installer -File $escape -Destination "$work/escape" } 'escapes destination'
    if (Test-Path "$work/escaped.txt") { throw 'ZIP escaped staging directory' }
    $wrong = New-Fixture 'wrong-platform' -Platform 'linux-x64'
    Expect-Failure { & $installer -File $wrong -Destination "$work/wrong" } 'Not a Windows'
    $broken = New-Fixture 'broken-runtime' -Check "throw 'runtime check failed'"
    Expect-Failure { & $installer -File $broken -Destination "$work/broken" } 'runtime check failed'
    foreach ($name in @('bad', 'escape', 'wrong', 'broken')) {
        if (Test-Path "$work/$name") { throw "Failed install created destination: $name" }
    }
    if (Get-ChildItem -LiteralPath $work -Filter '.vika-pending-*' -Force) { throw 'Staging directories were left behind' }
    Write-Host 'Installer tests passed: install, existing destination, checksum, traversal, platform, runtime failure, cleanup.'
} finally { Remove-Item -LiteralPath $work -Recurse -Force }
