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
#   OPENVIKING_ARK_API_KEY        - legacy fallback for both keys
#

set -e

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
DEFAULT_VLM_MODEL="doubao-seed-1-8-251228"
DEFAULT_EMBED_MODEL="doubao-embedding-vision-250615"
SELECTED_SERVER_PORT="${DEFAULT_SERVER_PORT}"
LANG_UI="en"

# Parse args (supports curl | bash -s -- ...)
for arg in "$@"; do
  [[ "$arg" == "-y" || "$arg" == "--yes" ]] && INSTALL_YES="1"
  [[ "$arg" == "--zh" ]] && LANG_UI="zh"
  [[ "$arg" == "-h" || "$arg" == "--help" ]] && {
    echo "Usage: curl -fsSL <INSTALL_URL> | bash [-s -- -y --zh]"
    echo ""
    echo "Options:"
    echo "  -y, --yes   Non-interactive mode"
    echo "  --zh        Chinese prompts"
    echo "  -h, --help  Show this help"
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
  info "$(tr "Installing OpenViking from PyPI..." "正在安装 OpenViking (PyPI)...")"
  info "$(tr "Using pip index: ${PIP_INDEX_URL}" "使用 pip 镜像源: ${PIP_INDEX_URL}")"
  python3 -m pip install --upgrade pip -q -i "${PIP_INDEX_URL}"
  python3 -m pip install openviking -i "${PIP_INDEX_URL}" || {
    err "$(tr "OpenViking install failed. Check Python version (>=3.10) and pip." "OpenViking 安装失败，请检查 Python 版本 (需 >= 3.10) 及 pip")"
    exit 1
  }
  info "$(tr "OpenViking installed ✓" "OpenViking 安装完成 ✓")"
}

configure_openviking_conf() {
  mkdir -p "${OPENVIKING_DIR}"

  local workspace="${OPENVIKING_DIR}/data"
  local server_port="${DEFAULT_SERVER_PORT}"
  local agfs_port="${DEFAULT_AGFS_PORT}"
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
    read -r -p "$(tr "AGFS port [${agfs_port}]: " "AGFS 端口 [${agfs_port}]: ")" _agfs_port < /dev/tty || true
    read -r -p "$(tr "VLM model [${vlm_model}]: " "VLM 模型 [${vlm_model}]: ")" _vlm_model < /dev/tty || true
    read -r -p "$(tr "Embedding model [${embedding_model}]: " "Embedding 模型 [${embedding_model}]: ")" _embedding_model < /dev/tty || true
    echo "$(tr "VLM and Embedding API keys can differ. You can leave either empty and edit ov.conf later." "说明：VLM 与 Embedding 的 API Key 可能不同，可分别填写；留空后续可在 ov.conf 修改。")"
    read -r -p "$(tr "VLM API key (optional): " "VLM API Key（可留空）: ")" _vlm_api_key < /dev/tty || true
    read -r -p "$(tr "Embedding API key (optional): " "Embedding API Key（可留空）: ")" _embedding_api_key < /dev/tty || true

    workspace="${_workspace:-$workspace}"
    server_port="${_server_port:-$server_port}"
    agfs_port="${_agfs_port:-$agfs_port}"
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
    "agfs": { "port": ${agfs_port}, "log_level": "warn", "backend": "local", "timeout": 10, "retry_times": 3 }
  },
  "embedding": {
    "dense": {
      "backend": "volcengine",
      "api_key": ${embedding_api_json},
      "model": "${embedding_model}",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "backend": "volcengine",
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
    "examples/openclaw-memory-plugin/openclaw.plugin.json"
    "examples/openclaw-memory-plugin/package.json"
    "examples/openclaw-memory-plugin/package-lock.json"
    "examples/openclaw-memory-plugin/.gitignore"
  )

  mkdir -p "${PLUGIN_DEST}"
  info "$(tr "Downloading memory-openviking plugin..." "正在下载 memory-openviking 插件...")"
  info "$(tr "Plugin source: ${REPO}@${BRANCH}" "插件来源: ${REPO}@${BRANCH}")"
  for rel in "${files[@]}"; do
    local name="${rel##*/}"
    local url="${gh_raw}/${rel}"
    curl -fsSL -o "${PLUGIN_DEST}/${name}" "${url}" || {
      err "$(tr "Download failed: ${url}" "下载失败: ${url}")"
      exit 1
    }
  done
  (cd "${PLUGIN_DEST}" && npm install --no-audit --no-fund) || {
    err "$(tr "Plugin dependency install failed: ${PLUGIN_DEST}" "插件依赖安装失败: ${PLUGIN_DEST}")"
    exit 1
  }
  info "$(tr "Plugin deployed: ${PLUGIN_DEST}" "插件部署完成: ${PLUGIN_DEST}")"
}

configure_openclaw_plugin() {
  local server_port="${SELECTED_SERVER_PORT}"
  local config_path="~/.openviking/ov.conf"
  info "$(tr "Configuring OpenClaw plugin..." "正在配置 OpenClaw 插件...")"

  openclaw config set plugins.enabled true
  openclaw config set plugins.allow '["memory-openviking"]' --json
  openclaw config set gateway.mode local
  openclaw config set plugins.slots.memory memory-openviking
  openclaw config set plugins.load.paths "[\"${PLUGIN_DEST}\"]" --json
  openclaw config set plugins.entries.memory-openviking.config.mode local
  openclaw config set plugins.entries.memory-openviking.config.configPath "${config_path}"
  openclaw config set plugins.entries.memory-openviking.config.port "${server_port}"
  openclaw config set plugins.entries.memory-openviking.config.targetUri viking://
  openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
  openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json
  info "$(tr "OpenClaw plugin configured" "OpenClaw 插件配置完成")"
}

write_openviking_env() {
  local py_path
  py_path="$(command -v python3 || command -v python || true)"
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
  validate_environment

  install_openclaw
  install_openviking
  configure_openviking_conf
  download_plugin
  configure_openclaw_plugin
  write_openviking_env

  echo ""
  bold "═══════════════════════════════════════════════════════════"
  bold "  $(tr "Installation complete!" "安装完成！")"
  bold "═══════════════════════════════════════════════════════════"
  echo ""
  info "$(tr "Run these commands to start OpenClaw + OpenViking:" "请按以下命令启动 OpenClaw + OpenViking：")"
  echo "  1) openclaw --version"
  echo "  2) openclaw onboard"
  echo "  3) source ${OPENCLAW_DIR}/openviking.env && openclaw gateway"
  echo "  4) openclaw status"
  echo ""
  info "$(tr "You can edit the config freely: ${OPENVIKING_DIR}/ov.conf" "你可以按需自由修改配置文件: ${OPENVIKING_DIR}/ov.conf")"
  echo ""
}

main "$@"
