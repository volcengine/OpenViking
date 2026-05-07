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
#   5. Install the plugin. On Claude Code >= 2.0 (with `claude plugin` support) we
#      use marketplace + plugin install. On older builds — or if marketplace is
#      unavailable — we fall back to legacy mode: `claude mcp add` + a merge into
#      ~/.claude/settings.json.
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
#
# `claude plugin` was introduced in Claude Code 2.0 (2025-10). Older builds only
# expose `claude mcp add` and the hooks system. We detect the major version and
# offer a legacy install path that wires the same functionality through
# `claude mcp add` + a merge into ~/.claude/settings.json.
#
# Note on `--scope`:
#   - `claude mcp add --scope user` has been supported since MCP first shipped,
#     and the default (`local`) ties the server to one project, so we DO pass it.
#   - `claude plugin install` / `claude plugin marketplace add` already default to
#     user scope, and the `--scope` flag is rejected by older 2.0.x builds (e.g.
#     2.0.76). We omit it.

heading '5. Plugin install'

# Probe for `claude plugin` support directly rather than parsing --version output.
# The version-string format isn't a stable contract; the subcommand's existence is.
has_plugin_subcommand() {
  claude plugin --help >/dev/null 2>&1
}

install_legacy() {
  local plugin_dir="$REPO_DIR/examples/claude-code-memory-plugin"
  local hooks_src="$plugin_dir/hooks/hooks.json"
  local settings="$HOME/.claude/settings.json"
  local ts; ts=$(date +%Y%m%d-%H%M%S)

  info "Legacy mode: registering MCP server + merging hooks into $settings"

  # 1) MCP server. Single-quoted ${VAR} literals so Claude Code expands them at
  # MCP launch time using whatever the rc wrapper has injected.
  info 'claude mcp add openviking (user scope)'
  claude mcp remove openviking -s user >/dev/null 2>&1 || true
  claude mcp add --scope user --transport http openviking \
    '${OPENVIKING_URL:-http://127.0.0.1:1933}/mcp' \
    --header 'Authorization: Bearer ${OPENVIKING_API_KEY:-}' \
    --header 'X-OpenViking-Account: ${OPENVIKING_ACCOUNT:-}' \
    --header 'X-OpenViking-User: ${OPENVIKING_USER:-}' \
    --header 'X-OpenViking-Agent: ${OPENVIKING_AGENT_ID:-}' || {
      err 'claude mcp add failed'
      return 1
    }

  # 2) Hooks: replace ${CLAUDE_PLUGIN_ROOT} (only expanded by 2.0+ plugin loader)
  # with an absolute path, then merge into ~/.claude/settings.json. Back up
  # first; verify the merged JSON before overwriting.
  if [ ! -f "$hooks_src" ]; then
    err "hooks source not found: $hooks_src"
    return 1
  fi
  mkdir -p "$HOME/.claude"
  [ -f "$settings" ] || echo '{}' > "$settings"
  cp -p "$settings" "$settings.bak.$ts"
  info "Backup: $settings.bak.$ts"

  # mktemp instead of `$$` — predictable PID-based names are vulnerable to
  # symlink races on shared /tmp.
  local tmp_h tmp_s
  tmp_h=$(mktemp "${TMPDIR:-/tmp}/ov-hooks.XXXXXX") || { err 'mktemp failed'; return 1; }
  tmp_s=$(mktemp "${TMPDIR:-/tmp}/ov-settings.XXXXXX") || { err 'mktemp failed'; rm -f "$tmp_h"; return 1; }

  # Replace ${CLAUDE_PLUGIN_ROOT} via jq, not sed — $plugin_dir comes from a
  # user-configurable env var and may contain &, |, or \ which would corrupt
  # sed substitution.
  if ! jq --arg root "$plugin_dir" \
      'walk(if type == "string" then gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $root) else . end)' \
      "$hooks_src" > "$tmp_h" 2>/dev/null; then
    err "expanding CLAUDE_PLUGIN_ROOT in $hooks_src failed"
    rm -f "$tmp_h" "$tmp_s"
    return 1
  fi

  # Shallow merge — keep user's other hook events; same-event keys get overwritten.
  # Explicit error branch so a malformed settings.json doesn't kill the whole
  # script via `set -e` and skip our cleanup.
  if ! jq --slurpfile h "$tmp_h" '.hooks = ((.hooks // {}) * $h[0].hooks)' \
      "$settings" > "$tmp_s" 2>/dev/null; then
    err "merging hooks into $settings failed; original untouched (intermediate: $tmp_s)"
    rm -f "$tmp_h"
    return 1
  fi
  mv "$tmp_s" "$settings"
  rm -f "$tmp_h"
  info 'hooks merged'
}

install_modern() {
  # `--scope` intentionally omitted. Default scope is already user; passing it
  # breaks older 2.0.x builds that don't recognize the flag.
  info 'claude plugin marketplace add'
  ( cd "$REPO_DIR" && claude plugin marketplace add "$REPO_DIR/examples" ) || \
    warn 'marketplace add returned non-zero (likely already added) — continuing'
  info 'claude plugin install'
  ( cd "$REPO_DIR" && claude plugin install claude-code-memory-plugin@openviking-plugins-local ) || {
    warn 'plugin install failed — falling back to legacy mode'
    install_legacy
    return $?
  }
  # Belt-and-suspenders: ensure enabled even if `install` left it disabled.
  claude plugin enable claude-code-memory-plugin@openviking-plugins-local >/dev/null 2>&1 || true
}

USE_LEGACY=0
if [ "$CLAUDE_AVAILABLE" -eq 1 ] && ! has_plugin_subcommand; then
  warn "This Claude Code build doesn't expose 'claude plugin' (introduced in 2.0)."
  ask 'Use legacy compatibility mode (claude mcp add + settings.json merge)? [Y/n] '
  read -r reply || reply=""
  case "$reply" in
    n|N|no|No|NO)
      warn "Skipping plugin install. Upgrade to Claude Code >= 2.0 and re-run."
      CLAUDE_AVAILABLE=0
      ;;
    *) USE_LEGACY=1 ;;
  esac
fi

if [ "$CLAUDE_AVAILABLE" -eq 1 ]; then
  if [ "$USE_LEGACY" -eq 1 ]; then
    install_legacy
  else
    install_modern
  fi
else
  warn "Run these manually after installing Claude Code:"
  warn "  cd \"$REPO_DIR\""
  warn '  claude plugin marketplace add "$(pwd)/examples"'
  warn '  claude plugin install claude-code-memory-plugin@openviking-plugins-local'
fi

# ----- Done -----
#
# We can't auto-source the rc into the user's shell — this script runs in a
# subshell (e.g. `bash <(curl ...)`), so any export/source we do here dies
# when the script exits. The user has to run `source` themselves, hence the
# bold callout below. (`source <(curl ...)` would work but is unsafe to
# recommend — it pipes remote code straight into the user's interactive shell.)

heading 'Done!'
info "Source:    $REPO_DIR"
info "Config:    $OVCLI_CONF"
[ -n "$RC" ] && info "Shell rc:  $RC"
printf '\n'
if [ -n "$RC" ]; then
  printf '%s%sNext — run this in your shell to pick up the wrapper:%s\n' "$BOLD" "$YELLOW" "$RESET"
  printf '    %s%ssource %s%s\n' "$BOLD" "$CYAN" "$RC" "$RESET"
  printf '  (or just open a new terminal window)\n\n'
fi
info 'Then:'
info '  claude              # start Claude Code'
info '  /mcp                # inside Claude Code, verify the OpenViking entry'
