# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
# Installs into a NEW directory only. Does not switch an existing release,
# change PATH, write configuration, or start PM2.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$File,
    [Parameter(Mandatory)][string]$Destination
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (![IO.Path]::IsPathRooted($Destination)) { throw 'Destination must be an absolute path' }
$archive = (Resolve-Path -LiteralPath $File).Path
$target = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $target) { throw "Destination already exists: $target" }
$checksumFile = "$archive.sha256"
$checksum = (Get-Content -LiteralPath $checksumFile -Raw).Trim()
if ($checksum -notmatch '^([a-fA-F0-9]{64})\s+(.+)$') { throw 'Invalid checksum file' }
$expected = $Matches[1]
if ($Matches[2] -cne [IO.Path]::GetFileName($archive)) { throw 'Checksum filename mismatch' }
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $expected) { throw 'SHA-256 mismatch' }
$parent = [IO.Path]::GetDirectoryName($target)
[IO.Directory]::CreateDirectory($parent) | Out-Null
$pending = Join-Path $parent ('.vika-pending-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($pending) | Out-Null
try {
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $prefix = $pending + [IO.Path]::DirectorySeparatorChar
        foreach ($entry in $zip.Entries) {
            # Reject traversal, ADS, absolute paths and symlinks before extraction.
            if ($entry.FullName.Contains(':') -or [IO.Path]::IsPathRooted($entry.FullName) -or
                (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Unsafe ZIP entry' }
            $entryPath = [IO.Path]::GetFullPath((Join-Path $pending $entry.FullName))
            if (!$entryPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'ZIP path escapes destination' }
        }
    } finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $pending)
    $metadata = Get-Content -LiteralPath (Join-Path $pending 'windows-preview.json') -Raw | ConvertFrom-Json
    if ($metadata.platform -ne 'win32-x64' -or $metadata.channel -ne 'windows-preview') { throw 'Not a Windows x64 preview' }
    & (Join-Path $pending 'vika-preview.ps1') -Action check
    # Directory.Move refuses an existing destination, including one created concurrently.
    [IO.Directory]::Move($pending, $target)
    Write-Host "Installed preview at $target"
    Write-Host 'Configure your manifest using docs/windows-preview.md before starting.'
} finally {
    if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Recurse -Force }
}
