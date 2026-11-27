# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

# OpenViking server installer and guided setup for Windows x64.
# Runs on Windows PowerShell 5.1 and PowerShell 7+, without administrator rights.
#
#   irm https://raw.githubusercontent.com/volcengine/OpenViking/main/scripts/windows/install-openviking.ps1 | iex
#
# The steps are the self-hosted quickstart's, in one command: install uv when it is
# missing, install Python, `uv tool install openviking`, put the config path in the
# environment, then hand over to `openviking-server init` (the same setup wizard as on
# macOS and Linux) and `openviking-server doctor`.
#
# Options need the script as a file, since `iex` cannot take parameters:
#   .\install-openviking.ps1 -PythonVersion 3.13 -Package openviking -NoInit
# -NoInit installs and configures the environment, and stops before the wizard.
# Everything this script prints stays ASCII: Windows PowerShell 5.1 decodes a fetched
# script with the ANSI code page, so non-ASCII text in it would arrive garbled.
[CmdletBinding()]
param(
    [string]$PythonVersion = '3.13',
    [string]$Package = 'openviking',
    [switch]$NoInit
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$PsMajor = $PSVersionTable.PSVersion.Major
$OnWindows = ($PsMajor -lt 6) -or $IsWindows
if (-not $OnWindows) {
    throw 'This script installs the OpenViking server on Windows; on macOS, Linux and WSL use the self-hosted quickstart instead.'
}
if ($PsMajor -lt 5) {
    throw 'Windows PowerShell 5.1 or PowerShell 7 is required; this is PowerShell ' + $PSVersionTable.PSVersion.ToString() + '.'
}
$Interactive = -not [Console]::IsInputRedirected

function Write-Step($Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Ok($Text) {
    Write-Host "   OK   $Text" -ForegroundColor Green
}

function Write-Note($Text) {
    Write-Host "   ..   $Text"
}

function Write-Warn($Text) {
    Write-Host "   !!   $Text" -ForegroundColor Yellow
}

function Confirm-Step($Question, [switch]$DefaultYes) {
    if (-not $Interactive) {
        return [bool]$DefaultYes
    }
    $Hint = if ($DefaultYes) { '[Y/n]' } else { '[y/N]' }
    $Answer = Read-Host "$Question $Hint"
    if (-not $Answer) {
        return [bool]$DefaultYes
    }
    return ($Answer -match '^(y|yes)$')
}

function Update-PathFromEnvironment {
    # Pick up PATH entries an installer just wrote, without asking for a new terminal.
    $Machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $User = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($Machine, $User) | Where-Object { $_ }) -join ';'
}

function Add-UserPath($Directory) {
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $Entries = @($UserPath -split ';' | Where-Object { $_ })
    if ($Entries -contains $Directory) {
        return $false
    }
    $New = (@($Entries) + $Directory) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $New, 'User')
    return $true
}

function Get-UvToolBinDir {
    $Saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $Out = & uv tool dir --bin 2>$null
        if ($LASTEXITCODE -eq 0 -and $Out) {
            return ($Out | Select-Object -First 1).ToString().Trim()
        }
    } catch {
    } finally {
        $ErrorActionPreference = $Saved
    }
    if ($env:UV_TOOL_BIN_DIR) {
        return $env:UV_TOOL_BIN_DIR
    }
    return (Join-Path $env:USERPROFILE '.local\bin')
}

# Windows PowerShell 5.1 needs TLS 1.2 for the download below, and an ANSI console
# needs UTF-8 to print what the Python side sends.
if ($PsMajor -lt 6) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {
    }
}
$SavedOutputEncoding = [Console]::OutputEncoding
$SavedPythonUtf8 = $env:PYTHONUTF8
try {
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    } catch {
    }
    # The wizard prints symbols such as a check mark; without UTF-8 a code page like
    # GBK fails to encode them when its output is redirected (#7414).
    $env:PYTHONUTF8 = '1'

    Write-Host ''
    Write-Host 'OpenViking server setup for Windows' -ForegroundColor Cyan
    $PsNote = if ($PsMajor -lt 6) {
        'Windows PowerShell: TLS 1.2 and the UTF-8 console are set for this run.'
    } else {
        'PowerShell 7: TLS 1.2 and the UTF-8 console are already the default.'
    }
    Write-Note ('PowerShell ' + $PSVersionTable.PSVersion.ToString() + ' - ' + $PsNote)

    # ---- uv ------------------------------------------------------------------
    Write-Step 'uv'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Note 'uv is the installer the self-hosted quickstart uses; it is not on PATH.'
        if (-not (Confirm-Step 'Install uv now?' -DefaultYes)) {
            throw 'uv is required: install it from https://docs.astral.sh/uv/getting-started/installation/ and run this script again.'
        }
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            & winget install --id astral-sh.uv -e --source winget `
                --accept-source-agreements --accept-package-agreements --disable-interactivity
            $Code = $LASTEXITCODE
            if ($Code -ne 0) {
                Write-Warn "winget exited with $Code; falling back to the uv installer script."
            }
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Invoke-WebRequest 'https://astral.sh/uv/install.ps1' -UseBasicParsing | Invoke-Expression
            Update-PathFromEnvironment
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv is still not on PATH; open a new terminal and run this script again.'
        }
    }
    Write-Ok ((& uv --version) -join ' ')

    $UvBinDir = Get-UvToolBinDir

    # ---- Python and the OpenViking tool --------------------------------------
    Write-Step "Python $PythonVersion"
    & uv python install $PythonVersion
    $Code = $LASTEXITCODE
    if ($Code -ne 0) {
        throw "uv could not install Python $PythonVersion (exit $Code)."
    }
    Write-Ok "Python $PythonVersion is available to uv."

    Write-Step "OpenViking ($Package)"
    $ToolList = (& uv tool list) -join "`n"
    if ($ToolList -match '(?m)^openviking(\s|$)') {
        Write-Note 'Already installed with uv; upgrading.'
        & uv tool upgrade openviking
        $Code = $LASTEXITCODE
        if ($Code -ne 0) {
            throw "uv could not upgrade OpenViking (exit $Code). Run: uv tool install --force --python $PythonVersion `"$Package`""
        }
    } else {
        & uv tool install --python $PythonVersion $Package
        $Code = $LASTEXITCODE
        if ($Code -ne 0) {
            throw "uv could not install $Package (exit $Code)."
        }
    }
    $env:Path = "$UvBinDir;$env:Path"
    $ServerCommand = Get-Command openviking-server -ErrorAction SilentlyContinue
    if (-not $ServerCommand) {
        throw "openviking-server is not in $UvBinDir; open a new terminal and run this script again."
    }
    $Server = $ServerCommand.Source
    if (-not $Server) {
        $Server = $ServerCommand.Name
    }
    Write-Ok "Installed to $UvBinDir"

    if (Add-UserPath $UvBinDir) {
        Write-Note "Added $UvBinDir to your user PATH; new terminals find openviking-server without this script."
    }

    # ---- Configuration location ---------------------------------------------
    Write-Step 'Configuration'
    $ConfigDir = Join-Path $env:USERPROFILE '.openviking'
    if (-not (Test-Path $ConfigDir)) {
        New-Item -ItemType Directory -Path $ConfigDir | Out-Null
    }
    # Set both paths for this terminal and for new ones, as the Windows install notes
    # ask, and keep a value the user already chose.
    $ConfigPath = $env:OPENVIKING_CONFIG_FILE
    if (-not $ConfigPath) {
        $ConfigPath = Join-Path $ConfigDir 'ov.conf'
        [Environment]::SetEnvironmentVariable('OPENVIKING_CONFIG_FILE', $ConfigPath, 'User')
    }
    $env:OPENVIKING_CONFIG_FILE = $ConfigPath
    $CliConfigPath = $env:OPENVIKING_CLI_CONFIG_FILE
    if (-not $CliConfigPath) {
        $CliConfigPath = Join-Path $ConfigDir 'ovcli.conf'
        [Environment]::SetEnvironmentVariable('OPENVIKING_CLI_CONFIG_FILE', $CliConfigPath, 'User')
    }
    $env:OPENVIKING_CLI_CONFIG_FILE = $CliConfigPath
    Write-Ok "OPENVIKING_CONFIG_FILE=$ConfigPath"
    Write-Note "OPENVIKING_CLI_CONFIG_FILE=$CliConfigPath"
    if (Test-Path $ConfigPath) {
        Write-Note 'An existing config stays in place; the wizard offers a partial reconfiguration and a backup.'
    }

    # ---- Setup wizard --------------------------------------------------------
    Write-Step 'Setup wizard'
    if ($NoInit) {
        Write-Note 'Skipped (-NoInit). Run openviking-server init when you are ready.'
    } elseif (-not $Interactive) {
        Write-Warn 'No interactive console, so the wizard did not start. Run openviking-server init in a terminal.'
    } else {
        Write-Note 'It asks for the OV access mode, the embedding and VLM models, and the server settings.'
        & $Server init
        $Code = $LASTEXITCODE
        if ($Code -eq 130) {
            Write-Warn 'The wizard was cancelled; no configuration was written.'
        } elseif ($Code -ne 0) {
            throw "The setup wizard exited with $Code."
        } else {
            Write-Ok 'Configuration written.'
        }
    }

    # ---- Check ---------------------------------------------------------------
    Write-Step 'Check'
    if (-not (Test-Path $ConfigPath)) {
        Write-Warn "No configuration at $ConfigPath yet; openviking-server doctor reports what is missing."
    }
    & $Server doctor
    $Code = $LASTEXITCODE
    if ($Code -eq 0) {
        Write-Ok 'The configuration, model access and authentication check out.'
    } else {
        Write-Warn "openviking-server doctor exited with ${Code}: fix the setting it names and run it again."
    }

    # ---- Start ---------------------------------------------------------------
    if ($Interactive -and (Test-Path $ConfigPath) -and (Confirm-Step 'Start the server now in a new window?' -DefaultYes)) {
        Write-Step 'Server'
        Start-Process -FilePath $Server -WorkingDirectory (Split-Path $ConfigPath -Parent)
        $Ready = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            try {
                Invoke-WebRequest 'http://127.0.0.1:1933/ready' -UseBasicParsing -TimeoutSec 2 | Out-Null
                $Ready = $true
                break
            } catch {
            }
        }
        if ($Ready) {
            Write-Ok 'http://127.0.0.1:1933/ready answers; the server window keeps the log.'
        } else {
            Write-Warn 'It is not ready yet: read the server window, or run openviking-server in a terminal to see why.'
        }
    }

    Write-Step 'Next'
    Write-Note 'Connect the ov CLI: ov config   (local service: http://127.0.0.1:1933)'
    Write-Note 'Check the CLI:      ov health'
    Write-Note 'Run the server:     openviking-server'
    Write-Note 'Reconfigure:        openviking-server init'
    Write-Host ''
} finally {
    $env:PYTHONUTF8 = $SavedPythonUtf8
    if ($SavedOutputEncoding) {
        try {
            [Console]::OutputEncoding = $SavedOutputEncoding
        } catch {
        }
    }
}
