# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
# Installer tests for install-preview.ps1. Each case runs the installer as a
# child process, so the exit code, the text and the file system are all checked
# the way a user meets them. OV_SUPERAGENT_HOME keeps the default layout in a temp
# directory and off the real profile and PATH.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$installer = Join-Path $PSScriptRoot 'install-preview.ps1'
$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) { $hostExe = (Get-Command -Name $(if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' })).Source }
$work = Join-Path ([IO.Path]::GetTempPath()) ('superagent-installer-test-' + [guid]::NewGuid().ToString('N'))
$testHome = Join-Path $work 'home'
[IO.Directory]::CreateDirectory($testHome) | Out-Null

# A package laid out like build-windows-preview.mjs makes one: metadata, the
# preview script the installer runs as its self-check, a bundled node and the
# CLI entry point, plus its .sha256.
function New-Fixture {
    param(
        [string]$Name,
        [string]$Version = '0.4.0-windows-preview-test',
        [string]$FixturePlatform = 'win32-x64',
        [string]$Channel = 'windows-preview',
        [string]$Check = 'param($Action)',
        [switch]$Traversal,
        [switch]$NoChecksum,
        [switch]$OvCheck,
        [string]$ChecksumName
    )
    $file = Join-Path $work "$Name.zip"
    $entries = [ordered]@{
        'windows-preview.json' = (@{version=$Version; platform=$FixturePlatform; channel=$Channel} | ConvertTo-Json -Compress)
        'superagent-preview.ps1' = $Check
        'runtime/node.exe' = ''
        'packages/cli/bin/vika.mjs' = ''
    }
    if ($OvCheck) { $entries['packages/cli/src/check-ov.mjs'] = '' }
    if ($Traversal) { $entries['../escaped.txt'] = 'must not escape' }
    $zip = [IO.Compression.ZipFile]::Open($file, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($name in $entries.Keys) {
            $writer = New-Object IO.StreamWriter($zip.CreateEntry($name).Open())
            try { $writer.Write($entries[$name]) } finally { $writer.Dispose() }
        }
    } finally { $zip.Dispose() }
    if (-not $NoChecksum) {
        $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
        $named = if ($ChecksumName) { $ChecksumName } else { [IO.Path]::GetFileName($file) }
        Set-Content -LiteralPath "$file.sha256" -Value "$hash  $named" -Encoding ascii
    }
    return $file
}

# Redirect process handles, not PowerShell 5.1 error records (which wrap lines).
function Invoke-TestProcess {
    param([string[]]$Arguments)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $hostExe
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.CreateNoWindow = $true
    # These controlled fixture arguments contain no quotes or trailing slashes.
    $info.Arguments = (($Arguments | ForEach-Object {
        if ($_ -match '"' -or $_.EndsWith('\')) { throw 'unsupported fixture argument' }
        '"' + $_ + '"'
    }) -join ' ')
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        return [pscustomobject]@{ Code = $process.ExitCode; Output = $stdout.Result + "`n" + $stderr.Result }
    } finally { $process.Dispose() }
}

# Runs the installer and returns its exit code and its output (stderr included).
function Invoke-Installer {
    param([string[]]$Arguments, [string]$Root = $testHome)
    $previous = $env:OV_SUPERAGENT_HOME
    $previousColour = $env:NO_COLOR
    $previousAction = $ErrorActionPreference
    $env:OV_SUPERAGENT_HOME = $Root
    $env:NO_COLOR = '1'
    $ErrorActionPreference = 'Continue'
    try {
        $result = Invoke-TestProcess -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installer) + $Arguments)
        $output = $result.Output
        $code = $result.Code
    } finally {
        $ErrorActionPreference = $previousAction
        $env:OV_SUPERAGENT_HOME = $previous
        $env:NO_COLOR = $previousColour
    }
    return [pscustomobject]@{ Code = $code; Output = $output }
}

function Expect-Failure {
    param([string]$Name, [string[]]$Arguments, [string]$Pattern)
    $run = Invoke-Installer -Arguments $Arguments
    if ($run.Code -eq 0) { throw "${Name}: the installer succeeded" }
    if ($run.Code -ne 1) { throw "${Name}: exit code $($run.Code)`n$($run.Output)" }
    if ($run.Output -notmatch $Pattern) { throw "${Name}: no '$Pattern' in:`n$($run.Output)" }
    return $run
}

function Resolve-LinkOrFile([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { throw "nothing at $path" }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.LinkType) { return [IO.Path]::GetFullPath(@($item.Target)[0]) }
    if ($item.PSIsContainer) { return $item.FullName }
    return (Get-Content -LiteralPath $path -Raw).Trim()
}

try {
    # ---- an isolated copy: exactly one new directory, nothing else ----------
    $valid = New-Fixture 'valid'
    $destination = Join-Path $work 'relocated package'
    $run = Invoke-Installer -Arguments @('-File', $valid, '-Destination', $destination, '-Lang', 'en')
    if ($run.Code -ne 0) { throw "isolated install failed:`n$($run.Output)" }
    if (-not (Test-Path -LiteralPath (Join-Path $destination 'windows-preview.json'))) { throw 'isolated install did not finish' }
    if ($run.Output -notmatch 'OpenViking SuperAgent 0\.4\.0-windows-preview-test') { throw "the install line does not name the version:`n$($run.Output)" }
    if (Test-Path -LiteralPath (Join-Path $testHome 'releases')) { throw 'an isolated install wrote into OV_SUPERAGENT_HOME' }
    if (Test-Path -LiteralPath (Join-Path $testHome 'bin')) { throw 'an isolated install created a launcher' }
    Expect-Failure 'existing destination' @('-File', $valid, '-Destination', $destination, '-Lang', 'en') 'Destination already exists' | Out-Null

    # ---- the same refusals as before, now in the installer's words ----------
    $bad = New-Fixture 'bad-hash'
    Add-Content -LiteralPath $bad -Value 'corruption'
    $failed = Expect-Failure 'checksum' @('-File', $bad, '-Destination', (Join-Path $work 'bad'), '-Lang', 'en') 'failed verification \(SHA-256 mismatch\)'
    if ($failed.Output -notmatch 'vika install failed: ') { throw "the failure has no prefix:`n$($failed.Output)" }

    $misnamed = New-Fixture 'misnamed' -ChecksumName 'another-package.zip'
    Expect-Failure 'checksum name' @('-File', $misnamed, '-Destination', (Join-Path $work 'misnamed'), '-Lang', 'en') 'the checksum file names another-package\.zip' | Out-Null

    $escape = New-Fixture 'traversal' -Traversal
    Expect-Failure 'traversal' @('-File', $escape, '-Destination', (Join-Path $work 'escape'), '-Lang', 'en') 'ZIP path escapes destination' | Out-Null
    if (Test-Path -LiteralPath (Join-Path $work 'escaped.txt')) { throw 'the ZIP escaped the staging directory' }

    $wrong = New-Fixture 'wrong-platform' -FixturePlatform 'linux-x64'
    Expect-Failure 'platform' @('-File', $wrong, '-Destination', (Join-Path $work 'wrong'), '-Lang', 'en') 'the package is not for win32-x64: linux-x64' | Out-Null

    $channel = New-Fixture 'wrong-channel' -Channel 'stable'
    Expect-Failure 'channel' @('-File', $channel, '-Destination', (Join-Path $work 'channel'), '-Lang', 'en') 'the package is not a Windows preview: stable' | Out-Null

    $broken = New-Fixture 'broken-runtime' -Check "throw 'runtime check failed'"
    $failed = Expect-Failure 'runtime' @('-File', $broken, '-Destination', (Join-Path $work 'broken'), '-Lang', 'en') 'this machine cannot run this release'
    if ($failed.Output -notmatch 'runtime check failed') { throw "the self-check detail is missing:`n$($failed.Output)" }

    foreach ($name in @('bad', 'misnamed', 'escape', 'wrong', 'channel', 'broken')) {
        if (Test-Path -LiteralPath (Join-Path $work $name)) { throw "a failed install created $name" }
    }
    if (Get-ChildItem -LiteralPath $work -Filter '.superagent-pending-*' -Force) { throw 'staging directories were left behind' }

    # Reject version traversal before running package code or changing releases.
    $sentinel = Join-Path $testHome 'outside.txt'
    Set-Content -LiteralPath $sentinel -Value 'keep-me'
    $badVersions = @('..', '.', '../outside', '..\outside', 'C:\outside', 'v1:ads', 'CON', 'v1.')
    for ($i = 0; $i -lt $badVersions.Count; $i++) {
        $badVersion = New-Fixture "bad-version-$i" -Version $badVersions[$i] -Check "throw 'must not run'"
        Expect-Failure 'version' @('-File', $badVersion, '-NoSetup', '-Lang', 'en') 'Invalid release version' | Out-Null
        if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne 'keep-me') { throw 'version validation changed outside data' }
    }

    # ---- a package without a checksum installs, and says so -----------------
    $loose = New-Fixture 'no-checksum' -NoChecksum
    $run = Invoke-Installer -Arguments @('-File', $loose, '-Destination', (Join-Path $work 'loose'), '-Lang', 'en')
    if ($run.Code -ne 0) { throw "a package without a checksum failed:`n$($run.Output)" }
    if ($run.Output -notmatch 'is not verified') { throw "the missing checksum is not reported:`n$($run.Output)" }

    # ---- the default layout: releases, current, rollback, launcher, lock ----
    $releases = Join-Path $testHome 'releases'
    $first = New-Fixture 'first' -Version '0.5.0-windows-preview-test'
    $run = Invoke-Installer -Arguments @('-File', $first, '-Lang', 'en')
    if ($run.Code -ne 0) { throw "the default install failed:`n$($run.Output)" }
    $one = [IO.Path]::GetFullPath((Join-Path $releases '0.5.0-windows-preview-test'))
    if ((Resolve-LinkOrFile (Join-Path $testHome 'current')) -ne $one) { throw 'current does not point at the release' }
    $launcher = [IO.Path]::Combine($testHome, 'bin', 'ov-superagent.cmd')
    if (-not (Test-Path -LiteralPath ([IO.Path]::Combine($testHome, 'bin', 'vika.cmd')))) { throw 'no compatibility launcher' }
    if (-not (Test-Path -LiteralPath $launcher)) { throw 'no bin\ov-superagent.cmd was written' }
    $launcherText = Get-Content -LiteralPath $launcher -Raw
    if ($launcherText -notmatch 'current') { throw 'the launcher does not go through current' }
    if ($launcherText -notmatch 'vika\.mjs') { throw 'the launcher does not run the CLI' }
    # The home may be HOME or the profile, so the launcher reads neither: it finds
    # the .superagent directory it was written into.
    if ($launcherText -match 'USERPROFILE|%HOME%') { throw "the launcher guesses the home directory:`n$launcherText" }
    if ($launcherText -notmatch '%~dp0') { throw "the launcher does not resolve its own location:`n$launcherText" }
    # Redirected output uses the console code page, which can turn the arrow into ?/^Z.
    if ($run.Output -notmatch 'OpenViking SuperAgent 0\.5\.0-windows-preview-test \S+ ') { throw "the install line is missing:`n$($run.Output)" }
    if ($run.Output -notmatch 'next: run ov-superagent setup') { throw "no next step is named:`n$($run.Output)" }
    if (Test-Path -LiteralPath (Join-Path $testHome 'deploy.lock')) { throw 'the lock was not released' }
    if (-not (Test-Path -LiteralPath (Join-Path $testHome 'language'))) { throw '-Lang was not saved for later commands' }
    if ((Get-Content -LiteralPath (Join-Path $testHome 'language') -Raw).Trim() -ne 'en') { throw 'the saved language is not the one asked for' }

    # The shell that ran the installer already knows vika: a .ps1 run with &
    # shares its environment, which is why setup can be started from here.
    $probe = Join-Path $work 'session.ps1'
    Set-Content -LiteralPath $probe -Encoding ascii -Value @"
`$env:OV_SUPERAGENT_HOME = '$testHome'
`$env:NO_COLOR = '1'
& '$installer' -File '$first' -Lang en -NoSetup
if ((`$env:PATH -split [IO.Path]::PathSeparator) -contains (Join-Path `$env:OV_SUPERAGENT_HOME 'bin')) { 'SESSION-PATH-OK' } else { "MISSING: `$env:PATH" }
"@
    $sessionResult = Invoke-TestProcess -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $probe)
    $session = $sessionResult.Output
    if ($sessionResult.Code -ne 0) { throw "session probe failed: $session" }
    if ($session -notmatch 'SESSION-PATH-OK') { throw "the current terminal was not given vika:`n$session" }

    # A second run of the same package is a no-op that still names the version.
    # English output: redirected streams use the console code page, which turns CJK into ?.
    $again = Invoke-Installer -Arguments @('-File', $first, '-Lang', 'en')
    if ($again.Code -ne 0) { throw "the repeated install failed:`n$($again.Output)" }
    if ($again.Output -notmatch 'OpenViking SuperAgent 0\.5\.0-windows-preview-test is installed already') { throw "the repeated install is not idempotent:`n$($again.Output)" }

    # Same version but different bytes must not overwrite an existing release.
    $conflict = New-Fixture 'same-version-different-content' -Version '0.5.0-windows-preview-test' -Check 'param($Action) # different'
    $beforeHash = (Get-FileHash -LiteralPath (Join-Path $one 'superagent-preview.ps1')).Hash
    Expect-Failure 'release conflict' @('-File', $conflict, '-NoSetup', '-Lang', 'en') 'Release conflict' | Out-Null
    if ((Get-FileHash -LiteralPath (Join-Path $one 'superagent-preview.ps1')).Hash -ne $beforeHash) { throw 'existing release was overwritten' }
    if ((Resolve-LinkOrFile (Join-Path $testHome 'current')) -ne $one) { throw 'conflict changed current' }

    # A new package moves current and keeps the old release as rollback.
    $second = New-Fixture 'second' -Version '0.6.0-windows-preview-test'
    $run = Invoke-Installer -Arguments @('-File', $second, '-Lang', 'en')
    if ($run.Code -ne 0) { throw "the upgrade failed:`n$($run.Output)" }
    $two = [IO.Path]::GetFullPath((Join-Path $releases '0.6.0-windows-preview-test'))
    if ((Resolve-LinkOrFile (Join-Path $testHome 'current')) -ne $two) { throw 'current did not move to the new release' }
    if ((Resolve-LinkOrFile (Join-Path $testHome 'rollback')) -ne $one) { throw 'rollback does not point at the previous release' }
    if (-not (Test-Path -LiteralPath $one)) { throw 'the previous release was removed' }

    # With an instance installed the next step is the restart, as install.sh says.
    Set-Content -LiteralPath (Join-Path $testHome 'config.json') -Value '{}' -Encoding ascii
    $third = New-Fixture 'third' -Version '0.7.0-windows-preview-test'
    $run = Invoke-Installer -Arguments @('-File', $third, '-Lang', 'en')
    if ($run.Code -ne 0) { throw "installing over an instance failed:`n$($run.Output)" }
    if ($run.Output -notmatch 'ov-superagent restart') { throw "the running instance is not mentioned:`n$($run.Output)" }
    if ($run.Output -match 'next: ') { throw "a configured machine is told to set up again:`n$($run.Output)" }

    # -NoSetup never enters setup, and the next step is still named (as install.sh
    # does for --no-setup), so a scripted run ends with a usable command.
    $quietHome = Join-Path $work 'quiet-home'
    [IO.Directory]::CreateDirectory($quietHome) | Out-Null
    $quiet = New-Fixture 'quiet' -Version '0.8.0-windows-preview-test'
    $run = Invoke-Installer -Arguments @('-File', $quiet, '-Lang', 'en', '-NoSetup') -Root $quietHome
    if ($run.Code -ne 0) { throw "-NoSetup failed:`n$($run.Output)" }
    if ($run.Output -notmatch 'next: run ov-superagent setup') { throw "-NoSetup did not name the next step:`n$($run.Output)" }

    # ---- the home is HOME when it is set, not %USERPROFILE% -----------------
    # OpenViking SuperAgent explicitly prefers HOME; Windows Node os.homedir() does not.
    # Clear OS only in this test child to suppress persistent user PATH writes.
    $homeWins = Join-Path $work 'home-wins'
    $profileIgnored = Join-Path $work 'profile-ignored'
    [IO.Directory]::CreateDirectory($homeWins) | Out-Null
    [IO.Directory]::CreateDirectory($profileIgnored) | Out-Null
    $saved = @{ OV_SUPERAGENT_HOME = $env:OV_SUPERAGENT_HOME; HOME = $env:HOME; USERPROFILE = $env:USERPROFILE; OS = $env:OS }
    $env:OV_SUPERAGENT_HOME = $null
    $env:OS = ''
    $env:HOME = $homeWins
    $env:USERPROFILE = $profileIgnored
    try {
        $result = Invoke-TestProcess -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installer, "-File", $first, "-NoSetup", "-Lang", "en")
        $output = $result.Output
        $code = $result.Code
    } finally {
        foreach ($key in $saved.Keys) { Set-Item -LiteralPath "Env:$key" -Value $saved[$key] }
    }
    if ($code -ne 0) { throw "installing with HOME set failed:`n$output" }
    if (-not (Test-Path -LiteralPath (Join-Path $homeWins '.superagent\releases'))) { throw "HOME was not used:`n$output" }
    if (Test-Path -LiteralPath (Join-Path $profileIgnored '.superagent')) { throw 'USERPROFILE won over HOME' }

    # ---- OpenViking is reported, not required -------------------------------
    $ov = New-Fixture 'ov' -Version '0.9.0-windows-preview-test' -OvCheck
    $run = Invoke-Installer -Arguments @('-File', $ov, '-Lang', 'en')
    if ($run.Code -ne 0) { throw "an unavailable OpenViking stopped the install:`n$($run.Output)" }
    if ($run.Output -notmatch 'OpenViking check failed') { throw "the OpenViking result is not reported:`n$($run.Output)" }

    if (Get-ChildItem -LiteralPath $releases -Filter '.pending-*' -Force) { throw 'a staging directory was left in releases' }
    Write-Host 'Installer tests passed: isolated and default layouts, idempotency, rollback, launcher, lock, checksum, traversal, platform, runtime, OpenViking.'
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
