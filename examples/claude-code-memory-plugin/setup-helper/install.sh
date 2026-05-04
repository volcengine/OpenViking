#!/usr/bin/env bash
#
# OpenViking Memory Plugin for Claude Code — interactive installer.
#
# One-liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
#
# Steps (each is idempotent — re-running is safe):
#   1. Check OS (macOS / Linux only) and required tools.
#   2. Set up ~/.openviking/ovcli.conf — reuse if present, prompt otherwise.
#   3. Clone (or refresh) the OpenViking repo to ~/.openviking/openviking-repo.
#   4. Add a `claude` shell function to your rc that injects creds at invocation.
#   5. Install the plugin via `claude plugin marketplace add` + `claude plugin install`.
#
# Env overrides:
#   OPENVIKING_HOME        default: $HOME/.openviking
#   OPENVIKING_REPO_DIR    default: $OPENVIKING_HOME/openviking-repo
#   OPENVIKING_REPO_URL    default: https://github.com/volcengine/OpenViking.git
#   OPENVIKING_REPO_BRANCH default: main
#
# Targets bash 3.2+ (macOS /bin/bash) and Linux.

set -euo pipefail

OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$OV_HOME/openviking-repo}"
REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_BRANCH="${OPENVIKING_REPO_BRANCH:-main}"
# Honor OPENVIKING_CLI_CONFIG_FILE (the env var the `ov` CLI itself reads —
# crates/ov_cli/src/config.rs:6) so this installer matches CLI behavior.
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"

MARKER_BEGIN='# >>> openviking claude-code memory plugin >>>'
MARKER_END='# <<< openviking claude-code memory plugin <<<'

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

# ----- 1. Environment check -----

heading '1. Environment check'

case "$(uname -s)" in
  Darwin|Linux) info "OS: $(uname -s)" ;;
  *) err "Unsupported OS: $(uname -s). Only macOS and Linux are supported."; exit 1 ;;
esac

missing=0
for cmd in git jq curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "$cmd not found. Please install it and re-run."
    missing=1
  fi
done
[ "$missing" -eq 1 ] && exit 1

if command -v claude >/dev/null 2>&1; then
  CLAUDE_AVAILABLE=1
  info "claude CLI: $(claude --version 2>/dev/null || echo unknown)"
else
  CLAUDE_AVAILABLE=0
  warn "claude CLI not found on PATH. Plugin install will be skipped at the end."
  warn "Install Claude Code first: https://docs.claude.com/en/docs/claude-code/setup"
fi

# ----- 2. ovcli.conf -----

heading "2. OpenViking client config ($OVCLI_CONF)"

mkdir -p "$OV_HOME"
chmod 700 "$OV_HOME" 2>/dev/null || true

CURRENT_URL=""
CURRENT_KEY=""
if [ -f "$OVCLI_CONF" ]; then
  CURRENT_URL=$(jq -r '.url // ""' "$OVCLI_CONF" 2>/dev/null || true)
  CURRENT_KEY=$(jq -r '.api_key // ""' "$OVCLI_CONF" 2>/dev/null || true)
  if [ -n "$CURRENT_URL" ] && [ -n "$CURRENT_KEY" ]; then
    key_preview=$(printf '%s' "$CURRENT_KEY" | cut -c1-8)
    info "Existing config found:"
    info "  url     = $CURRENT_URL"
    info "  api_key = ${key_preview}…"
    ask 'Reuse these values? [Y/n] '
    read -r reply || reply=""
    case "$reply" in
      n|N|no|No|NO) CURRENT_URL=""; CURRENT_KEY="" ;;
    esac
  fi
fi

if [ -z "$CURRENT_URL" ] || [ -z "$CURRENT_KEY" ]; then
  printf '%sChoose where you'\''ll connect to OpenViking:%s\n' "$BOLD" "$RESET"
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
  # -s: don't echo (hide secret); fall back if -s unsupported
  if read -rs API_INPUT 2>/dev/null; then
    printf '\n'
  else
    read -r API_INPUT || API_INPUT=""
  fi
  CURRENT_KEY="$API_INPUT"

  if [ -f "$OVCLI_CONF" ]; then
    backup="$OVCLI_CONF.bak.$(date +%s)"
    cp "$OVCLI_CONF" "$backup"
    info "Backed up existing config → $backup"
  fi
  jq -n --arg url "$CURRENT_URL" --arg key "$CURRENT_KEY" \
    '{url: $url, api_key: $key}' > "$OVCLI_CONF"
  chmod 600 "$OVCLI_CONF"
  info "Wrote $OVCLI_CONF (mode 0600)"
fi

# ----- 3. Clone / refresh repo -----

heading "3. OpenViking source repository ($REPO_DIR)"

if [ -d "$REPO_DIR/.git" ]; then
  info "Updating existing checkout"
  git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  git -C "$REPO_DIR" reset --hard "FETCH_HEAD"
else
  if [ -e "$REPO_DIR" ]; then
    err "$REPO_DIR exists but is not a git checkout. Move it aside or set OPENVIKING_REPO_DIR."
    exit 1
  fi
  info "Cloning $REPO_URL (branch $REPO_BRANCH, depth 1)"
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# ----- 4. Shell rc wrapper -----

heading '4. Shell rc — claude function wrapper'

case "${SHELL:-}" in
  */zsh)  RC="$HOME/.zshrc" ;;
  */bash) RC="$HOME/.bashrc" ;;
  *)
    if   [ -f "$HOME/.zshrc" ];  then RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then RC="$HOME/.bashrc"
    else RC=""; fi
    ;;
esac

if [ -z "$RC" ]; then
  warn 'Could not detect shell rc. Add the function wrapper manually — see:'
  warn '  https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md'
else
  touch "$RC"
  if grep -qF "$MARKER_BEGIN" "$RC"; then
    info "Existing wrapper detected in $RC — replacing in place"
    awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
      $0 == b {skip=1; next}
      $0 == e {skip=0; next}
      !skip
    ' "$RC" > "$RC.tmp" && mv "$RC.tmp" "$RC"
  else
    info "Appending wrapper to $RC"
  fi
  cat >> "$RC" <<EOF

$MARKER_BEGIN
claude() {
  local _ov_conf="\${OPENVIKING_CLI_CONFIG_FILE:-\$HOME/.openviking/ovcli.conf}"
  if [ -f "\$_ov_conf" ] && command -v jq >/dev/null 2>&1; then
    local _ov_url _ov_key
    _ov_url=\$(jq -r '.url // empty'     "\$_ov_conf" 2>/dev/null)
    _ov_key=\$(jq -r '.api_key // empty' "\$_ov_conf" 2>/dev/null)
    OPENVIKING_URL="\${OPENVIKING_URL:-\$_ov_url}" \\
    OPENVIKING_API_KEY="\${OPENVIKING_API_KEY:-\$_ov_key}" \\
      command claude "\$@"
  else
    command claude "\$@"
  fi
}
$MARKER_END
EOF
fi

# ----- 5. Plugin install -----

heading '5. Plugin install'

if [ "$CLAUDE_AVAILABLE" -eq 1 ]; then
  # Use --scope user so the plugin is active from any directory, not just $REPO_DIR.
  # `local` scope binds enablement to $REPO_DIR's .claude/settings.local.json, which
  # surfaces as "disabled" the moment the user `cd`s elsewhere and forces a manual
  # `claude plugin enable` post-install.
  info 'claude plugin marketplace add'
  ( cd "$REPO_DIR" && claude plugin marketplace add "$REPO_DIR/examples" --scope user ) || \
    warn 'marketplace add returned non-zero (likely already added) — continuing'
  info 'claude plugin install'
  ( cd "$REPO_DIR" && claude plugin install claude-code-memory-plugin@openviking-plugins-local --scope user )
  # Belt-and-suspenders: make sure it ends up enabled even if `install` left it
  # in a disabled state (observed on some Claude Code versions).
  claude plugin enable claude-code-memory-plugin@openviking-plugins-local --scope user >/dev/null 2>&1 || true
else
  warn "Run these manually after installing Claude Code:"
  warn "  cd \"$REPO_DIR\""
  warn '  claude plugin marketplace add "$(pwd)/examples" --scope user'
  warn '  claude plugin install claude-code-memory-plugin@openviking-plugins-local --scope user'
fi

# ----- Done -----

heading 'Done!'
info "Source:    $REPO_DIR"
info "Config:    $OVCLI_CONF"
[ -n "$RC" ] && info "Shell rc:  $RC"
printf '\n'
info 'Next:'
[ -n "$RC" ] && info "  source $RC          # or open a new shell"
info '  claude              # start Claude Code'
info '  /mcp                # inside Claude Code, verify the OpenViking entry'
