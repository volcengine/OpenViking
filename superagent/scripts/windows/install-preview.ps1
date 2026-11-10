# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

# Vika installer for Windows x64. The steps and the words are scripts/install.sh's:
# verify, unpack, self-check, install, then say what comes next.
#
#   .\install-preview.ps1 -File vika-<version>-win32-x64.zip
#   .\install-preview.ps1 -File <zip> -Destination D:\vika-test   # isolated copy
#
# The default install uses the POSIX layout under ~\.vika: releases\<version>,
# the `current` link, the `rollback` link, bin\vika.cmd, and ~\.vika\bin on the
# user PATH. -Destination instead installs exactly into that new directory and
# changes nothing else, which is what side-by-side testing and CI use.
#
# With no -File, download from the Windows preview channel (checksum required).
#   irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1 | iex
# Options: -File <zip>, -Version <version>, -DownloadBase <url>,
# -Destination <dir>, -Lang <zh|en>, -NoSetup.
# VIKA_VERSION and VIKA_DOWNLOAD_BASE also work with irm | iex.
# Vika's home is HOME when it is set, otherwise the profile, as vika itself
# resolves it; bin\vika.cmd finds its .vika directory instead of guessing one.
# Environment: VIKA_LANG picks the language, NO_COLOR turns the colours off,
# VIKA_HOME overrides the installer destination for tests only.
[CmdletBinding()]
param(
    [string]$File,
    [string]$Version = $env:VIKA_VERSION,
    [string]$DownloadBase = $env:VIKA_DOWNLOAD_BASE,
    [string]$Destination,
    [ValidateSet('', 'zh', 'en')][string]$Lang,
    [switch]$NoSetup
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Platform = 'win32-x64'
$Isolated = [bool]$Destination

# ---- Home, language, colour ------------------------------------------------
# Match Vika's platform homeDir(): HOME first, then USERPROFILE on Windows.
# Node's os.homedir() itself uses USERPROFILE on Windows, not HOME.
# VIKA_HOME is an installer fixture override; it is not a runtime home setting.
$HomeBase = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
$HomeBase = [IO.Path]::GetFullPath($HomeBase)
$VikaHome = if ($env:VIKA_HOME) { $env:VIKA_HOME } else { Join-Path $HomeBase '.vika' }
$VikaHome = [IO.Path]::GetFullPath($VikaHome)
# A .ps1 run with & shares this process's environment, so the terminal that runs
# the installer can use vika as soon as it returns. VIKA_HOME keeps the tests off
# the real profile's PATH; the persistent write is Windows-only.
$SessionPath = -not $Isolated
$PersistPath = -not $Isolated -and -not $env:VIKA_HOME -and $env:OS -eq 'Windows_NT'

# Chinese or English, as the vika command picks it (packages/cli/src/i18n.mjs):
# -Lang or VIKA_LANG, else what setup saved, else Chinese.
$UI = $Lang
if (-not $UI) { $UI = $env:VIKA_LANG }
if ($UI -notin @('zh', 'en')) { $UI = '' }
if (-not $UI) {
    $saved = Join-Path $VikaHome 'language'
    if (Test-Path -LiteralPath $saved) {
        $text = Get-Content -LiteralPath $saved -Raw -ErrorAction SilentlyContinue
        if ($text) { $text = $text.Trim() }
        if ($text -in @('zh', 'en')) { $UI = $text }
    }
}
if (-not $UI) { $UI = 'zh' }
# M <Chinese> <English> prints the one in use.
function M([string]$zh, [string]$en) { if ($UI -eq 'en') { return $en } return $zh }

$ESC = [char]27
$Check = [char]0x2714
$Cross = [char]0x2716
$Arrow = [char]0x203A
$Bang = [char]0x26A0

# Colour and motion only on a terminal; piped output stays plain lines.
$Fancy = 0
if (-not $env:NO_COLOR -and -not [Console]::IsOutputRedirected -and -not [Console]::IsErrorRedirected) {
    try {
        if (-not ('VikaConsole.Api' -as [type])) {
            Add-Type -Namespace VikaConsole -Name Api -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern IntPtr GetStdHandle(int handle);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleMode(IntPtr handle, uint mode);
'@
        }
        $handle = [VikaConsole.Api]::GetStdHandle(-11)
        $mode = [uint32]0
        if ([VikaConsole.Api]::GetConsoleMode($handle, [ref]$mode)) {
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING: the colours install.sh uses too.
            $virtual = $mode -bor 0x0004
            if ([VikaConsole.Api]::SetConsoleMode($handle, $virtual)) { $Fancy = 1 }
        }
    } catch { $Fancy = 0 }
}
if ($Fancy) {
    $RESET = "$ESC[0m"; $BOLD = "$ESC[1m"; $DIM = "$ESC[2m"
    $PINK = "$ESC[38;5;213m"; $VIOLET = "$ESC[38;5;177m"; $LILAC = "$ESC[38;5;141m"; $SKY = "$ESC[38;5;117m"
    $GRAY = "$ESC[38;5;244m"; $GREEN = "$ESC[38;5;114m"; $YELLOW = "$ESC[38;5;221m"; $RED = "$ESC[38;5;203m"
} else {
    $RESET = ''; $BOLD = ''; $DIM = ''; $PINK = ''; $VIOLET = ''; $LILAC = ''; $SKY = ''
    $GRAY = ''; $GREEN = ''; $YELLOW = ''; $RED = ''
}
$PreviousEncoding = $null
if ($Fancy) {
    try { $PreviousEncoding = [Console]::OutputEncoding; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { $PreviousEncoding = $null }
}

# ---- Output ---------------------------------------------------------------
# Everything goes to stderr, as install.sh does, so stdout stays free.
# Get-FileHash is missing when Windows PowerShell 5.1 starts from pwsh with
# PowerShell 7's PSModulePath, so hash through .NET.
function Get-Sha256([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    try { return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stream))).Replace('-', '') }
    finally { $stream.Dispose() }
}
function Say([string]$text) { [Console]::Error.WriteLine($text) }
function Ok([string]$text) { if ($Fancy) { Say "  ${GREEN}$Check${RESET} $text" } else { Say $text } }
function Note([string]$text) { if ($Fancy) { Say "  ${GRAY}$Arrow${RESET} $text" } else { Say $text } }
function Warn([string]$text) { if ($Fancy) { Say "  ${YELLOW}$Bang${RESET} $text" } else { Say $text } }

# Mutable state also works when invoked as a scriptblock (irm with options).
$InstallerState = @{}
$InstallerState.DownloadDir = ''
$InstallerState.Stage = ''
$InstallerState.Lock = ''
$InstallerState.Finished = $false
function Cleanup {
    if ($InstallerState.Finished) { return }
    $InstallerState.Finished = $true
    if ($Fancy) { [Console]::Error.Write("$ESC[?25h") }
    if ($PreviousEncoding) { try { [Console]::OutputEncoding = $PreviousEncoding } catch { } }
    if ($InstallerState.DownloadDir -and (Test-Path -LiteralPath $InstallerState.DownloadDir)) { Remove-Item -LiteralPath $InstallerState.DownloadDir -Recurse -Force -ErrorAction SilentlyContinue }
    if ($InstallerState.Stage -and (Test-Path -LiteralPath $InstallerState.Stage)) { Remove-Item -LiteralPath $InstallerState.Stage -Recurse -Force -ErrorAction SilentlyContinue }
    if ($InstallerState.Lock -and [IO.Directory]::Exists($InstallerState.Lock)) { try { [IO.Directory]::Delete($InstallerState.Lock) } catch { } }
}
function Die([string]$message) {
    $failed = M 'vika 安装失败：' 'vika install failed: '
    if ($Fancy) { Say "  ${RED}$Cross${RESET} $failed$message" } else { Say "$failed$message" }
    Cleanup
    throw "$failed$message"
}

# ~ for the home directory, so paths stay short.
function Short([string]$path) {
    if ($path -and $path.StartsWith($HomeBase, [StringComparison]::OrdinalIgnoreCase)) { return '~' + $path.Substring($HomeBase.Length) }
    return $path
}

function Header([string]$what) {
    if ($Fancy) {
        Say ''
        Say "  ${BOLD}${PINK}V${VIOLET}I${LILAC}K${SKY}A${RESET}  ${GRAY}$(M '安装' 'Installing') $what · $Platform${RESET}"
        Say ''
    } else {
        Say (M "安装 Vika $what（$Platform）" "Installing Vika $what ($Platform)")
    }
}

# ---- Spinner --------------------------------------------------------------
$SpinFrames = @([char]0x280B, [char]0x2819, [char]0x2839, [char]0x2838, [char]0x283C, [char]0x2834, [char]0x2826, [char]0x2827, [char]0x2807, [char]0x280F)
$InstallerState.SpinShown = $false
function Spin([string]$label, [int]$step = 1) {
    if (-not $Fancy) { return }
    if (-not $InstallerState.SpinShown) { [Console]::Error.Write("$ESC[?25l"); $InstallerState.SpinShown = $true }
    $frame = $SpinFrames[$step % $SpinFrames.Count]
    [Console]::Error.Write("`r$ESC[2K  $VIOLET$frame$RESET $label")
}
function SpinDone {
    if (-not $Fancy -or -not $InstallerState.SpinShown) { return }
    [Console]::Error.Write("`r$ESC[2K$ESC[?25h")
    $InstallerState.SpinShown = $false
}

# ---- Files ----------------------------------------------------------------
# Runs a command without letting its stderr become a terminating error.
function Invoke-Native([string]$exe, [string[]]$arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $exe @arguments 2>&1
        return [pscustomobject]@{ Code = $LASTEXITCODE; Output = @($out) }
    } catch {
        return [pscustomobject]@{ Code = 1; Output = @($_.Exception.Message) }
    } finally { $ErrorActionPreference = $previous }
}

# The release runs its own Node: runtime\node.exe in a Windows package,
# node/bin/node in a POSIX-shaped one.
function Get-BundledNode([string]$root) {
    foreach ($relative in @(@('runtime', 'node.exe'), @('node', 'bin', 'node.exe'), @('node', 'bin', 'node'), @('runtime', 'node'))) {
        $candidate = [IO.Path]::Combine([string[]](@($root) + $relative))
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

# What the package declares about itself.
function Read-Metadata([string]$root) {
    $file = Join-Path $root 'windows-preview.json'
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    try { return Get-Content -LiteralPath $file -Raw | ConvertFrom-Json } catch { return $null }
}

# Directory.Move refuses an existing destination, including one created
# concurrently. Freshly extracted executables can stay briefly locked (antivirus
# scan, the check process exiting), so retry before giving up.
function Move-Into([string]$from, [string]$to) {
    for ($attempt = 1; ; $attempt++) {
        try { [IO.Directory]::Move($from, $to); return }
        catch [System.UnauthorizedAccessException], [System.IO.IOException] {
            if ((Test-Path -LiteralPath $to) -or $attempt -ge 20) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
}

# A directory link, or a file holding the path when links are not available.
# New-Item can report success without creating anything on a volume that has no
# junctions, so the link is checked before it is trusted.
function New-Link([string]$path, [string]$value, [switch]$AsFile) {
    if (Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue) { throw "Link already exists: $path" }
    if (-not $AsFile) {
        try {
            New-Item -ItemType Junction -Path $path -Value $value -ErrorAction Stop | Out-Null
            if ((Get-Link $path) -eq [IO.Path]::GetFullPath($value)) { return }
        } catch { }
        # Remove only our partially created link, never a real directory.
        Remove-Link $path
    }
    Set-Content -LiteralPath $path -Value $value
    if ((Get-Link $path) -ne [IO.Path]::GetFullPath($value)) { throw "Cannot verify link: $path" }
}
function Move-Link([string]$from, [string]$to) {
    $item = Get-Item -LiteralPath $from -Force -ErrorAction Stop
    if ($item.PSIsContainer) { [IO.Directory]::Move($from, $to) }
    else { [IO.File]::Move($from, $to) }
}
# Prepare all links before changing either public name. Renames are individually
# atomic, not the whole transaction. Preserve backups if recovery itself fails.
function Switch-ReleaseLinks([string]$current, [string]$rollback, [string]$value, [string]$previous, [switch]$AsFile) {
    $id = [guid]::NewGuid().ToString('N')
    $changes = @([pscustomobject]@{ Path=$current; Value=$value; New="$current.new-$id"; Backup="$current.old-$id"; Saved=$false; Installed=$false })
    if ($previous) {
        $changes += [pscustomobject]@{ Path=$rollback; Value=$previous; New="$rollback.new-$id"; Backup="$rollback.old-$id"; Saved=$false; Installed=$false }
    }
    try {
        foreach ($change in $changes) {
            $existing = Get-Item -LiteralPath $change.Path -Force -ErrorAction SilentlyContinue
            if ($existing -and $existing.PSIsContainer -and -not $existing.LinkType) {
                throw "Refusing to replace a real directory: $($change.Path)"
            }
            New-Link $change.New $change.Value -AsFile:$AsFile
        }
        foreach ($change in $changes) {
            if (Get-Item -LiteralPath $change.Path -Force -ErrorAction SilentlyContinue) {
                Move-Link $change.Path $change.Backup
                $change.Saved = $true
            }
            Move-Link $change.New $change.Path
            $change.Installed = $true
        }
    } catch {
        $failure = $_.Exception.Message
        $recovery = @()
        for ($i = $changes.Count - 1; $i -ge 0; $i--) {
            $change = $changes[$i]
            try {
                if ($change.Installed) { Remove-Link $change.Path }
                if ($change.Saved) { Move-Link $change.Backup $change.Path }
            } catch { $recovery += "Restore $($change.Backup) to $($change.Path): $($_.Exception.Message)" }
        }
        if ($recovery.Count) { throw "$failure; recovery incomplete; preserve backups: $($recovery -join '; ')" }
        throw $failure
    } finally {
        foreach ($change in $changes) {
            try { Remove-Link $change.New } catch { Warn "Temporary link retained: $($change.New)" }
        }
    }
    foreach ($change in $changes) {
        try { Remove-Link $change.Backup } catch { Warn "Previous link retained: $($change.Backup)" }
    }
}
function Get-Link([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.LinkType) {
        $value = @($item.Target)[0]
        if ($value) { return [IO.Path]::GetFullPath($value) }
    }
    if ($item.PSIsContainer) { return $item.FullName }
    $text = Get-Content -LiteralPath $path -Raw
    if ($text) { return $text.Trim() }
    return $null
}
# Remove-Item -Recurse follows a directory link and deletes its target, so a
# link goes through Directory::Delete; real directories are never removed here.
function Remove-Link([string]$path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return }
    if ($item.LinkType) {
        if ($item.PSIsContainer) { [IO.Directory]::Delete($path) }
        else { [IO.File]::Delete($path) }
        return
    }
    if ($item.PSIsContainer) { throw "Refusing to remove a real directory: $path" }
    [IO.File]::Delete($path)
}

function Assert-Version([string]$version) {
    if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]*$' -or $version.EndsWith('.') -or
        $version -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
        throw "Invalid release version: $version"
    }
}
function Release-Path([string]$root, [string]$version) {
    Assert-Version $version
    $root = [IO.Path]::GetFullPath($root)
    $candidate = [IO.Path]::GetFullPath((Join-Path $root $version))
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($candidate), $root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release escapes releases directory: $version"
    }
    $existing = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    if ($existing -and (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Refusing a linked release directory: $candidate"
    }
    return $candidate
}

# Extracts the whole package, rejecting traversal, ADS, absolute paths and
# symlinks before anything is written.
function Expand-Package([string]$archivePath, [string]$stageDir) {
    $label = M '解压' 'Unpacking'
    $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $prefix = $stageDir + [IO.Path]::DirectorySeparatorChar
        $entries = $zip.Entries
        $total = $entries.Count
        $index = 0
        foreach ($entry in $entries) {
            $index++
            if ($entry.FullName.Contains(':') -or [IO.Path]::IsPathRooted($entry.FullName) -or
                (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
                throw (M '安装包里有不安全的条目' 'Unsafe ZIP entry')
            }
            $entryPath = [IO.Path]::GetFullPath((Join-Path $stageDir $entry.FullName))
            if (-not $entryPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw (M '安装包里的路径跑到了目标目录外面' 'ZIP path escapes destination')
            }
            if ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\') -or -not $entry.Name) {
                [void][IO.Directory]::CreateDirectory($entryPath)
            } else {
                [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($entryPath))
                $source = $entry.Open()
                try {
                    $target = [IO.File]::Create($entryPath)
                    try { $source.CopyTo($target) } finally { $target.Dispose() }
                } finally { $source.Dispose() }
            }
            if (($index % 20) -eq 0 -or $index -eq $total) { Spin "$label  $index / $total" $index }
        }
    } finally { $zip.Dispose() }
    SpinDone
}

# The package's own runtime must start before anything points at it: a CPU or
# system-library mismatch surfaces here and an existing install is untouched.
function Test-Release([string]$root) {
    $preview = Join-Path $root 'vika-preview.ps1'
    if (Test-Path -LiteralPath $preview) {
        $global:LASTEXITCODE = 0
        # irm | iex works under the default Windows execution policy too.
        # Keep PSScriptRoot intact in the package check, without changing the
        # caller's policy or requiring a globally installed PowerShell 7.
        $hostExe = (Get-Process -Id $PID).Path
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $preview -Action check
        if ($LASTEXITCODE -ne 0) {
            throw "vika-preview.ps1 -Action check exited $LASTEXITCODE"
        }
        return
    }
    $node = Get-BundledNode $root
    if (-not $node) { throw (M '安装包里没有自带的 Node' 'the package has no bundled Node') }
    $ran = Invoke-Native $node @('--version')
    if ($ran.Code -ne 0) { throw ([string]::Join(' ', @($ran.Output))) }
    $ran = Invoke-Native $node @([IO.Path]::Combine($root, 'packages', 'cli', 'bin', 'vika.mjs'), '--version')
    if ($ran.Code -ne 0) { throw ([string]::Join(' ', @($ran.Output))) }
}

# When setup is skipped, check the existing OpenViking connection. This package
# does not carry OpenViking, so report a failure without aborting installation.
function Test-OpenViking([string]$root) {
    $node = Get-BundledNode $root
    $script = [IO.Path]::Combine($root, 'packages', 'cli', 'src', 'check-ov.mjs')
    if (-not $node -or -not (Test-Path -LiteralPath $script)) { return }
    $previousLang = $env:VIKA_LANG
    $env:VIKA_LANG = $UI
    try { $result = Invoke-Native $node @($script) }
    finally {
        if ($previousLang) { $env:VIKA_LANG = $previousLang } else { Remove-Item Env:\VIKA_LANG -ErrorAction SilentlyContinue }
    }
    if ($result.Code -eq 0) {
        Ok (M 'OpenViking 已就绪' 'OpenViking is ready')
        return
    }
    Warn (M 'OpenViking 检查未通过：装完按 docs\windows-preview.md 配置连接' 'OpenViking check failed: connect a service as docs\windows-preview.md describes')
    $detail = [string]::Join(' ', @($result.Output | Where-Object { $_ }))
    if ($detail) { Note $detail }
}

# Never traverse reparse points when comparing an already installed release.
function Release-Files([string]$root) {
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (-not $rootItem.PSIsContainer -or ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Not a plain release directory: $root"
    }
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($root)
    while ($stack.Count) {
        $directory = $stack.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked release entry: $($item.FullName)" }
            if ($item.PSIsContainer) { $stack.Push($item.FullName) }
            else { $item.FullName }
        }
    }
}
function Assert-SameRelease([string]$stageDir, [string]$existing) {
    $expected = @(Release-Files $stageDir)
    $actual = @(Release-Files $existing)
    if ($expected.Count -ne $actual.Count) { throw "Release conflict; existing directory retained: $existing" }
    foreach ($file in $expected) {
        $relative = $file.Substring($stageDir.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        $other = Join-Path $existing $relative
        if (-not (Test-Path -LiteralPath $other -PathType Leaf) -or
            (Get-Sha256 $file) -ne (Get-Sha256 $other)) {
            throw "Release conflict; existing directory retained: $existing"
        }
    }
}

# ---- Install --------------------------------------------------------------
function Install-Release([string]$stageDir, [string]$version) {
    $releasesRoot = Join-Path $VikaHome 'releases'
    [void][IO.Directory]::CreateDirectory($releasesRoot)
    $place = Release-Path $releasesRoot $version
    $current = Join-Path $VikaHome 'current'
    $previous = Get-Link $current
    if (Test-Path -LiteralPath $place) {
        # Compare complete file contents, not merely an untrusted version string.
        # Refuse any links in an existing tree before descending into it.
        Assert-SameRelease $stageDir $place
        Remove-Item -LiteralPath $stageDir -Recurse -Force
        $InstallerState.Stage = ''
        if ($previous -and ([IO.Path]::GetFullPath($previous) -eq $place)) {
            Ok (M "已经是 Vika $version" "Vika $version is installed already")
            return [pscustomobject]@{ Changed = $false; Previous = $previous }
        }
    } else {
        Move-Into $stageDir $place
        $InstallerState.Stage = ''
    }
    Switch-ReleaseLinks $current (Join-Path $VikaHome 'rollback') $place $previous
    Ok ("$(M '安装' 'Install ')  ${GRAY}Vika $version → $(Short $place)${RESET}")
    return [pscustomobject]@{ Changed = $true; Previous = $previous }
}

# `current` moves on upgrade; bin\vika.cmd does not. The batch file finds the
# .vika directory from its own location, so no HOME or USERPROFILE is guessed
# (a machine may have HOME pointing somewhere else) and the file stays ASCII
# whatever the account is called.
function Write-Launcher([string]$nodeRelative, [string]$mjsRelative) {
    $binDir = Join-Path $VikaHome 'bin'
    [void][IO.Directory]::CreateDirectory($binDir)
    $text = @(
        '@echo off',
        'rem Always the installed release: `current` moves on upgrade, this file does not.',
        'setlocal',
        'pushd "%~dp0.." || exit /b 1',
        'set "VIKA_HOME=%CD%"',
        'popd',
        'set "RELEASE=%VIKA_HOME%\current"',
        'if not exist "%RELEASE%\" if exist "%VIKA_HOME%\current" set /p RELEASE=<"%VIKA_HOME%\current"',
        '"%RELEASE%\__NODE__" "%RELEASE%\__MJS__" %*'
    ) -join "`r`n"
    $text = $text.Replace('__NODE__', $nodeRelative).Replace('__MJS__', $mjsRelative)
    [IO.File]::WriteAllText((Join-Path $binDir 'vika.cmd'), $text + "`r`n")
    return $binDir
}

function Add-ToPath([string]$binDir) {
    $separator = [IO.Path]::PathSeparator
    # This terminal first: the installer usually runs inside the shell the user
    # typed in, and a child process cannot change its parent's PATH.
    $parts = @($env:PATH -split [regex]::Escape($separator) | Where-Object { $_ })
    if ($parts -notcontains $binDir) { $env:PATH = (@($binDir) + $parts) -join $separator }
    if (-not $PersistPath) { return }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $persistent = @($userPath -split [regex]::Escape($separator) | Where-Object { $_ })
    if ($persistent -contains $binDir) { return }
    try {
        [Environment]::SetEnvironmentVariable('Path', (@($binDir) + $persistent) -join $separator, 'User')
        Ok ("$(M '命令' 'Command ')  ${GRAY}$(M "$(Short $binDir) 已加进 PATH：这个终端和新开的终端都能直接用 vika" "$(Short $binDir) is on the PATH: this terminal and new ones know vika")${RESET}")
    } catch {
        Warn (M "写不了用户 PATH；Vika 已安装完成，这个终端里可以直接用 vika。新终端要直接用，请手动加入：$binDir" "Could not write the user PATH; Vika is installed and this terminal knows vika. For new terminals, add this yourself: $binDir")
    }
}

# Resolve a version once, then use its versioned objects, never the mutable ZIP
# alias. A missing checksum is fatal for downloads, optional only for local files.
function Download-Package {
    if (-not $DownloadBase) { $DownloadBase = '__VIKA_DOWNLOAD_BASE__' }
    if ($DownloadBase.StartsWith('__VIKA_')) { $DownloadBase = 'https://ovrelease.tos-cn-beijing.volces.com/vika' }
    $DownloadBase = $DownloadBase.TrimEnd('/')
    $uri = [uri]$DownloadBase
    if (-not $uri.IsAbsoluteUri -or ($uri.Scheme -ne 'https' -and -not ($uri.Scheme -eq 'http' -and $uri.IsLoopback))) {
        throw 'DownloadBase must use HTTPS (HTTP is allowed only for loopback tests)'
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    if (-not $Version) { $Version = ([string](Invoke-RestMethod -Uri "$DownloadBase/windows-preview/latest/version")).Trim() }
    Assert-Version $Version
    $InstallerState.RequestedVersion = $Version
    $name = "vika-$Version-win32-x64.zip"
    $base = "$DownloadBase/windows-preview/$Version"
    $InstallerState.DownloadDir = Join-Path ([IO.Path]::GetTempPath()) ('vika-download-' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($InstallerState.DownloadDir)
    $target = Join-Path $InstallerState.DownloadDir $name
    Note (M "下载 Vika $Version" "Downloading Vika $Version")
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$name.sha256" -OutFile "$target.sha256"
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile $target
    return $target
}

# ---- Main ----------------------------------------------------------------
try {
if (-not (Test-Path -LiteralPath $HomeBase -PathType Container)) {
    Die (M "家目录不存在：$HomeBase（HOME 和 USERPROFILE 都不是一个目录）" "the home directory does not exist: $HomeBase (HOME and USERPROFILE point nowhere)")
}
$InstallerState.RequestedVersion = $null
if ($File -and $Version) { Die 'Use either -File or -Version, not both' }
if (-not $File) { $File = Download-Package }
if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { Die (M "找不到 $File" "$File is not there") }
$archive = (Resolve-Path -LiteralPath $File).Path
$archiveName = [IO.Path]::GetFileName($archive)

$Arch = $env:PROCESSOR_ARCHITECTURE
if ($Arch -and $Arch -ne 'AMD64') {
    Die (M "不支持 CPU 架构 $Arch；Windows 包只有 x64" "the $Arch CPU architecture is not supported; the Windows package is x64 only")
}

$Place = $null
if ($Isolated) {
    if (-not [IO.Path]::IsPathRooted($Destination)) { Die (M 'Destination 需要绝对路径' 'Destination must be an absolute path') }
    $Place = [IO.Path]::GetFullPath($Destination)
    if (Test-Path -LiteralPath $Place) { Die (M "目标目录已存在：$Place" "Destination already exists: $Place") }
}

Header $archiveName

# The checksum is optional next to a local package, as it is for install.sh.
$checksumFile = "$archive.sha256"
if (Test-Path -LiteralPath $checksumFile) {
    $line = (Get-Content -LiteralPath $checksumFile -Raw).Trim()
    if ($line -match '^([a-fA-F0-9]{64})(\s+(\S+))?$') {
        $expected = $Matches[1]
        $named = $Matches[3]
        if ($named -and ($named -cne $archiveName)) {
            Die (M "校验文件对不上：写的是 $named，不是 $archiveName" "the checksum file names $named, not $archiveName")
        }
        if ((Get-Sha256 $archive) -ine $expected) {
            Die (M '安装包校验失败（SHA-256 不一致）' 'the package failed verification (SHA-256 mismatch)')
        }
        Ok ("$(M '校验' 'Verify  ')  ${GRAY}$(M 'SHA-256 一致' 'SHA-256 matches')${RESET}")
    } else {
        Die (M "校验文件看不懂：$archiveName.sha256" "the checksum file cannot be read: $archiveName.sha256")
    }
} else {
    Note (M "注意：$archiveName 旁边没有 .sha256，跳过校验" "note: there is no .sha256 next to $archiveName, so it is not verified")
}

if ($Isolated) {
    try {
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Place))
        $InstallerState.Stage = Join-Path ([IO.Path]::GetDirectoryName($Place)) ('.vika-pending-' + [guid]::NewGuid().ToString('N'))
        [void][IO.Directory]::CreateDirectory($InstallerState.Stage)
    } catch { Die (M "写不了 $Place：$($_.Exception.Message)" "cannot write $Place`: $($_.Exception.Message)") }
} else {
    # The updater takes the same lock while it upgrades.
    $deadlock = Join-Path $VikaHome 'deploy.lock'
    try {
        [void][IO.Directory]::CreateDirectory($VikaHome)
        [void][IO.Directory]::CreateDirectory((Join-Path $VikaHome 'releases'))
        New-Item -ItemType Directory -Path $deadlock -ErrorAction Stop | Out-Null
        $InstallerState.Lock = $deadlock
    } catch { Die (M "另一个安装或升级正在进行（$(Short $deadlock)）" "another install or upgrade is running ($(Short $deadlock))") }
    try {
        $InstallerState.Stage = Join-Path (Join-Path $VikaHome 'releases') ".pending-$PID"
        [void][IO.Directory]::CreateDirectory($InstallerState.Stage)
    } catch { Die (M "写不了 $(Short $VikaHome)：$($_.Exception.Message)" "cannot write $(Short $VikaHome)`: $($_.Exception.Message)") }
}

try { Expand-Package $archive $InstallerState.Stage }
catch { $detail = $_.Exception.Message; Die ((M '解压失败：' 'unpacking failed: ') + $detail) }

# What the package says about itself, then the name it arrived under.
$metadata = Read-Metadata $InstallerState.Stage
$version = $null
if ($metadata) {
    if ($metadata.platform -and $metadata.platform -ne $Platform) {
        $found = $metadata.platform
        Die (M "安装包不是给 $Platform 的：$found" "the package is not for $Platform`: $found")
    }
    if ($metadata.channel -and $metadata.channel -ne 'windows-preview') {
        $found = $metadata.channel
        Die (M "安装包不是 Windows 预览包：$found" "the package is not a Windows preview: $found")
    }
    $version = $metadata.version
}
if (-not $version -and ($archiveName -match '^vika-(.+)-win32-x64\.zip$')) { $version = $Matches[1] }
if (-not $version) { Die (M "认不出这个包的版本：$archiveName" "cannot tell which version this package is: $archiveName") }
try { Assert-Version $version } catch { Die $_.Exception.Message }
if ($InstallerState.RequestedVersion -and $version -cne $InstallerState.RequestedVersion) { Die "Package version $version does not match requested version $($InstallerState.RequestedVersion)" }

try { Test-Release $InstallerState.Stage }
catch {
    $reason = M '这台机器运行不了这个版本（系统库太旧或 CPU 不匹配）' 'this machine cannot run this release (its system libraries are too old, or the CPU differs)'
    $detail = $_.Exception.Message
    if ($detail) { $reason = $reason + ' ' + (M "（$detail）" "($detail)") }
    Die $reason
}
Ok ("$(M '自检' 'Check   ')  ${GRAY}$(M '自带的 Node 能在这台机器上运行' 'the bundled Node runs on this machine')${RESET}")

# Setup runs next only for a first install at a terminal, as install.sh decides
# it, and it asks for the OpenViking connection itself; otherwise nobody asks, so
# the check runs here (a failure is reported, not fatal: this package ships no
# OpenViking and the manifest is written afterwards).
$configFile = if ($env:VIKA_CONFIG) { $env:VIKA_CONFIG } else { Join-Path $VikaHome 'config.json' }
$interactive = -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
$setupNext = -not $Isolated -and -not $NoSetup -and $interactive -and -not (Test-Path -LiteralPath $configFile)
if (-not $Isolated -and -not $setupNext) { Test-OpenViking $InstallerState.Stage }

$node = Get-BundledNode $InstallerState.Stage
$nodeRelative = $null
if ($node) { $nodeRelative = $node.Substring($InstallerState.Stage.Length).TrimStart(@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)) }

if ($Isolated) {
    try { Move-Into $InstallerState.Stage $Place }
    catch { $detail = $_.Exception.Message; Die (M "装不到 $Place：$detail" "cannot install to $Place`: $detail") }
    $InstallerState.Stage = ''
    Ok ("$(M '安装' 'Install ')  ${GRAY}Vika $version → $(Short $Place)${RESET}")
    $changed = $true
    $previous = $null
} else {
    try { $result = Install-Release $InstallerState.Stage $version }
    catch { $detail = $_.Exception.Message; Die (M "装不到 $(Short $VikaHome)：$detail" "cannot install to $(Short $VikaHome)`: $detail") }
    $changed = $result.Changed
    $previous = $result.Previous
    # -Lang is kept, as `vika --lang` keeps it.
    if ($Lang) { Set-Content -LiteralPath (Join-Path $VikaHome 'language') -Value $Lang -Encoding ascii }
    if ($nodeRelative) {
        $binDir = Write-Launcher $nodeRelative ([IO.Path]::Combine('packages', 'cli', 'bin', 'vika.mjs'))
        if ($SessionPath) { Add-ToPath $binDir }
    }
}

Cleanup

$configFile = if ($env:VIKA_CONFIG) { $env:VIKA_CONFIG } else { Join-Path $VikaHome 'config.json' }
# Only the next step differs from install.sh: this preview has no updater, and an
# isolated copy is not reachable by `vika`, so it names its own start command.
if ($Isolated) {
    $isolatedStart = Short (Join-Path $Place 'vika-preview.ps1')
    Note (M "下一步：按 docs\windows-preview.md 写好 manifest，然后运行 $isolatedStart -Action start" "next: write the manifest as docs\windows-preview.md describes, then run $isolatedStart -Action start")
} elseif (Test-Path -LiteralPath $configFile) {
    if ($changed -and $previous) {
        Note (M "运行中的实例还在旧版本上：执行 vika restart 换到 $version" "running instances are on the old release: vika restart moves them to $version")
    }
} elseif ($setupNext) {
    # As install.sh does: the first install goes straight into setup, in the
    # terminal that ran the installer, which now knows vika.
    & (Join-Path (Join-Path $VikaHome 'bin') 'vika.cmd') setup
    if ($LASTEXITCODE) { throw "vika setup exited $LASTEXITCODE" }
} else {
    Note (M '下一步：运行 vika setup' 'next: run vika setup')
}
} finally { Cleanup }
