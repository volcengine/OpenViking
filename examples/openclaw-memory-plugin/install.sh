#!/bin/bash
#
# OpenClaw + OpenViking one-click installer
# Usage: curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-memory-plugin/install.sh | bash
#
# Environment variables:
#   REPO=owner/repo               - GitHub repository (default: volcengine/OpenViking)
#   BRANCH=branch                 - Git branch/tag/commit (default: main)
#   OPENVIKING_INSTALL_YES=1      - non-interactive mode (same as -y)
#   SKIP_OPENCLAW=1               - skip OpenClaw check
#   SKIP_OPENVIKING=1             - skip OpenViking installation
#   NPM_REGISTRY=url              - npm registry (default: https://registry.npmmirror.com)
#   PIP_INDEX_URL=url             - pip index URL (default: https://pypi.tuna.tsinghua.edu.cn/simple)
#   OPENVIKING_VLM_API_KEY        - VLM model API key (optional)
#   OPENVIKING_EMBEDDING_API_KEY  - Embedding model API key (optional)
#   OPENVIKING_ARK_API_KEY       - legacy fallback for both keys
#   OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1 - if venv unavailable (PEP 668 only), allow pip --break-system-packages (opt-in, default off)
#   GET_PIP_URL=url                 - URL for get-pip.py when using venv --without-pip (default: auto)
#
# On Debian/Ubuntu (PEP 668), the script installs OpenViking into a venv at
# ~/.openviking/venv to avoid "externally-managed-environment" errors.
#

set -e

# Set by install_openviking when using venv (e.g. on Debian/Ubuntu); used by write_openviking_env
OPENVIKING_PYTHON_PATH=""

REPO="${REPO:-volcengine/OpenViking}"
BRANCH="${BRANCH:-main}"
INSTALL_YES="${OPENVIKING_INSTALL_YES:-0}"
SKIP_OC="${SKIP_OPENCLAW:-0}"
SKIP_OV="${SKIP_OPENVIKING:-0}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
HOME_DIR="${HOME:-$USERPROFILE}"
OPENCLAW_DIR="${HOME_DIR}/.openclaw"
OPENVIKING_DIR="${HOME_DIR}/.openviking"
PLUGIN_DEST="${OPENCLAW_DIR}/extensions/memory-openviking"
DEFAULT_SERVER_PORT=1933
DEFAULT_AGFS_PORT=1833
DEFAULT_VLM_MODEL="doubao-seed-2-0-pro-260215"
DEFAULT_EMBED_MODEL="doubao-embedding-vision-251215"
SELECTED_SERVER_PORT="${DEFAULT_SERVER_PORT}"
SELECTED_MODE="local"
LANG_UI="en"

# Parse args (supports curl | bash -s -- ...)
_expect_workdir=""
for arg in "$@"; do
  if [[ -n "$_expect_workdir" ]]; then
    OPENCLAW_DIR="$arg"
    PLUGIN_DEST="${OPENCLAW_DIR}/extensions/memory-openviking"
    _expect_workdir=""
    continue
  fi
  [[ "$arg" == "-y" || "$arg" == "--yes" ]] && INSTALL_YES="1"
  [[ "$arg" == "--zh" ]] && LANG_UI="zh"
  [[ "$arg" == "--workdir" ]] && { _expect_workdir="1"; continue; }
  [[ "$arg" == "-h" || "$arg" == "--help" ]] && {
    echo "Usage: curl -fsSL <INSTALL_URL> | bash [-s -- -y --zh --workdir <path>]"
    echo ""
    echo "Options:"
    echo "  -y, --yes            Non-interactive mode"
    echo "  --zh                 Chinese prompts"
    echo "  --workdir <path>     OpenClaw config directory (default: ~/.openclaw)"
    echo "  -h, --help           Show this help"
    echo ""
    echo "Env vars: REPO, BRANCH, OPENVIKING_INSTALL_YES, SKIP_OPENCLAW, SKIP_OPENVIKING, NPM_REGISTRY, PIP_INDEX_URL"
    exit 0
  }
done

tr() {
  local en="$1"
  local zh="$2"
  if [[ "$LANG_UI" == "zh" ]]; then
    echo "$zh"
  else
    echo "$en"
  fi
}

# Prefer interactive mode. Even with curl | bash, try reading from /dev/tty.
# Fall back to defaults only when no interactive TTY is available.
if [[ ! -t 0 && "$INSTALL_YES" != "1" ]]; then
  if [[ ! -r /dev/tty ]]; then
    INSTALL_YES="1"
    echo "[WARN] $(tr "No interactive TTY detected. Falling back to defaults (-y)." "未检测到可交互终端，自动切换为默认配置模式（等同于 -y）")"
  else
    echo "[INFO] $(tr "Pipeline execution detected. Interactive prompts will use /dev/tty." "检测到管道执行，将通过 /dev/tty 进入交互配置")"
  fi
fi

# 颜色与输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }
bold()  { echo -e "${BOLD}$1${NC}"; }

# Detect OS
detect_os() {
  case "$(uname -s)" in
    Linux*)   OS="linux";;
    Darwin*)  OS="macos";;
    CYGWIN*|MINGW*|MSYS*) OS="windows";;
    *)        OS="unknown";;
  esac
  if [[ "$OS" == "windows" ]]; then
    err "$(tr "Windows is not supported by this installer yet. Please follow the docs for manual setup." "Windows 暂不支持此一键安装脚本，请参考文档手动安装。")"
    exit 1
  fi
}

# Detect Linux distro
detect_distro() {
  DISTRO="unknown"
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release 2>/dev/null || true
    case "${ID:-}" in
      ubuntu|debian|linuxmint) DISTRO="debian";;
      fedora|rhel|centos|rocky|almalinux|openeuler) DISTRO="rhel";;
    esac
  fi
  if command -v apt &>/dev/null; then
    DISTRO="debian"
  elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
    DISTRO="rhel"
  fi
}

# ─── Workdir detection & mode selection ───

detect_openclaw_instances() {
  local instances=()
  for dir in "${HOME_DIR}"/.openclaw*; do
    [[ -d "$dir" ]] || continue
    # skip workspace/data subdirectories
    [[ "$(basename "$dir")" == .openclaw-* ]] || [[ "$(basename "$dir")" == ".openclaw" ]] || continue
    instances+=("$dir")
  done
  echo "${instances[@]}"
}

select_workdir() {
  # Already set via --workdir
  [[ -n "$OPENCLAW_DIR" && "$OPENCLAW_DIR" != "${HOME_DIR}/.openclaw" ]] && return 0

  local instances=($(detect_openclaw_instances))

  # Only default instance or none — keep default
  if [[ ${#instances[@]} -le 1 ]]; then
    return 0
  fi

  # Multiple instances found — let user pick
  if [[ "$INSTALL_YES" != "1" ]]; then
    echo ""
    bold "$(tr "Found multiple OpenClaw instances:" "发现多个 OpenClaw 实例：")"
    local i=1
    for inst in "${instances[@]}"; do
      echo "  ${i}) ${inst}"
      i=$((i + 1))
    done
    echo ""
    read -r -p "$(tr "Select instance number [1]: " "选择实例编号 [1]: ")" _choice < /dev/tty || true
    if [[ -n "$_choice" && "$_choice" =~ ^[0-9]+$ ]]; then
      local idx=$((_choice - 1))
      if [[ $idx -ge 0 && $idx -lt ${#instances[@]} ]]; then
        OPENCLAW_DIR="${instances[$idx]}"
      else
        warn "$(tr "Invalid selection, using default" "无效选择，使用默认")"
        OPENCLAW_DIR="${instances[0]}"
      fi
    else
      OPENCLAW_DIR="${instances[0]}"
    fi
    PLUGIN_DEST="${OPENCLAW_DIR}/extensions/memory-openviking"
  fi
}

select_mode() {
  if [[ "$INSTALL_YES" == "1" ]]; then
    SELECTED_MODE="local"
    return 0
  fi
  echo ""
  read -r -p "$(tr "Plugin mode - local or remote [local]: " "插件模式 - local 或 remote [local]: ")" _mode < /dev/tty || true
  _mode="${_mode:-local}"
  if [[ "$_mode" == "remote" ]]; then
    SELECTED_MODE="remote"
  else
    SELECTED_MODE="local"
  fi
}

collect_remote_config() {
  remote_base_url="http://127.0.0.1:1933"
  remote_api_key=""
  remote_agent_id=""
  if [[ "$INSTALL_YES" != "1" ]]; then
    read -r -p "$(tr "OpenViking server URL [${remote_base_url}]: " "OpenViking 服务器地址 [${remote_base_url}]: ")" _base_url < /dev/tty || true
    read -r -p "$(tr "API Key (optional): " "API Key（可选）: ")" _api_key < /dev/tty || true
    read -r -p "$(tr "Agent ID (optional): " "Agent ID（可选）: ")" _agent_id < /dev/tty || true
    remote_base_url="${_base_url:-$remote_base_url}"
    remote_api_key="${_api_key:-}"
    remote_agent_id="${_agent_id:-}"
  fi
}

# ─── Environment checks ───

check_python() {
  local py="${OPENVIKING_PYTHON:-python3}"
  local out
  if ! out=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null); then
    echo "fail|$py|$(tr "Python not found. Install Python >= 3.10." "Python 未找到，请安装 Python >= 3.10")"
    return 1
  fi
  local major minor
  IFS=. read -r major minor <<< "$out"
  if [[ "$major" -lt 3 ]] || [[ "$major" -eq 3 && "$minor" -lt 10 ]]; then
    echo "fail|$out|$(tr "Python $out is too old. Need >= 3.10." "Python 版本 $out 过低，需要 >= 3.10")"
    return 1
  fi
  echo "ok|$out|$py"
  return 0
}

check_node() {
  local out
  if ! out=$(node -v 2>/dev/null); then
    echo "fail||$(tr "Node.js not found. Install Node.js >= 22." "Node.js 未找到，请安装 Node.js >= 22")"
    return 1
  fi
  local v="${out#v}"
  local major
  major="${v%%.*}"
  if [[ -z "$major" ]] || [[ "$major" -lt 22 ]]; then
    echo "fail|$out|$(tr "Node.js $out is too old. Need >= 22." "Node.js 版本 $out 过低，需要 >= 22")"
    return 1
  fi
  echo "ok|$out|node"
  return 0
}

# Print guidance for missing dependencies
print_install_hints() {
  local missing=("$@")
  bold "\n═══════════════════════════════════════════════════════════"
  bold "  $(tr "Environment check failed. Install missing dependencies first:" "环境校验未通过，请先安装以下缺失组件：")"
  bold "═══════════════════════════════════════════════════════════\n"

  for item in "${missing[@]}"; do
    local name="${item%%|*}"
    local rest="${item#*|}"
    err "$(tr "Missing: $name" "缺失: $name")"
    [[ -n "$rest" ]] && echo "  $rest"
    echo ""
  done

  detect_distro
  echo "$(tr "Based on your system ($DISTRO), you can run:" "根据你的系统 ($DISTRO)，可执行以下命令安装：")"
  echo ""

  if printf '%s\n' "${missing[@]}" | grep -q "Python"; then
    echo "  # $(tr "Install Python 3.10+ (pyenv recommended)" "安装 Python 3.10+（推荐 pyenv）")"
    echo "  curl https://pyenv.run | bash"
    echo "  export PATH=\"\$HOME/.pyenv/bin:\$PATH\""
    echo "  eval \"\$(pyenv init -)\""
    echo "  pyenv install 3.11.12"
    echo "  pyenv global 3.11.12"
    echo "  python3 --version    # $(tr "verify >= 3.10" "确认 >= 3.10")"
    echo ""
  fi

  if printf '%s\n' "${missing[@]}" | grep -q "Node"; then
    echo "  # $(tr "Install Node.js 22+ (nvm)" "安装 Node.js 22+（nvm）")"
    echo "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
    echo "  source ~/.bashrc"
    echo "  nvm install 22"
    echo "  nvm use 22"
    echo "  node -v            # $(tr "verify >= v22" "确认 >= v22")"
    echo ""
  fi

  bold "$(tr "After installation, rerun this script." "安装完成后，请重新运行本脚本。")"
  bold "$(tr "See details: https://github.com/${REPO}/blob/${BRANCH}/examples/openclaw-memory-plugin/INSTALL.md" "详细说明见: https://github.com/${REPO}/blob/${BRANCH}/examples/openclaw-memory-plugin/INSTALL-ZH.md")"
  echo ""
  exit 1
}

# Validate environment
validate_environment() {
  info "$(tr "Checking OpenViking runtime environment..." "正在校验 OpenViking 运行环境...")"
  echo ""

  local missing=()
  local r

  r=$(check_python) || missing+=("Python 3.10+ | $(echo "$r" | cut -d'|' -f3)")
  if [[ "${r%%|*}" == "ok" ]]; then
    info "  Python: $(echo "$r" | cut -d'|' -f2) ✓"
  fi

  r=$(check_node) || missing+=("Node.js 22+ | $(echo "$r" | cut -d'|' -f3)")
  if [[ "${r%%|*}" == "ok" ]]; then
    info "  Node.js: $(echo "$r" | cut -d'|' -f2) ✓"
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo ""
    print_install_hints "${missing[@]}"
  fi

  echo ""
  info "$(tr "Environment check passed ✓" "环境校验通过 ✓")"
  echo ""
}

# ─── Install flow ───

install_openclaw() {
  if [[ "$SKIP_OC" == "1" ]]; then
    info "$(tr "Skipping OpenClaw check (SKIP_OPENCLAW=1)" "跳过 OpenClaw 校验 (SKIP_OPENCLAW=1)")"
    return 0
  fi
  info "$(tr "Checking OpenClaw..." "正在校验 OpenClaw...")"
  if command -v openclaw >/dev/null 2>&1; then
    info "$(tr "OpenClaw detected ✓" "OpenClaw 已安装 ✓")"
    return 0
  fi

  err "$(tr "OpenClaw not found. Install it manually, then rerun this script." "未检测到 OpenClaw，请先手动安装后再执行本脚本")"
  echo ""
  echo "$(tr "Recommended command:" "推荐命令：")"
  echo "  npm install -g openclaw --registry ${NPM_REGISTRY}"
  echo ""
  echo "$(tr "If npm global install fails, install Node via nvm and retry." "如 npm 全局安装失败，建议先用 nvm 安装 Node 后再执行上述命令。")"
  echo "$(tr "After installation, run:" "安装完成后，运行：")"
  echo "  openclaw --version"
  echo "  openclaw onboard"
  echo ""
  exit 1
}

install_openviking() {
  if [[ "$SKIP_OV" == "1" ]]; then
    info "$(tr "Skipping OpenViking install (SKIP_OPENVIKING=1)" "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)")"
    return 0
  fi
  local py="${OPENVIKING_PYTHON:-python3}"
  info "$(tr "Installing OpenViking from PyPI..." "正在安装 OpenViking (PyPI)...")"
  info "$(tr "Using pip index: ${PIP_INDEX_URL}" "使用 pip 镜像源: ${PIP_INDEX_URL}")"

  # Try system-wide pip first (works on many systems)
  local err_out
  err_out=$("$py" -m pip install --upgrade pip -q -i "${PIP_INDEX_URL}" 2>&1) || true
  if err_out=$("$py" -m pip install openviking -i "${PIP_INDEX_URL}" 2>&1); then
    OPENVIKING_PYTHON_PATH="$(command -v "$py" || true)"
    [[ -z "$OPENVIKING_PYTHON_PATH" ]] && OPENVIKING_PYTHON_PATH="$py"
    info "$(tr "OpenViking installed ✓" "OpenViking 安装完成 ✓")"
    return 0
  fi

  # When system has no pip, or PEP 668 (Debian/Ubuntu): use a venv
  if echo "$err_out" | grep -q "externally-managed-environment\|externally managed\|No module named pip"; then
    if echo "$err_out" | grep -q "No module named pip"; then
      info "$(tr "System Python has no pip. Using a venv at ~/.openviking/venv" "系统 Python 未安装 pip，将使用 ~/.openviking/venv 虚拟环境")"
    else
      # Opt-in: allow install with --break-system-packages when venv is not available (PEP 668 only, default off)
      if [[ "${OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES}" == "1" ]]; then
        info "$(tr "Installing OpenViking with --break-system-packages (OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1)" "正在以 --break-system-packages 安装 OpenViking（已设置 OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1）")"
        if "$py" -m pip install --break-system-packages openviking -i "${PIP_INDEX_URL}"; then
          OPENVIKING_PYTHON_PATH="$(command -v "$py" || true)"
          [[ -z "$OPENVIKING_PYTHON_PATH" ]] && OPENVIKING_PYTHON_PATH="$py"
          info "$(tr "OpenViking installed ✓ (system)" "OpenViking 安装完成 ✓（系统）")"
          return 0
        fi
      fi
      info "$(tr "System Python is externally managed (PEP 668). Using a venv at ~/.openviking/venv" "检测到系统 Python 受管 (PEP 668)，将使用 ~/.openviking/venv 虚拟环境")"
    fi
    mkdir -p "${OPENVIKING_DIR}"
    local venv_dir="${OPENVIKING_DIR}/venv"
    local venv_py="${venv_dir}/bin/python"

    # Reuse existing venv if it has openviking (avoid repeated create on re-run)
    if [[ -x "${venv_py}" ]] && "${venv_py}" -c "import openviking" 2>/dev/null; then
      info "$(tr "Using existing venv with openviking: ${venv_dir}" "复用已有虚拟环境（已装 openviking）: ${venv_dir}")"
      "${venv_py}" -m pip install -q -U openviking -i "${PIP_INDEX_URL}" 2>/dev/null || true
      OPENVIKING_PYTHON_PATH="${venv_dir}/bin/python"
      info "$(tr "OpenViking installed ✓ (venv)" "OpenViking 安装完成 ✓（虚拟环境）")"
      return 0
    fi

    local venv_ok=0
    # Try 1: stdlib venv with ensurepip (needs python3-venv); errors suppressed to avoid confusing "ensurepip not available" message
    if "$py" -m venv "${venv_dir}" 2>/dev/null; then
      venv_ok=1
    fi

    # Try 2: venv --without-pip then bootstrap pip via get-pip.py (no ensurepip needed; works when Try 1 fails)
    if [[ "$venv_ok" -eq 0 ]]; then
      rm -rf "${venv_dir}" 2>/dev/null || true
      info "$(tr "Creating venv without system pip, then installing pip..." "正在创建无系统 pip 的虚拟环境并安装 pip...")"
      if "$py" -m venv --without-pip "${venv_dir}" 2>/dev/null; then
        info "$(tr "Venv created without pip; bootstrapping pip (using index: ${PIP_INDEX_URL})..." "已创建无 pip 的虚拟环境，正在安装 pip（使用镜像: ${PIP_INDEX_URL}）...")"
        local get_pip get_pip_url
        get_pip=$(mktemp -t get-pip.XXXXXX.py 2>/dev/null || echo "/tmp/get-pip.py")
        # Prefer mirror for get-pip.py when PIP_INDEX_URL is in China to avoid slow/timeout
        if [[ -n "${GET_PIP_URL}" ]]; then
          get_pip_url="${GET_PIP_URL}"
        elif echo "${PIP_INDEX_URL}" | grep -q "tuna.tsinghua\|pypi.tuna"; then
          get_pip_url="https://mirrors.tuna.tsinghua.edu.cn/pypi/web/static/get-pip.py"
        else
          get_pip_url="https://bootstrap.pypa.io/get-pip.py"
        fi
        if ! curl -fsSL --connect-timeout 15 --max-time 120 "${get_pip_url}" -o "${get_pip}" 2>/dev/null; then
          if [[ "${get_pip_url}" != "https://bootstrap.pypa.io/get-pip.py" ]]; then
            curl -fsSL --connect-timeout 15 --max-time 120 "https://bootstrap.pypa.io/get-pip.py" -o "${get_pip}" 2>/dev/null || true
          fi
        fi
        if [[ -s "${get_pip}" ]] && PIP_INDEX_URL="${PIP_INDEX_URL}" "$venv_py" "${get_pip}" -q 2>/dev/null; then
          venv_ok=1
        fi
        rm -f "${get_pip}" 2>/dev/null || true
      fi
    fi

    # Try 3: virtualenv (if already installed or installable with --user)
    if [[ "$venv_ok" -eq 0 ]]; then
      rm -rf "${venv_dir}" 2>/dev/null || true
      if "$py" -m virtualenv "${venv_dir}" 2>/dev/null; then
        venv_ok=1
      elif "$py" -m pip install --user virtualenv -i "${PIP_INDEX_URL}" -q 2>/dev/null && "$py" -m virtualenv "${venv_dir}" 2>/dev/null; then
        venv_ok=1
      fi
    fi

    if [[ "$venv_ok" -eq 0 ]]; then
      rm -rf "${venv_dir}" 2>/dev/null || true
      local py_ver
      py_ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3")
      err "$(tr "Could not create venv. Install venv then re-run:" "无法创建虚拟环境。请先安装 venv 后重新执行：")"
      echo "  sudo apt install python${py_ver}-venv   # or python3-full"
      echo ""
      echo "$(tr "Or (may conflict with system packages):" "或允许安装到系统（可能与系统包冲突）：")"
      echo "  OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1 curl -fsSL ... | bash"
      exit 1
    fi

    "$venv_py" -m pip install --upgrade pip -q -i "${PIP_INDEX_URL}"
    if ! "$venv_py" -m pip install openviking -i "${PIP_INDEX_URL}"; then
      err "$(tr "OpenViking install failed in venv." "在虚拟环境中安装 OpenViking 失败。")"
      exit 1
    fi
    OPENVIKING_PYTHON_PATH="${venv_dir}/bin/python"
    info "$(tr "OpenViking installed ✓ (venv)" "OpenViking 安装完成 ✓（虚拟环境）")"
    return 0
  fi

  err "$(tr "OpenViking install failed. Check Python version (>=3.10) and pip." "OpenViking 安装失败，请检查 Python 版本 (需 >= 3.10) 及 pip")"
  echo "$err_out" >&2
  exit 1
}

configure_openviking_conf() {
  mkdir -p "${OPENVIKING_DIR}"

  local workspace="${OPENVIKING_DIR}/data"
  local server_port="${DEFAULT_SERVER_PORT}"
  local vlm_model="${DEFAULT_VLM_MODEL}"
  local embedding_model="${DEFAULT_EMBED_MODEL}"
  local vlm_api_key="${OPENVIKING_VLM_API_KEY:-${OPENVIKING_ARK_API_KEY:-}}"
  local embedding_api_key="${OPENVIKING_EMBEDDING_API_KEY:-${OPENVIKING_ARK_API_KEY:-}}"
  local conf_path="${OPENVIKING_DIR}/ov.conf"
  local vlm_api_json="null"
  local embedding_api_json="null"

  if [[ "$INSTALL_YES" != "1" ]]; then
    echo ""
    read -r -p "$(tr "OpenViking workspace path [${workspace}]: " "OpenViking 数据目录 [${workspace}]: ")" _workspace < /dev/tty || true
    read -r -p "$(tr "OpenViking HTTP port [${server_port}]: " "OpenViking HTTP 端口 [${server_port}]: ")" _server_port < /dev/tty || true
    read -r -p "$(tr "VLM model [${vlm_model}]: " "VLM 模型 [${vlm_model}]: ")" _vlm_model < /dev/tty || true
    read -r -p "$(tr "Embedding model [${embedding_model}]: " "Embedding 模型 [${embedding_model}]: ")" _embedding_model < /dev/tty || true
    echo "$(tr "VLM and Embedding API keys can differ. You can leave either empty and edit ov.conf later." "说明：VLM 与 Embedding 的 API Key 可能不同，可分别填写；留空后续可在 ov.conf 修改。")"
    read -r -p "$(tr "VLM API key (optional): " "VLM API Key（可留空）: ")" _vlm_api_key < /dev/tty || true
    read -r -p "$(tr "Embedding API key (optional): " "Embedding API Key（可留空）: ")" _embedding_api_key < /dev/tty || true

    workspace="${_workspace:-$workspace}"
    server_port="${_server_port:-$server_port}"
    vlm_model="${_vlm_model:-$vlm_model}"
    embedding_model="${_embedding_model:-$embedding_model}"
    vlm_api_key="${_vlm_api_key:-$vlm_api_key}"
    embedding_api_key="${_embedding_api_key:-$embedding_api_key}"
  fi

  if [[ -n "${vlm_api_key}" ]]; then
    vlm_api_json="\"${vlm_api_key}\""
  fi
  if [[ -n "${embedding_api_key}" ]]; then
    embedding_api_json="\"${embedding_api_key}\""
  fi

  mkdir -p "${workspace}"
  cat > "${conf_path}" <<EOF
{
  "server": {
    "host": "127.0.0.1",
    "port": ${server_port},
    "root_api_key": null,
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "${workspace}",
    "vectordb": { "name": "context", "backend": "local", "project": "default" },
    "agfs": { "log_level": "warn", "backend": "local", "timeout": 10, "retry_times": 3 }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": ${embedding_api_json},
      "model": "${embedding_model}",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": ${vlm_api_json},
    "model": "${vlm_model}",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
EOF
  SELECTED_SERVER_PORT="${server_port}"
  info "$(tr "Config generated: ${conf_path}" "已生成配置: ${conf_path}")"
}

download_plugin() {
  local gh_raw="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
  local files=(
    "examples/openclaw-memory-plugin/index.ts"
    "examples/openclaw-memory-plugin/config.ts"
    "examples/openclaw-memory-plugin/client.ts"
    "examples/openclaw-memory-plugin/process-manager.ts"
    "examples/openclaw-memory-plugin/memory-ranking.ts"
    "examples/openclaw-memory-plugin/text-utils.ts"
    "examples/openclaw-memory-plugin/openclaw.plugin.json"
    "examples/openclaw-memory-plugin/package.json"
    "examples/openclaw-memory-plugin/package-lock.json"
    "examples/openclaw-memory-plugin/.gitignore"
  )
  local total=${#files[@]}
  local i=0

  mkdir -p "${PLUGIN_DEST}"
  info "$(tr "Downloading memory-openviking plugin from ${REPO}@${BRANCH} (${total} files)..." "正在从 ${REPO}@${BRANCH} 下载 memory-openviking 插件（共 ${total} 个文件）...")"
  local max_retries=3
  for rel in "${files[@]}"; do
    i=$((i + 1))
    local name="${rel##*/}"
    local url="${gh_raw}/${rel}"
    local ok=0
    echo -n "  [${i}/${total}] ${name} "
    local attempt=1
    while [[ "$attempt" -le "${max_retries}" ]]; do
      if curl -fsSL --connect-timeout 15 --max-time 120 -# -o "${PLUGIN_DEST}/${name}" "${url}" 2>/dev/null; then
        ok=1
        break
      fi
      [[ "$attempt" -lt "${max_retries}" ]] && sleep 2
      attempt=$((attempt + 1))
    done
    if [[ "$ok" -eq 1 ]]; then
      echo "✓"
    elif [[ "$name" == ".gitignore" ]]; then
      echo "$(tr "(retries failed, using minimal .gitignore)" "（重试失败，使用最小 .gitignore）")"
      echo "node_modules/" > "${PLUGIN_DEST}/${name}"
    else
      echo ""
      err "$(tr "Download failed after ${max_retries} retries: ${url}" "下载失败（已重试 ${max_retries} 次）: ${url}")"
      exit 1
    fi
  done
  info "$(tr "Installing plugin npm dependencies (may take 1-2 min, npm will show progress)..." "正在安装插件 npm 依赖（约 1–2 分钟，npm 会显示进度）...")"
  (cd "${PLUGIN_DEST}" && npm install --no-audit --no-fund) || {
    err "$(tr "Plugin dependency install failed: ${PLUGIN_DEST}" "插件依赖安装失败: ${PLUGIN_DEST}")"
    exit 1
  }
  info "$(tr "Plugin deployed: ${PLUGIN_DEST}" "插件部署完成: ${PLUGIN_DEST}")"
}

configure_openclaw_plugin() {
  info "$(tr "Configuring OpenClaw plugin..." "正在配置 OpenClaw 插件...")"

  local oc_env=()
  if [[ "$OPENCLAW_DIR" != "${HOME_DIR}/.openclaw" ]]; then
    oc_env=(env OPENCLAW_STATE_DIR="$OPENCLAW_DIR")
  fi

  "${oc_env[@]}" openclaw config set plugins.enabled true
  "${oc_env[@]}" openclaw config set plugins.allow '["memory-openviking"]' --json
  "${oc_env[@]}" openclaw config set gateway.mode local
  "${oc_env[@]}" openclaw config set plugins.slots.memory memory-openviking
  "${oc_env[@]}" openclaw config set plugins.load.paths "[\"${PLUGIN_DEST}\"]" --json
  "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.mode "${SELECTED_MODE}"
  "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.targetUri viking://user/memories
  "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
  "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json

  if [[ "$SELECTED_MODE" == "local" ]]; then
    local config_path="${OPENVIKING_DIR}/ov.conf"
    "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.configPath "${config_path}"
    "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.port "${SELECTED_SERVER_PORT}"
  else
    "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.baseUrl "${remote_base_url}"
    if [[ -n "${remote_api_key}" ]]; then
      "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.apiKey "${remote_api_key}"
    fi
    if [[ -n "${remote_agent_id}" ]]; then
      "${oc_env[@]}" openclaw config set plugins.entries.memory-openviking.config.agentId "${remote_agent_id}"
    fi
  fi

  info "$(tr "OpenClaw plugin configured" "OpenClaw 插件配置完成")"
}

write_openviking_env() {
  local py_path
  if [[ -n "${OPENVIKING_PYTHON_PATH}" ]]; then
    py_path="${OPENVIKING_PYTHON_PATH}"
  else
    py_path="$(command -v python3 || command -v python || true)"
  fi
  mkdir -p "${OPENCLAW_DIR}"
  cat > "${OPENCLAW_DIR}/openviking.env" <<EOF
export OPENVIKING_PYTHON='${py_path}'
EOF
  info "$(tr "Environment file generated: ${OPENCLAW_DIR}/openviking.env" "已生成环境文件: ${OPENCLAW_DIR}/openviking.env")"
}

# ─── 主流程 ───

main() {
  echo ""
  bold "$(tr "🦣 OpenClaw + OpenViking Installer" "🦣 OpenClaw + OpenViking 一键安装")"
  echo ""

  detect_os
  select_workdir
  info "$(tr "Target: ${OPENCLAW_DIR}" "目标实例: ${OPENCLAW_DIR}")"

  select_mode
  info "$(tr "Mode: ${SELECTED_MODE}" "模式: ${SELECTED_MODE}")"

  if [[ "$SELECTED_MODE" == "local" ]]; then
    validate_environment
    install_openclaw
    install_openviking
    configure_openviking_conf
  else
    install_openclaw
    collect_remote_config
  fi

  download_plugin
  configure_openclaw_plugin

  if [[ "$SELECTED_MODE" == "local" ]]; then
    write_openviking_env
  fi

  echo ""
  bold "═══════════════════════════════════════════════════════════"
  bold "  $(tr "Installation complete!" "安装完成！")"
  bold "═══════════════════════════════════════════════════════════"
  echo ""
  if [[ "$SELECTED_MODE" == "local" ]]; then
    info "$(tr "Run these commands to start OpenClaw + OpenViking:" "请按以下命令启动 OpenClaw + OpenViking：")"
    echo "  1) openclaw --version"
    echo "  2) openclaw onboard"
    echo "  3) source ${OPENCLAW_DIR}/openviking.env && openclaw gateway"
    echo "  4) openclaw status"
    echo ""
    info "$(tr "You can edit the config freely: ${OPENVIKING_DIR}/ov.conf" "你可以按需自由修改配置文件: ${OPENVIKING_DIR}/ov.conf")"
  else
    info "$(tr "Run these commands to start OpenClaw:" "请按以下命令启动 OpenClaw：")"
    echo "  1) openclaw --version"
    echo "  2) openclaw onboard"
    echo "  3) openclaw gateway"
    echo "  4) openclaw status"
    echo ""
    info "$(tr "Remote server: ${remote_base_url}" "远程服务器: ${remote_base_url}")"
  fi
  echo ""
}

main "$@"
