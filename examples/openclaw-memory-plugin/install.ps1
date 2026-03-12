param(
  [switch]$Yes,
  [switch]$Zh,
  [string]$Workdir = ""
)

$ErrorActionPreference = "Stop"

function T {
  param(
    [string]$En,
    [string]$ZhText
  )
  if ($Zh) { return $ZhText }
  return $En
}

function Info($m) { Write-Host "[INFO] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[ERROR] $m" -ForegroundColor Red }
function Title($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Utf8NoBom {
  param(
    [string]$Path,
    [string]$Content
  )
  $enc = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

$Repo = if ($env:REPO) { $env:REPO } else { "volcengine/OpenViking" }
$Branch = if ($env:BRANCH) { $env:BRANCH } else { "main" }
$NpmRegistry = if ($env:NPM_REGISTRY) { $env:NPM_REGISTRY } else { "https://registry.npmmirror.com" }
$PipIndexUrl = if ($env:PIP_INDEX_URL) { $env:PIP_INDEX_URL } else { "https://pypi.tuna.tsinghua.edu.cn/simple" }

$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$OpenClawDir = if ($Workdir) { $Workdir } else { Join-Path $HomeDir ".openclaw" }
$OpenVikingDir = Join-Path $HomeDir ".openviking"
$PluginDest = Join-Path $OpenClawDir "extensions\memory-openviking"
$SelectedMode = "local"

$DefaultServerPort = 1933
$DefaultAgfsPort = 1833
$DefaultVlmModel = "doubao-seed-2-0-pro-260215"
$DefaultEmbeddingModel = "doubao-embedding-vision-251215"

function Get-PythonCommand {
  if ($env:OPENVIKING_PYTHON) { return $env:OPENVIKING_PYTHON }
  if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
  if (Get-Command python3 -ErrorAction SilentlyContinue) { return "python3" }
  return $null
}

function Check-Python {
  $py = Get-PythonCommand
  if (-not $py) {
    return @{ Ok = $false; Detail = (T "Python not found. Install Python >= 3.10." "Python 未找到，请安装 Python >= 3.10") }
  }
  try {
    $v = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if (-not $v) {
      return @{ Ok = $false; Detail = (T "Python command failed." "Python 命令执行失败") }
    }
    $parts = $v.Trim().Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
      return @{ Ok = $false; Detail = (T "Python $v is too old. Need >= 3.10." "Python 版本 $v 过低，需要 >= 3.10") }
    }
    return @{ Ok = $true; Detail = "$v ($py)"; Cmd = $py }
  } catch {
    return @{ Ok = $false; Detail = $_.Exception.Message }
  }
}

function Check-Node {
  try {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
      return @{ Ok = $false; Detail = (T "Node.js not found. Install Node.js >= 22." "Node.js 未找到，请安装 Node.js >= 22") }
    }
    $v = (node -v).Trim()
    $major = [int]($v.TrimStart("v").Split(".")[0])
    if ($major -lt 22) {
      return @{ Ok = $false; Detail = (T "Node.js $v is too old. Need >= 22." "Node.js 版本 $v 过低，需要 >= 22") }
    }
    return @{ Ok = $true; Detail = $v }
  } catch {
    return @{ Ok = $false; Detail = $_.Exception.Message }
  }
}

function Validate-Environment {
  Info (T "Checking OpenViking runtime environment..." "正在校验 OpenViking 运行环境...")
  Write-Host ""

  $missing = @()

  $py = Check-Python
  if ($py.Ok) {
    Info ("  Python: {0} ✓" -f $py.Detail)
  } else {
    $missing += "Python >= 3.10"
    Err ("  {0}" -f $py.Detail)
  }

  $node = Check-Node
  if ($node.Ok) {
    Info ("  Node.js: {0} ✓" -f $node.Detail)
  } else {
    $missing += "Node.js >= 22"
    Err ("  {0}" -f $node.Detail)
  }

  if ($missing.Count -gt 0) {
    Write-Host ""
    Err (T "Environment check failed. Install missing dependencies first." "环境校验未通过，请先安装以下缺失组件。")
    Write-Host ""
    if ($missing -contains "Python >= 3.10") {
      Write-Host (T "Python (example via winget):" "Python（可使用 winget 安装示例）：")
      Write-Host "  winget install --id Python.Python.3.11 -e"
      Write-Host ""
    }
    if ($missing -contains "Node.js >= 22") {
      Write-Host (T "Node.js (example via nvm-windows):" "Node.js（可使用 nvm-windows 安装示例）：")
      Write-Host "  nvm install 22.22.0"
      Write-Host "  nvm use 22.22.0"
      Write-Host ""
    }
    exit 1
  }

  Write-Host ""
  Info (T "Environment check passed ✓" "环境校验通过 ✓")
  Write-Host ""
}

function Check-OpenClaw {
  if ($env:SKIP_OPENCLAW -eq "1") {
    Info (T "Skipping OpenClaw check (SKIP_OPENCLAW=1)" "跳过 OpenClaw 校验 (SKIP_OPENCLAW=1)")
    return
  }

  Info (T "Checking OpenClaw..." "正在校验 OpenClaw...")
  if (Get-Command openclaw -ErrorAction SilentlyContinue) {
    Info (T "OpenClaw detected ✓" "OpenClaw 已安装 ✓")
    return
  }

  Err (T "OpenClaw not found. Install it manually, then rerun this script." "未检测到 OpenClaw，请先手动安装后再执行本脚本")
  Write-Host ""
  Write-Host (T "Recommended command:" "推荐命令：")
  Write-Host "  npm install -g openclaw --registry $NpmRegistry"
  Write-Host ""
  Write-Host "  openclaw --version"
  Write-Host "  openclaw onboard"
  Write-Host ""
  exit 1
}

function Install-OpenViking {
  if ($env:SKIP_OPENVIKING -eq "1") {
    Info (T "Skipping OpenViking install (SKIP_OPENVIKING=1)" "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)")
    return
  }

  $py = (Check-Python).Cmd
  Info (T "Installing OpenViking from PyPI..." "正在安装 OpenViking (PyPI)...")
  Info ("{0} {1}" -f (T "Using pip index:" "使用 pip 镜像源:"), $PipIndexUrl)
  & $py -m pip install --upgrade pip -i $PipIndexUrl | Out-Host
  & $py -m pip install openviking -i $PipIndexUrl | Out-Host
  Info (T "OpenViking installed ✓" "OpenViking 安装完成 ✓")
}

function Prompt-OrDefault {
  param(
    [string]$PromptText,
    [string]$DefaultValue
  )
  $v = Read-Host "$PromptText [$DefaultValue]"
  if ([string]::IsNullOrWhiteSpace($v)) { return $DefaultValue }
  return $v.Trim()
}

function Prompt-Optional {
  param([string]$PromptText)
  $v = Read-Host $PromptText
  if ([string]::IsNullOrWhiteSpace($v)) { return "" }
  return $v.Trim()
}

function Select-Workdir {
  # Already set via -Workdir
  $defaultDir = Join-Path $HomeDir ".openclaw"
  if ($OpenClawDir -ne $defaultDir) { return }

  $instances = @()
  Get-ChildItem -Path $HomeDir -Directory -Filter ".openclaw*" -ErrorAction SilentlyContinue | ForEach-Object {
    $name = $_.Name
    if ($name -eq ".openclaw" -or $name -like ".openclaw-*") {
      $instances += $_.FullName
    }
  }

  if ($instances.Count -le 1) { return }

  if (-not $Yes) {
    Write-Host ""
    Title (T "Found multiple OpenClaw instances:" "发现多个 OpenClaw 实例：")
    for ($i = 0; $i -lt $instances.Count; $i++) {
      Write-Host "  $($i + 1)) $($instances[$i])"
    }
    Write-Host ""
    $choice = Prompt-OrDefault (T "Select instance number" "选择实例编号") "1"
    $idx = [int]$choice - 1
    if ($idx -ge 0 -and $idx -lt $instances.Count) {
      $script:OpenClawDir = $instances[$idx]
    } else {
      Warn (T "Invalid selection, using default" "无效选择，使用默认")
      $script:OpenClawDir = $instances[0]
    }
    $script:PluginDest = Join-Path $script:OpenClawDir "extensions\memory-openviking"
  }
}

function Select-Mode {
  if ($Yes) {
    $script:SelectedMode = "local"
    return
  }
  $mode = Prompt-OrDefault (T "Plugin mode (local/remote)" "插件模式 (local/remote)") "local"
  if ($mode -eq "remote") {
    $script:SelectedMode = "remote"
  } else {
    $script:SelectedMode = "local"
  }
}

function Collect-RemoteConfig {
  $script:RemoteBaseUrl = "http://127.0.0.1:1933"
  $script:RemoteApiKey = ""
  $script:RemoteAgentId = ""
  if (-not $Yes) {
    $script:RemoteBaseUrl = Prompt-OrDefault (T "OpenViking server URL" "OpenViking 服务器地址") $script:RemoteBaseUrl
    $script:RemoteApiKey = Prompt-Optional (T "API Key (optional)" "API Key（可选）")
    $script:RemoteAgentId = Prompt-Optional (T "Agent ID (optional)" "Agent ID（可选）")
  }
}

function Configure-OvConf {
  New-Item -ItemType Directory -Force -Path $OpenVikingDir | Out-Null

  $workspace = Join-Path $OpenVikingDir "data"
  $serverPort = "$DefaultServerPort"
  $agfsPort = "$DefaultAgfsPort"
  $vlmModel = $DefaultVlmModel
  $embeddingModel = $DefaultEmbeddingModel

  $legacyKey = if ($env:OPENVIKING_ARK_API_KEY) { $env:OPENVIKING_ARK_API_KEY } else { "" }
  $vlmApiKey = if ($env:OPENVIKING_VLM_API_KEY) { $env:OPENVIKING_VLM_API_KEY } else { $legacyKey }
  $embeddingApiKey = if ($env:OPENVIKING_EMBEDDING_API_KEY) { $env:OPENVIKING_EMBEDDING_API_KEY } else { $legacyKey }

  if (-not $Yes) {
    Write-Host ""
    $workspace = Prompt-OrDefault (T "OpenViking workspace path" "OpenViking 数据目录") $workspace
    $serverPort = Prompt-OrDefault (T "OpenViking HTTP port" "OpenViking HTTP 端口") $serverPort
    $agfsPort = Prompt-OrDefault (T "AGFS port" "AGFS 端口") $agfsPort
    $vlmModel = Prompt-OrDefault (T "VLM model" "VLM 模型") $vlmModel
    $embeddingModel = Prompt-OrDefault (T "Embedding model" "Embedding 模型") $embeddingModel
    Write-Host (T "VLM and Embedding API keys can differ. You can leave either empty and edit ov.conf later." "说明：VLM 与 Embedding 的 API Key 可能不同，可分别填写；留空后续可在 ov.conf 修改。")
    $vlmInput = Prompt-Optional (T "VLM API key (optional)" "VLM API Key（可留空）")
    $embInput = Prompt-Optional (T "Embedding API key (optional)" "Embedding API Key（可留空）")
    if ($vlmInput) { $vlmApiKey = $vlmInput }
    if ($embInput) { $embeddingApiKey = $embInput }
  }

  New-Item -ItemType Directory -Force -Path $workspace | Out-Null

  $cfg = @{
    server = @{
      host = "127.0.0.1"
      port = [int]$serverPort
      root_api_key = $null
      cors_origins = @("*")
    }
    storage = @{
      workspace = $workspace
      vectordb = @{ name = "context"; backend = "local"; project = "default" }
      agfs = @{ port = [int]$agfsPort; log_level = "warn"; backend = "local"; timeout = 10; retry_times = 3 }
    }
    embedding = @{
      dense = @{
        provider = "volcengine"
        api_key = $(if ($embeddingApiKey) { $embeddingApiKey } else { $null })
        model = $embeddingModel
        api_base = "https://ark.cn-beijing.volces.com/api/v3"
        dimension = 1024
        input = "multimodal"
      }
    }
    log = @{
      level = "WARNING"
      format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
      output = "file"
      rotation = $true
      rotation_days = 3
      rotation_interval = "midnight"
    }
    vlm = @{
      provider = "volcengine"
      api_key = $(if ($vlmApiKey) { $vlmApiKey } else { $null })
      model = $vlmModel
      api_base = "https://ark.cn-beijing.volces.com/api/v3"
      temperature = 0.1
      max_retries = 3
    }
  }

  $confPath = Join-Path $OpenVikingDir "ov.conf"
  $cfgJson = $cfg | ConvertTo-Json -Depth 10
  Write-Utf8NoBom -Path $confPath -Content $cfgJson
  Info ("{0} {1}" -f (T "Config generated:" "已生成配置:"), $confPath)
  return [int]$serverPort
}

function Download-Plugin {
  $rawBase = "https://raw.githubusercontent.com/$Repo/$Branch"
  $files = @(
    "examples/openclaw-memory-plugin/index.ts",
    "examples/openclaw-memory-plugin/config.ts",
    "examples/openclaw-memory-plugin/client.ts",
    "examples/openclaw-memory-plugin/process-manager.ts",
    "examples/openclaw-memory-plugin/memory-ranking.ts",
    "examples/openclaw-memory-plugin/text-utils.ts",
    "examples/openclaw-memory-plugin/openclaw.plugin.json",
    "examples/openclaw-memory-plugin/package.json",
    "examples/openclaw-memory-plugin/package-lock.json",
    "examples/openclaw-memory-plugin/tsconfig.json",
    "examples/openclaw-memory-plugin/.gitignore"
  )

  New-Item -ItemType Directory -Force -Path $PluginDest | Out-Null
  Info (T "Downloading memory-openviking plugin..." "正在下载 memory-openviking 插件...")
  Info ("{0} $Repo@$Branch" -f (T "Plugin source:" "插件来源:"))

  foreach ($rel in $files) {
    $name = Split-Path $rel -Leaf
    $url = "$rawBase/$rel"
    $dst = Join-Path $PluginDest $name
    try {
      Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing | Out-Null
    } catch {
      Err ("{0} $url" -f (T "Download failed:" "下载失败:"))
      throw
    }
  }

  Push-Location $PluginDest
  try {
    npm install --no-audit --no-fund | Out-Host
  } finally {
    Pop-Location
  }
  Info ("{0} $PluginDest" -f (T "Plugin deployed:" "插件部署完成:"))
}

function Configure-OpenClawPlugin {
  param([int]$ServerPort)
  Info (T "Configuring OpenClaw plugin..." "正在配置 OpenClaw 插件...")

  $cfgPath = Join-Path $OpenClawDir "openclaw.json"
  $cfg = @{}
  if (Test-Path $cfgPath) {
    try {
      $raw = Get-Content -Raw -Path $cfgPath
      if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $obj = $raw | ConvertFrom-Json -AsHashtable
        if ($obj) { $cfg = $obj }
      }
    } catch {
      Warn (T "Existing openclaw.json is invalid. Rebuilding required sections." "检测到已有 openclaw.json 非法，将重建相关配置节点。")
    }
  }

  if (-not $cfg.ContainsKey("plugins")) { $cfg["plugins"] = @{} }
  if (-not $cfg.ContainsKey("gateway")) { $cfg["gateway"] = @{} }
  if (-not $cfg["plugins"].ContainsKey("slots")) { $cfg["plugins"]["slots"] = @{} }
  if (-not $cfg["plugins"].ContainsKey("load")) { $cfg["plugins"]["load"] = @{} }
  if (-not $cfg["plugins"].ContainsKey("entries")) { $cfg["plugins"]["entries"] = @{} }

  # Keep plugin load paths unique.
  $existingPaths = @()
  if ($cfg["plugins"]["load"].ContainsKey("paths") -and $cfg["plugins"]["load"]["paths"]) {
    $existingPaths = @($cfg["plugins"]["load"]["paths"])
  }
  $mergedPaths = @($existingPaths + @($PluginDest) | Select-Object -Unique)

  $cfg["plugins"]["enabled"] = $true
  $existingAllow = @()
  if ($cfg["plugins"].ContainsKey("allow") -and $cfg["plugins"]["allow"]) {
    $existingAllow = @($cfg["plugins"]["allow"])
  }
  if ($existingAllow -notcontains "memory-openviking") {
    $existingAllow += "memory-openviking"
  }
  $cfg["plugins"]["allow"] = $existingAllow
  $cfg["plugins"]["slots"]["memory"] = "memory-openviking"
  $cfg["plugins"]["load"]["paths"] = $mergedPaths

  $pluginConfig = @{
    mode = $SelectedMode
    targetUri = "viking://user/memories"
    autoRecall = $true
    autoCapture = $true
  }

  if ($SelectedMode -eq "local") {
    $ovConfPath = Join-Path $OpenVikingDir "ov.conf"
    $pluginConfig["configPath"] = $ovConfPath
    $pluginConfig["port"] = $ServerPort
  } else {
    $pluginConfig["baseUrl"] = $RemoteBaseUrl
    if ($RemoteApiKey) { $pluginConfig["apiKey"] = $RemoteApiKey }
    if ($RemoteAgentId) { $pluginConfig["agentId"] = $RemoteAgentId }
  }

  $cfg["plugins"]["entries"]["memory-openviking"] = @{ config = $pluginConfig }
  $cfg["gateway"]["mode"] = "local"

  $cfgJson = $cfg | ConvertTo-Json -Depth 20
  Write-Utf8NoBom -Path $cfgPath -Content $cfgJson

  Info (T "OpenClaw plugin configured" "OpenClaw 插件配置完成")
}

function Write-OpenVikingEnv {
  $pyCmd = Get-PythonCommand
  $pyPath = ""
  if ($pyCmd) {
    $g = Get-Command $pyCmd -ErrorAction SilentlyContinue
    if ($g) { $pyPath = $g.Source }
  }

  New-Item -ItemType Directory -Force -Path $OpenClawDir | Out-Null
  $envPath = Join-Path $OpenClawDir "openviking.env.ps1"
  $envContent = '$env:OPENVIKING_PYTHON = "' + $pyPath + '"'
  Write-Utf8NoBom -Path $envPath -Content $envContent

  Info ("{0} $envPath" -f (T "Environment file generated:" "已生成环境文件:"))
}

Title (T "🦣 OpenClaw + OpenViking Installer" "🦣 OpenClaw + OpenViking 一键安装")
Write-Host ""

Select-Workdir
Info ("{0} $OpenClawDir" -f (T "Target:" "目标实例:"))

Select-Mode
Info ("{0} $SelectedMode" -f (T "Mode:" "模式:"))

$serverPort = $DefaultServerPort
if ($SelectedMode -eq "local") {
  Validate-Environment
  Check-OpenClaw
  Install-OpenViking
  $serverPort = Configure-OvConf
} else {
  Check-OpenClaw
  Collect-RemoteConfig
}

Download-Plugin
Configure-OpenClawPlugin -ServerPort $serverPort

if ($SelectedMode -eq "local") {
  Write-OpenVikingEnv
}

Write-Host ""
Title "═══════════════════════════════════════════════════════════"
Title ("  {0}" -f (T "Installation complete!" "安装完成！"))
Title "═══════════════════════════════════════════════════════════"
Write-Host ""

if ($SelectedMode -eq "local") {
  Info (T "Run these commands to start OpenClaw + OpenViking:" "请按以下命令启动 OpenClaw + OpenViking：")
  Write-Host "  1) openclaw --version"
  Write-Host "  2) openclaw onboard"
  Write-Host "  3) . `"$OpenClawDir\openviking.env.ps1`"; openclaw gateway"
  Write-Host "  4) openclaw status"
  Write-Host ""
  Info ("{0} $OpenVikingDir\ov.conf" -f (T "You can edit the config freely:" "你可以按需自由修改配置文件:"))
} else {
  Info (T "Run these commands to start OpenClaw:" "请按以下命令启动 OpenClaw：")
  Write-Host "  1) openclaw --version"
  Write-Host "  2) openclaw onboard"
  Write-Host "  3) openclaw gateway"
  Write-Host "  4) openclaw status"
  Write-Host ""
  Info ("{0} $RemoteBaseUrl" -f (T "Remote server:" "远程服务器:"))
}
Write-Host ""
