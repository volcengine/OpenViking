#!/usr/bin/env bash
#
# OpenViking Memory Plugins for GitHub Copilot — interactive installer.
#
# One-liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/copilot/setup-helper/install.sh)
#
# Steps (each is idempotent — re-running is safe):
#   1. Check OS (macOS / Linux only) and required baseline tools.
#   2. Set up ~/.openviking/ovcli.conf — reuse if present, prompt otherwise.
#   3. Clone (or refresh) the OpenViking repo to ~/.openviking/openviking-repo.
#   4. Optionally install the VS Code extension from a .vsix.
#   5. Install the Copilot CLI MCP server npm package and merge mcp.json.
#   6. Optionally append the copilot() shell-wrapper fallback.
#
# Env overrides:
#   OPENVIKING_HOME        default: $HOME/.openviking
#   OPENVIKING_REPO_DIR    default: $OPENVIKING_HOME/openviking-repo
#   OPENVIKING_REPO_URL    default: https://github.com/volcengine/OpenViking.git
#   OPENVIKING_REPO_BRANCH default: main
#   OPENVIKING_COPILOT_VSIX path to a prebuilt openviking-copilot .vsix
#   COPILOT_MCP_JSON       path to the Copilot CLI mcp.json to merge
#
# Targets bash 3.2+ (macOS /bin/bash) and Linux.

set -euo pipefail

OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$OV_HOME/openviking-repo}"
REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_BRANCH="${OPENVIKING_REPO_BRANCH:-main}"
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"
COPILOT_DIR="$REPO_DIR/examples/copilot"
VSCODE_EXT_DIR="$COPILOT_DIR/vscode-extension"
WRAPPER_SRC="$COPILOT_DIR/cli-plugin/wrapper/copilot.sh"
MARKER_BEGIN='# >>> openviking copilot memory plugin >>>'
MARKER_END='# <<< openviking copilot memory plugin <<<'

if [ -t 1 ]; then
  CYAN=$'\033[0;36m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  CYAN=''; GREEN=''; YELLOW=''; RED=''; BOLD=''; RESET=''
fi
info()    { printf '%s==>%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()    { printf '%s!!%s  %s\n' "$YELLOW" "$RESET" "$*"; }
err()     { printf '%sxx%s  %s\n' "$RED" "$RESET" "$*" >&2; }
ask()     { printf '%s??%s  %s' "$CYAN" "$RESET" "$*"; }
heading() { printf '\n%s%s%s\n' "$BOLD" "$*" "$RESET"; }

backup_file() {
  local file="$1"
  if [ -f "$file" ]; then
    local backup="$file.bak.$(date +%Y%m%d-%H%M%S)"
    cp -p "$file" "$backup"
    info "Backup: $backup"
  fi
}

prompt_yes_no() {
  local prompt="$1"
  local default_yes="$2"
  local reply=""
  if [ "$default_yes" = "1" ]; then
    ask "$prompt [Y/n] "
  else
    ask "$prompt [y/N] "
  fi
  read -r reply || reply=""
  case "$reply" in
    y|Y|yes|Yes|YES) return 0 ;;
    n|N|no|No|NO) return 1 ;;
    *) [ "$default_yes" = "1" ] ;;
  esac
}

# ----- 1. Environment check -----

heading '1. Environment check'

OS_NAME=$(uname -s)
case "$OS_NAME" in
  Darwin|Linux) info "OS: $OS_NAME" ;;
  *) err "Unsupported OS: $OS_NAME. Only macOS and Linux are supported."; exit 1 ;;
esac

missing=0
for cmd in git jq curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "$cmd not found. Please install it and re-run."
    missing=1
  fi
done
[ "$missing" -eq 1 ] && exit 1

if command -v code >/dev/null 2>&1; then
  CODE_AVAILABLE=1
  info "VS Code CLI: $(code --version 2>/dev/null | sed -n '1p' || echo available)"
else
  CODE_AVAILABLE=0
  warn "VS Code 'code' CLI not found on PATH. VS Code extension install can be skipped."
fi

if command -v npm >/dev/null 2>&1; then
  NPM_AVAILABLE=1
  info "npm: $(npm --version 2>/dev/null || echo available)"
else
  NPM_AVAILABLE=0
  warn "npm not found on PATH. CLI MCP package install and source .vsix packaging can be skipped."
fi

# ----- 2. ovcli.conf -----

heading "2. OpenViking client config ($OVCLI_CONF)"

mkdir -p "$OV_HOME"
chmod 700 "$OV_HOME" 2>/dev/null || true

CURRENT_URL=""
CURRENT_KEY=""
CURRENT_ACCOUNT=""
CURRENT_USER=""
CURRENT_AGENT=""
if [ -f "$OVCLI_CONF" ]; then
  CURRENT_URL=$(jq -r '.url // ""' "$OVCLI_CONF" 2>/dev/null || true)
  CURRENT_KEY=$(jq -r '.api_key // ""' "$OVCLI_CONF" 2>/dev/null || true)
  CURRENT_ACCOUNT=$(jq -r '.account // ""' "$OVCLI_CONF" 2>/dev/null || true)
  CURRENT_USER=$(jq -r '.user // ""' "$OVCLI_CONF" 2>/dev/null || true)
  CURRENT_AGENT=$(jq -r '.agent_id // ""' "$OVCLI_CONF" 2>/dev/null || true)
  if [ -n "$CURRENT_URL" ]; then
    info "Existing config found:"
    info "  url      = $CURRENT_URL"
    if [ -n "$CURRENT_KEY" ]; then
      key_preview=$(printf '%s' "$CURRENT_KEY" | cut -c1-8)
      info "  api_key  = ${key_preview}…"
    else
      info "  api_key  = <empty>"
    fi
    [ -n "$CURRENT_ACCOUNT" ] && info "  account  = $CURRENT_ACCOUNT"
    [ -n "$CURRENT_USER" ] && info "  user     = $CURRENT_USER"
    [ -n "$CURRENT_AGENT" ] && info "  agent_id = $CURRENT_AGENT"
    if ! prompt_yes_no 'Reuse these values?' 1; then
      CURRENT_URL=""; CURRENT_KEY=""; CURRENT_ACCOUNT=""; CURRENT_USER=""; CURRENT_AGENT=""
    fi
  fi
fi

if [ -z "$CURRENT_URL" ]; then
  printf '%sChoose where you will connect to OpenViking:%s\n' "$BOLD" "$RESET"
  printf '  1) Self-hosted / local                          [default: http://127.0.0.1:1933]\n'
  printf '  2) Volcengine OpenViking Cloud                  [https://api.vikingdb.cn-beijing.volces.com/openviking]\n'
  ask '[1/2, default 1]: '
  read -r MODE_INPUT || MODE_INPUT=""
  case "$MODE_INPUT" in
    2)
      CURRENT_URL="https://api.vikingdb.cn-beijing.volces.com/openviking"
      info "Using Volcengine OpenViking Cloud: $CURRENT_URL"
      KEY_PROMPT="API key (required for Volcengine OpenViking Cloud): "
      ;;
    *)
      DEFAULT_URL="http://127.0.0.1:1933"
      ask "OpenViking server URL [$DEFAULT_URL]: "
      read -r URL_INPUT || URL_INPUT=""
      CURRENT_URL="${URL_INPUT:-$DEFAULT_URL}"
      KEY_PROMPT="API key (leave empty for unauthenticated local mode): "
      ;;
  esac

  ask "$KEY_PROMPT"
  if read -rs API_INPUT 2>/dev/null; then
    printf '\n'
  else
    read -r API_INPUT || API_INPUT=""
  fi
  CURRENT_KEY="$API_INPUT"

  ask 'OpenViking account header (optional): '
  read -r CURRENT_ACCOUNT || CURRENT_ACCOUNT=""
  ask 'OpenViking user header (optional): '
  read -r CURRENT_USER || CURRENT_USER=""
  ask 'OpenViking agent id [copilot-cli]: '
  read -r CURRENT_AGENT || CURRENT_AGENT=""
  CURRENT_AGENT="${CURRENT_AGENT:-copilot-cli}"

  mkdir -p "$(dirname "$OVCLI_CONF")"
  backup_file "$OVCLI_CONF"
  tmp=$(mktemp "$OVCLI_CONF.XXXXXX") || { err 'mktemp failed'; exit 1; }
  jq -n \
    --arg url "$CURRENT_URL" \
    --arg key "$CURRENT_KEY" \
    --arg account "$CURRENT_ACCOUNT" \
    --arg user "$CURRENT_USER" \
    --arg agent "$CURRENT_AGENT" \
    '{url: $url, api_key: $key, account: $account, user: $user, agent_id: $agent}' > "$tmp"
  mv "$tmp" "$OVCLI_CONF"
  chmod 600 "$OVCLI_CONF"
  info "Wrote $OVCLI_CONF (mode 0600)"
fi

# ----- 3. Clone / refresh repo -----

heading "3. OpenViking source repository ($REPO_DIR)"

if [ -d "$REPO_DIR/.git" ]; then
  info "Updating existing checkout"
  git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  if git -C "$REPO_DIR" diff --quiet && git -C "$REPO_DIR" diff --cached --quiet; then
    git -C "$REPO_DIR" checkout -B "$REPO_BRANCH" "FETCH_HEAD"
  else
    warn "$REPO_DIR has local changes; leaving checkout untouched after fetch."
  fi
else
  if [ -e "$REPO_DIR" ]; then
    err "$REPO_DIR exists but is not a git checkout. Move it aside or set OPENVIKING_REPO_DIR."
    exit 1
  fi
  info "Cloning $REPO_URL (branch $REPO_BRANCH, depth 1)"
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# ----- 4. VS Code extension -----

heading '4. VS Code extension'

VSIX_PATH=""
build_or_find_vsix() {
  local found="${OPENVIKING_COPILOT_VSIX:-}"
  if [ -n "$found" ]; then
    if [ -f "$found" ]; then
      VSIX_PATH="$found"
      return 0
    fi
    err "OPENVIKING_COPILOT_VSIX does not exist: $found"
    return 1
  fi

  found=$(ls "$VSCODE_EXT_DIR"/*.vsix 2>/dev/null | tail -n 1 || true)
  if [ -n "$found" ]; then
    VSIX_PATH="$found"
    return 0
  fi

  if [ "$NPM_AVAILABLE" -ne 1 ]; then
    warn 'Cannot build .vsix because npm is not available.'
    return 1
  fi

  info 'No .vsix found; packaging from source.'
  ( cd "$COPILOT_DIR" && npm install && npm run package -w openviking-copilot )
  found=$(ls "$VSCODE_EXT_DIR"/*.vsix 2>/dev/null | tail -n 1 || true)
  if [ -z "$found" ]; then
    err 'VS Code package command completed but no .vsix was found.'
    return 1
  fi
  VSIX_PATH="$found"
}

if [ "$CODE_AVAILABLE" -eq 1 ]; then
  if prompt_yes_no 'Install / update the VS Code OpenViking extension from .vsix?' 1; then
    if build_or_find_vsix; then
      info "Installing VS Code extension: $VSIX_PATH"
      code --install-extension "$VSIX_PATH" --force
    else
      warn 'Skipped VS Code extension install.'
    fi
  else
    info 'Skipped VS Code extension install.'
  fi
else
  warn 'Skipped VS Code extension install because code CLI is unavailable.'
fi

# ----- 5. Copilot CLI MCP server -----

heading '5. Copilot CLI MCP server'

if [ "$NPM_AVAILABLE" -eq 1 ]; then
  if prompt_yes_no 'Install / update @openviking/copilot-cli-memory globally with npm?' 1; then
    npm i -g @openviking/copilot-cli-memory
  else
    info 'Skipped npm global install.'
  fi
else
  warn 'Skipped npm global install because npm is unavailable.'
fi

case "$OS_NAME" in
  Darwin) DEFAULT_MCP_JSON="$HOME/Library/Application Support/GitHub Copilot/mcp.json" ;;
  *) DEFAULT_MCP_JSON="$HOME/.config/github-copilot/mcp.json" ;;
esac
MCP_JSON="${COPILOT_MCP_JSON:-$DEFAULT_MCP_JSON}"
ask "Copilot CLI mcp.json path [$MCP_JSON]: "
read -r MCP_INPUT || MCP_INPUT=""
MCP_JSON="${MCP_INPUT:-$MCP_JSON}"

if prompt_yes_no 'Merge OpenViking MCP server entry into this mcp.json?' 1; then
  mkdir -p "$(dirname "$MCP_JSON")"
  if [ -f "$MCP_JSON" ]; then
    backup_file "$MCP_JSON"
  else
    info "Creating $MCP_JSON"
  fi
  tmp=$(mktemp "$MCP_JSON.XXXXXX") || { err 'mktemp failed'; exit 1; }
  if [ -f "$MCP_JSON" ]; then
    input_json="$MCP_JSON"
  else
    input_json="$tmp.empty"
    printf '{}\n' > "$input_json"
  fi
  if ! jq --arg conf "$OVCLI_CONF" '
      .mcpServers = (.mcpServers // {}) |
      .mcpServers.openviking = {
        command: "openviking-copilot-mcp",
        args: [],
        env: {
          OPENVIKING_MEMORY_ENABLED: "true",
          OPENVIKING_CLI_CONFIG_FILE: $conf
        }
      }
    ' "$input_json" > "$tmp" 2>/dev/null; then
    err "Could not merge MCP entry into $MCP_JSON; original left untouched."
    rm -f "$tmp" "${input_json:-}"
    exit 1
  fi
  mv "$tmp" "$MCP_JSON"
  [ "${input_json:-}" != "$MCP_JSON" ] && rm -f "${input_json:-}"
  info "Merged OpenViking MCP server into $MCP_JSON"
else
  info 'Skipped mcp.json merge.'
fi

# ----- 6. Shell wrapper -----

heading '6. Optional copilot() shell wrapper'

case "${SHELL:-}" in
  */zsh)  RC="$HOME/.zshrc" ;;
  */bash) RC="$HOME/.bashrc" ;;
  *)
    if   [ -f "$HOME/.zshrc" ];  then RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then RC="$HOME/.bashrc"
    else RC=""; fi
    ;;
esac

if [ ! -f "$WRAPPER_SRC" ]; then
  warn "Wrapper source not found: $WRAPPER_SRC"
  warn 'Skipping wrapper setup.'
elif [ -z "$RC" ]; then
  warn 'Could not detect shell rc. Add the wrapper manually:'
  warn "  source $WRAPPER_SRC"
else
  info "Shell rc: $RC"
  if prompt_yes_no 'Append / refresh the copilot() shell wrapper?' 0; then
    if [ -f "$RC" ]; then
      backup_file "$RC"
    else
      mkdir -p "$(dirname "$RC")"
      : > "$RC"
    fi
    tmp=$(mktemp "$RC.XXXXXX") || { err 'mktemp failed'; exit 1; }
    if grep -qF "$MARKER_BEGIN" "$RC"; then
      awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
        $0 == b {skip=1; next}
        $0 == e {skip=0; next}
        !skip
      ' "$RC" > "$tmp"
    else
      cp "$RC" "$tmp"
    fi
    cat >> "$tmp" <<EOF

$MARKER_BEGIN
# OpenViking Copilot memory wrapper. Re-run installer to refresh.
source "$WRAPPER_SRC"
$MARKER_END
EOF
    mv "$tmp" "$RC"
    info "Wrapper installed in $RC"
  else
    info 'Skipped shell wrapper.'
  fi
fi

# ----- Done -----

heading 'Done!'
info "Source:  $REPO_DIR"
info "Config:  $OVCLI_CONF"
info "MCP:     $MCP_JSON"
printf '\n'
if [ -n "${RC:-}" ]; then
  printf '%s%sNext — run this in your shell to pick up any rc changes:%s\n' "$BOLD" "$YELLOW" "$RESET"
  printf '    %s%ssource %s%s\n' "$BOLD" "$CYAN" "$RC" "$RESET"
  printf '  (or open a new terminal window)\n\n'
fi
info 'Then:'
info '  openviking-copilot-mcp --check'
info '  copilot              # start GitHub Copilot CLI'
