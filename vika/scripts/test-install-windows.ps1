# Integration smoke: real installer and lifecycle scripts in a disposable home.
# Run with Windows PowerShell 5.1 to test the installer's oldest supported host.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$installer = Join-Path $PSScriptRoot 'install-windows.ps1'
# Environment.GetEnvironmentVariable(..., 'User') expands REG_EXPAND_SZ using
# the current USERPROFILE. Compare the registry's raw value and kind so our
# deliberately changed home cannot look like a persistent PATH mutation.
function Get-RawUserPath {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
    try {
        if ($null -eq $key -or $key.GetValueNames() -notcontains 'Path') {
            return [pscustomobject]@{ Value = $null; Kind = $null }
        }
        return [pscustomobject]@{
            Value = $key.GetValue('Path', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            Kind = $key.GetValueKind('Path')
        }
    } finally { if ($null -ne $key) { $key.Dispose() } }
}
$originalUserPath = Get-RawUserPath
$originalProcessPath = $env:Path
$names = @('USERPROFILE', 'HOME', 'PM2_HOME', 'APPDATA', 'LOCALAPPDATA', 'NPM_CONFIG_CACHE', 'VIKA_CONFIG')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
# Construct Unicode without depending on the .ps1 file's legacy ANSI decoding.
$prefix = ([string][char]0x7EF4) + ([string][char]0x4EAC) + ' install fixture '
$root = Join-Path ([IO.Path]::GetTempPath()) ($prefix + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $root | Out-Null
try {
    $env:USERPROFILE = $root
    $env:HOME = $root
    $env:PM2_HOME = Join-Path $root '.vika\pm2'
    $env:APPDATA = Join-Path $root 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $root 'AppData\Local'
    $env:NPM_CONFIG_CACHE = Join-Path $root 'npm-cache'
    $env:VIKA_CONFIG = Join-Path $root '.vika\config.json'
    New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA | Out-Null

    # Do not use --ignore-scripts: this must download/install the real Windows
    # Lark binary and run the managed Pi version validation through the installer.
    & $installer -SkipSetup -SkipPathUpdate
    $current = Join-Path $root '.vika\current'
    $junction = Get-Item -LiteralPath $current -Force
    if ($junction.LinkType -ne 'Junction') { throw 'Installer did not create a current junction.' }
    $launcher = Join-Path $root '.vika\bin\vika-launch.mjs'
    $cmdLauncher = Join-Path $root '.vika\bin\vika.cmd'
    foreach ($file in @($launcher, $cmdLauncher, (Join-Path $current 'scripts\pm2-windows.cjs'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing installed file: $file" }
    }
    $help = & $node $launcher --help
    if ($LASTEXITCODE -ne 0 -or -not (($help -join "`n") -match 'vika')) { throw 'Installed Node launcher --help failed.' }
    $cmdHelp = & $cmdLauncher --help
    if ($LASTEXITCODE -ne 0 -or -not (($cmdHelp -join "`n") -match 'vika')) { throw 'Installed vika.cmd --help failed.' }
    & $node (Join-Path $current 'packages\daemon\scripts\check-pi.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Installed managed Pi validation failed.' }
    $lark = Join-Path $current 'node_modules\@larksuite\cli\bin\lark-cli.exe'
    if (-not (Test-Path -LiteralPath $lark -PathType Leaf)) { throw 'npm lifecycle did not install the Windows Lark binary.' }
    & $lark --version
    if ($LASTEXITCODE -ne 0) { throw 'Installed Windows Lark binary could not execute.' }
    if (Test-Path -LiteralPath $env:VIKA_CONFIG) { throw 'SkipSetup unexpectedly created a connector configuration.' }
    if (Test-Path -LiteralPath $env:PM2_HOME) { throw 'Install smoke unexpectedly started PM2.' }
    $currentUserPath = Get-RawUserPath
    if ($currentUserPath.Value -cne $originalUserPath.Value -or $currentUserPath.Kind -ne $originalUserPath.Kind) { throw 'SkipPathUpdate changed the user PATH.' }
    if ($env:Path -cne $originalProcessPath) { throw 'SkipPathUpdate changed the process PATH.' }

    $refused = $false
    try { & $installer -SkipSetup -SkipPathUpdate } catch {
        if ($_.Exception.Message -notmatch 'already exists') { throw }
        $refused = $true
    }
    if (-not $refused) { throw 'Installer did not refuse an existing installation.' }
    Write-Host 'Windows source install, junction, launchers, npm lifecycle and bundled CLIs passed.'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    $env:Path = $originalProcessPath
    # Remove the junction itself first, never recursively traverse its target.
    $current = Join-Path $root '.vika\current'
    if (Test-Path -LiteralPath $current) { [IO.Directory]::Delete($current) }
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
