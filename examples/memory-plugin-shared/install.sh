#!/usr/bin/env bash
#
# OpenViking Memory Plugin shared installer for Claude Code and Codex.
#
# One-liner (GitHub):
#   bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
# Non-interactive:
#   bash install.sh --harness claude,codex --source remote --url http://127.0.0.1:1933 --api-key ''
# Fork / branch verification:
#   OPENVIKING_REPO_URL=https://github.com/you/OpenViking.git \
#   OPENVIKING_REPO_REF=my-branch bash install.sh --source remote
#
# Source modes (--source, auto-detected when omitted):
#   remote   Register remote marketplaces (git-subdir for Claude Code, git for
#            Codex). No repo clone. Default.
#   archive  Download the marketplace archive (TOS / GitHub-free) and register
#            it as a local directory marketplace for both harnesses.
#   dev      Register this checkout's examples/ directory as the marketplace.
#            Auto-selected when running from a repo checkout.
#
# Legacy Claude Code (< 2.0, no `claude plugin`) is still supported: the
# installer falls back to `claude mcp add` (stdio proxy) + a hooks merge into
# ~/.claude/settings.json. That path needs a local copy of the plugin, so it
# fetches the source even in remote mode.
#
# Targets bash 3.2+ (macOS /bin/bash) and Linux.

set -euo pipefail

OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$OV_HOME/openviking-repo}"
REPO_REF="${OPENVIKING_REPO_REF:-${OPENVIKING_REPO_BRANCH:-main}}"
REPO_ARCHIVE_URL="${OPENVIKING_REPO_ARCHIVE_URL:-}"
MKT_ARCHIVE_URL="${OPENVIKING_MARKETPLACE_ARCHIVE_URL:-}"
ARCHIVE_MARKER='.openviking-archive-source'
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"

# One marketplace name everywhere. Claude Code and Codex keep separate
# registries, and within one harness the three source modes are alternative
# channels for the same plugin — a single name keeps the plugin id
# (openviking-memory@openviking) and its per-id config stable across modes.
MARKETPLACE_NAME="${OPENVIKING_MARKETPLACE_NAME:-openviking}"
PLUGIN_NAME="openviking-memory"
PLUGIN_ID="${PLUGIN_NAME}@${MARKETPLACE_NAME}"

# Pre-unification names, cleaned up on upgrade.
OLD_MARKETPLACE_NAME='openviking-plugins-local'
CC_OLD_IDS="claude-code-memory-plugin@${OLD_MARKETPLACE_NAME} ${PLUGIN_NAME}@${OLD_MARKETPLACE_NAME}"
CODEX_OLD_ID="${PLUGIN_NAME}@${OLD_MARKETPLACE_NAME}"
CODEX_OLD_MARKETPLACE_ROOT="$HOME/.codex/${OLD_MARKETPLACE_NAME}-marketplace"

CODEX_CONFIG="${CODEX_CONFIG_FILE:-$HOME/.codex/config.toml}"
CC_SETTINGS="$HOME/.claude/settings.json"
CC_KNOWN_MARKETPLACES="$HOME/.claude/plugins/known_marketplaces.json"
MKT_DIR_ARCHIVE="$OV_HOME/memory-plugin-marketplace"
# Directory-shaped on purpose: Claude Code's file-type marketplaces
# mis-derive installLocation and fail `marketplace update` with EISDIR.
CC_REMOTE_MKT_DIR="$OV_HOME/marketplaces/openviking-claude"
CC_REMOTE_MANIFEST="$CC_REMOTE_MKT_DIR/.claude-plugin/marketplace.json"

REQUESTED_HARNESSES=""
SOURCE_ARG=""
URL_ARG=""
API_KEY_ARG="__OPENVIKING_UNSET__"
ACCOUNT_ARG="__OPENVIKING_UNSET__"
USER_ARG="__OPENVIKING_UNSET__"
STATUSLINE_ARG=""   # "", yes, no
YES=0

CHECKOUT_DIR=""     # repo checkout the script itself lives in, when applicable
SRC_ROOT=""         # local source root once ensured (checkout or $REPO_DIR)
MKT_DIR=""          # directory marketplace root for archive/dev modes
SOURCE_MODE=""

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

usage() {
  cat <<EOF
Usage: install.sh [options]

Options:
  --harness LIST     Comma-separated harnesses: claude, codex, or both.
  --source MODE      remote | archive | dev (default: auto-detect).
  --url URL          OpenViking server base URL.
  --api-key KEY      OpenViking API key. Pass '' for unauthenticated local mode.
  --account ID       Optional OpenViking account.
  --user ID          Optional OpenViking user.
  --statusline       Register the Claude Code statusline without asking.
  --no-statusline    Skip the statusline prompt.
  --yes, -y          Use defaults for prompts when possible.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --harness) REQUESTED_HARNESSES="${2:-}"; shift 2 ;;
    --source) SOURCE_ARG="${2:-}"; shift 2 ;;
    --url) URL_ARG="${2:-}"; shift 2 ;;
    --api-key) API_KEY_ARG="${2-}"; shift 2 ;;
    --account) ACCOUNT_ARG="${2-}"; shift 2 ;;
    --user) USER_ARG="${2-}"; shift 2 ;;
    --statusline) STATUSLINE_ARG="yes"; shift ;;
    --no-statusline) STATUSLINE_ARG="no"; shift ;;
    --yes|-y) YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) err "Unknown argument: $1"; usage; exit 2 ;;
  esac
done

# Interactive prompts read from /dev/tty (fd 3) so `bash <(curl ...)` and even
# `curl | bash` keep their prompts. No tty -> non-interactive defaults.
# Probe in a subshell first: a failed `exec` redirection would abort the script.
INTERACTIVE=0
if [ "$YES" -ne 1 ] && ( exec 3</dev/tty ) 2>/dev/null; then
  exec 3</dev/tty
  INTERACTIVE=1
fi

read_tty() { # read_tty <varname> [-s]
  local __var="$1" __val=""
  if [ "${2:-}" = "-s" ]; then
    if IFS= read -rs __val <&3 2>/dev/null; then printf '\n'; else __val=""; fi
  else
    IFS= read -r __val <&3 || __val=""
  fi
  eval "$__var=\$__val"
}

split_harnesses() {
  printf '%s\n' "$1" | tr ',' '\n' | while IFS= read -r h; do
    h=$(printf '%s' "$h" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$h" ] && printf '%s\n' "$h"
  done
}

contains_harness() {
  local want="$1" h
  while IFS= read -r h; do
    [ "$h" = "$want" ] && return 0
  done <<EOF
$(split_harnesses "$SELECTED_HARNESSES")
EOF
  return 1
}

json_get() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  node -e '
    try {
      const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
      const v = c[process.argv[2]];
      if (v != null && v !== "") process.stdout.write(String(v));
    } catch {}
  ' "$file" "$key" 2>/dev/null || true
}

json_merge_ovcli() {
  local file="$1" url="$2" key="$3" account="$4" user="$5"
  node - "$file" "$url" "$key" "$account" "$user" <<'NODE'
const fs = require("node:fs");
const [file, url, apiKey, account, user] = process.argv.slice(2);
let c = {};
try { c = JSON.parse(fs.readFileSync(file, "utf8")); } catch {}
if (url) c.url = url;
if (apiKey !== "__OPENVIKING_KEEP__") c.api_key = apiKey;
if (account !== "__OPENVIKING_KEEP__") {
  if (account) c.account = account; else delete c.account;
}
if (user !== "__OPENVIKING_KEEP__") {
  if (user) c.user = user; else delete c.user;
}
fs.mkdirSync(require("node:path").dirname(file), { recursive: true });
fs.writeFileSync(file, JSON.stringify(c, null, 2) + "\n", { mode: 0o600 });
NODE
  chmod 600 "$file" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Harness selection (checkbox TUI on a tty, text fallback otherwise)
# ---------------------------------------------------------------------------

HAVE_CLAUDE=0; HAVE_CODEX=0
command -v claude >/dev/null 2>&1 && HAVE_CLAUDE=1
command -v codex >/dev/null 2>&1 && HAVE_CODEX=1

SEL_CLAUDE=0; SEL_CODEX=0; TUI_CURSOR=0; TUI_LINES=0

tui_item_line() { # tui_item_line <index> <selected> <label> <detected>
  local mark='[ ]' cur='  ' note=''
  [ "$2" -eq 1 ] && mark="[${GREEN}x${RESET}]"
  [ "$TUI_CURSOR" -eq "$1" ] && cur="${CYAN}>${RESET} "
  [ "$4" -eq 1 ] && note="  ${GREEN}(detected)${RESET}" || note="  ${YELLOW}(not found in PATH)${RESET}"
  printf '\r\033[K %s%s %s%s\n' "$cur" "$mark" "$3" "$note" >/dev/tty
}

tui_draw() {
  [ "$TUI_LINES" -gt 0 ] && printf '\033[%dA' "$TUI_LINES" >/dev/tty
  tui_item_line 0 "$SEL_CLAUDE" "Claude Code" "$HAVE_CLAUDE"
  tui_item_line 1 "$SEL_CODEX" "Codex" "$HAVE_CODEX"
  printf '\r\033[K   %s↑/↓ move · space toggle · a all · enter confirm%s\n' "$CYAN" "$RESET" >/dev/tty
  TUI_LINES=3
}

tui_select_harnesses() {
  local key rest
  SEL_CLAUDE=$HAVE_CLAUDE; SEL_CODEX=$HAVE_CODEX
  if [ "$SEL_CLAUDE$SEL_CODEX" = "00" ]; then SEL_CLAUDE=1; SEL_CODEX=1; fi
  printf '%sSelect the harnesses to install for:%s\n' "$BOLD" "$RESET" >/dev/tty
  printf '\033[?25l' >/dev/tty
  trap 'printf "\033[?25h" >/dev/tty' EXIT
  TUI_LINES=0
  tui_draw
  while :; do
    IFS= read -rsn1 key <&3 || key=""
    case "$key" in
      $'\x1b')
        rest=""
        IFS= read -rsn2 -t 1 rest <&3 || rest=""
        case "$rest" in
          '[A') TUI_CURSOR=0 ;;
          '[B') TUI_CURSOR=1 ;;
        esac
        ;;
      k) TUI_CURSOR=0 ;;
      j) TUI_CURSOR=1 ;;
      ' ')
        if [ "$TUI_CURSOR" -eq 0 ]; then SEL_CLAUDE=$((1 - SEL_CLAUDE)); else SEL_CODEX=$((1 - SEL_CODEX)); fi
        ;;
      a|A) SEL_CLAUDE=1; SEL_CODEX=1 ;;
      ''|$'\n'|$'\r')
        if [ $((SEL_CLAUDE + SEL_CODEX)) -eq 0 ]; then continue; fi
        break
        ;;
      q|Q) break ;;
    esac
    tui_draw
  done
  printf '\033[?25h' >/dev/tty
  trap - EXIT
  SELECTED_HARNESSES=""
  [ "$SEL_CLAUDE" -eq 1 ] && SELECTED_HARNESSES="claude"
  [ "$SEL_CODEX" -eq 1 ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}codex"
  [ -n "$SELECTED_HARNESSES" ] || SELECTED_HARNESSES="claude,codex"
}

select_harnesses() {
  local detected="" reply default
  [ "$HAVE_CLAUDE" -eq 1 ] && detected="claude"
  [ "$HAVE_CODEX" -eq 1 ] && detected="${detected:+$detected,}codex"

  if [ -n "$REQUESTED_HARNESSES" ]; then
    SELECTED_HARNESSES="$REQUESTED_HARNESSES"
    return
  fi
  default="${detected:-claude,codex}"
  if [ "$INTERACTIVE" -eq 1 ] && [ -w /dev/tty ]; then
    tui_select_harnesses
  elif [ "$INTERACTIVE" -eq 1 ]; then
    info "Detected harnesses: ${detected:-none}"
    ask "Install harnesses [${default}]: "
    read_tty reply
    SELECTED_HARNESSES="${reply:-$default}"
  else
    SELECTED_HARNESSES="$default"
  fi
}

validate_selected_harnesses() {
  local h bad=0
  while IFS= read -r h; do
    case "$h" in
      claude|codex) ;;
      *) err "Unsupported harness: $h"; bad=1 ;;
    esac
  done <<EOF
$(split_harnesses "$SELECTED_HARNESSES")
EOF
  [ "$bad" -eq 0 ] || exit 2
}

# ---------------------------------------------------------------------------
# ovcli.conf wizard
# ---------------------------------------------------------------------------

configure_ovcli() {
  local current_url url key account user reply mode url_input
  heading "2. OpenViking client config ($OVCLI_CONF)"
  mkdir -p "$OV_HOME"
  chmod 700 "$OV_HOME" 2>/dev/null || true

  current_url="$(json_get "$OVCLI_CONF" url)"

  url="$current_url"
  key="__OPENVIKING_KEEP__"
  account="__OPENVIKING_KEEP__"
  user="__OPENVIKING_KEEP__"

  [ -n "$URL_ARG" ] && url="$URL_ARG"
  [ "$API_KEY_ARG" != "__OPENVIKING_UNSET__" ] && key="$API_KEY_ARG"
  [ "$ACCOUNT_ARG" != "__OPENVIKING_UNSET__" ] && account="$ACCOUNT_ARG"
  [ "$USER_ARG" != "__OPENVIKING_UNSET__" ] && user="$USER_ARG"

  if [ -z "$url" ] && [ "$INTERACTIVE" -eq 1 ]; then
    printf '%sChoose where you will connect to OpenViking:%s\n' "$BOLD" "$RESET"
    printf '  1) Self-hosted / local                          [default: http://127.0.0.1:1933]\n'
    printf '  2) Volcengine OpenViking Cloud                  [https://api.vikingdb.cn-beijing.volces.com/openviking]\n'
    ask '[1/2, default 1]: '
    read_tty mode
    case "$mode" in
      2) url="https://api.vikingdb.cn-beijing.volces.com/openviking" ;;
      *)
        ask 'OpenViking server URL [http://127.0.0.1:1933]: '
        read_tty url_input
        url="${url_input:-http://127.0.0.1:1933}"
        ;;
    esac
  fi
  [ -z "$url" ] && url="${current_url:-http://127.0.0.1:1933}"

  if [ "$key" = "__OPENVIKING_KEEP__" ] && [ -z "$current_url" ] && [ "$INTERACTIVE" -eq 1 ]; then
    ask 'API key (leave empty for unauthenticated local mode): '
    read_tty reply -s
    key="$reply"
  fi

  if [ -f "$OVCLI_CONF" ]; then
    cp "$OVCLI_CONF" "$OVCLI_CONF.bak.$(date +%s)"
  fi
  json_merge_ovcli "$OVCLI_CONF" "$url" "$key" "$account" "$user"
  info "Config ready: $OVCLI_CONF"
  info "Reconfigure later: node <plugin>/scripts/setup.mjs (or re-run this installer)"
}

# ---------------------------------------------------------------------------
# Source acquisition
# ---------------------------------------------------------------------------

fetch_archive() { # fetch_archive <url> <dest> <required-subpath>
  local url="$1" dest="$2" need="$3" tmp_zip tmp_dir top
  command -v unzip >/dev/null 2>&1 || { err 'unzip not found; required to install from an archive.'; exit 1; }
  tmp_zip=$(mktemp "${TMPDIR:-/tmp}/ov-src.XXXXXX") || { err 'mktemp failed'; exit 1; }
  tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ov-src.XXXXXX") || { err 'mktemp failed'; rm -f "$tmp_zip"; exit 1; }
  info "Downloading archive"
  info "  $url"
  curl -fsSL -o "$tmp_zip" "$url" || { rm -rf "$tmp_zip" "$tmp_dir"; return 1; }
  unzip -q "$tmp_zip" -d "$tmp_dir" || { err 'unzip failed'; rm -rf "$tmp_zip" "$tmp_dir"; exit 1; }
  top=$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ -n "$top" ] && [ ! -e "$top/$need" ] && [ -e "$tmp_dir/$need" ]; then
    top="$tmp_dir"
  fi
  if [ -z "$top" ] || [ ! -e "$top/$need" ]; then
    err "unexpected archive layout (missing $need)"
    rm -rf "$tmp_zip" "$tmp_dir"; exit 1
  fi
  if [ -e "$dest" ] && [ ! -f "$dest/$ARCHIVE_MARKER" ] && [ ! -d "$dest/.git" ]; then
    err "$dest exists and is not an OpenViking checkout/archive."
    rm -rf "$tmp_zip" "$tmp_dir"; exit 1
  fi
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  if [ "$top" = "$tmp_dir" ]; then
    mkdir -p "$dest"
    ( shopt -s dotglob; mv "$tmp_dir"/* "$dest"/ )
  else
    mv "$top" "$dest"
  fi
  : > "$dest/$ARCHIVE_MARKER"
  rm -rf "$tmp_zip" "$tmp_dir"
}

resolve_self_checkout() {
  local src dir
  src="${BASH_SOURCE[0]}"
  dir="$(cd "$(dirname "$src")" >/dev/null 2>&1 && pwd -P)" || return 0
  if [ -d "$dir/../../.git" ] && [ -d "$dir/../claude-code-memory-plugin" ]; then
    CHECKOUT_DIR="$(cd "$dir/../.." >/dev/null 2>&1 && pwd -P)"
  fi
}

resolve_source_mode() {
  if [ -n "$SOURCE_ARG" ]; then
    SOURCE_MODE="$SOURCE_ARG"
  elif [ -n "$MKT_ARCHIVE_URL" ] || [ -n "$REPO_ARCHIVE_URL" ]; then
    SOURCE_MODE="archive"
  elif [ -n "$CHECKOUT_DIR" ]; then
    SOURCE_MODE="dev"
  else
    SOURCE_MODE="remote"
  fi
  case "$SOURCE_MODE" in
    remote|archive|dev) ;;
    *) err "Invalid --source: $SOURCE_MODE (expected remote, archive, or dev)"; exit 2 ;;
  esac
  info "Source mode: $SOURCE_MODE"
}

# Ensure a local copy of the plugin sources exists (legacy Claude Code and the
# statusline need real files on disk even in remote mode).
ensure_checkout() {
  [ -n "$SRC_ROOT" ] && return 0
  if [ -n "$CHECKOUT_DIR" ]; then
    SRC_ROOT="$CHECKOUT_DIR"
    info "Using current checkout: $SRC_ROOT"
    return 0
  fi
  if [ "$SOURCE_MODE" = "archive" ] && [ -n "$MKT_DIR" ]; then
    # The marketplace archive already contains the plugin sources.
    SRC_ROOT=""
    return 1
  fi
  if [ -n "$REPO_ARCHIVE_URL" ]; then
    fetch_archive "$REPO_ARCHIVE_URL" "$REPO_DIR" "examples" || { err "source archive download failed"; exit 1; }
  elif [ -d "$REPO_DIR/.git" ]; then
    info "Refreshing checkout ($REPO_REF)"
    git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_REF"
    git -C "$REPO_DIR" reset --hard FETCH_HEAD
  else
    if [ -e "$REPO_DIR" ] && [ ! -f "$REPO_DIR/$ARCHIVE_MARKER" ]; then
      err "$REPO_DIR exists but is not a git checkout."
      exit 1
    fi
    command -v git >/dev/null 2>&1 || { err "git not found (needed to fetch sources)."; exit 1; }
    info "Cloning $REPO_URL (ref $REPO_REF)"
    rm -rf "$REPO_DIR"
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$REPO_DIR"
  fi
  SRC_ROOT="$REPO_DIR"
}

# Locate the plugin dir with real files on disk (for legacy / statusline).
plugin_dir_on_disk() { # plugin_dir_on_disk <plugin-subdir>
  if [ -n "$MKT_DIR" ] && [ -d "$MKT_DIR/$1" ]; then
    printf '%s' "$MKT_DIR/$1"
    return 0
  fi
  ensure_checkout || true
  if [ -n "$SRC_ROOT" ] && [ -d "$SRC_ROOT/examples/$1" ]; then
    printf '%s' "$SRC_ROOT/examples/$1"
    return 0
  fi
  return 1
}

prepare_marketplace_dir() {
  case "$SOURCE_MODE" in
    dev)
      MKT_DIR="$CHECKOUT_DIR/examples"
      ;;
    archive)
      heading "3. Marketplace archive"
      if [ -n "$MKT_ARCHIVE_URL" ] && fetch_archive "$MKT_ARCHIVE_URL" "$MKT_DIR_ARCHIVE" ".claude-plugin/marketplace.json"; then
        MKT_DIR="$MKT_DIR_ARCHIVE"
      elif [ -n "$REPO_ARCHIVE_URL" ]; then
        [ -n "$MKT_ARCHIVE_URL" ] && warn "marketplace archive unavailable; falling back to the full source archive"
        fetch_archive "$REPO_ARCHIVE_URL" "$REPO_DIR" "examples" || { err "source archive download failed"; exit 1; }
        SRC_ROOT="$REPO_DIR"
        MKT_DIR="$REPO_DIR/examples"
      else
        err "archive mode needs OPENVIKING_MARKETPLACE_ARCHIVE_URL or OPENVIKING_REPO_ARCHIVE_URL"
        exit 1
      fi
      ;;
  esac
  if [ -n "$MKT_DIR" ] && [ ! -f "$MKT_DIR/.claude-plugin/marketplace.json" ]; then
    err "marketplace dir $MKT_DIR is missing .claude-plugin/marketplace.json"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Legacy wrapper cleanup (pre-stdio installs)
# ---------------------------------------------------------------------------

strip_rc_block() {
  local rc="$1" begin="$2" end="$3"
  [ -n "$rc" ] && [ -f "$rc" ] || return 0
  grep -qF "$begin" "$rc" || return 0
  if ! grep -qF "$end" "$rc"; then
    warn "Found $begin in $rc but missing end marker; leaving it untouched."
    return 0
  fi
  awk -v b="$begin" -v e="$end" '
    $0 == b {skip=1; next}
    $0 == e {skip=0; next}
    !skip
  ' "$rc" > "$rc.tmp" && mv "$rc.tmp" "$rc"
  info "Removed legacy rc block from $rc"
}

cleanup_rc_wrappers() {
  local rc
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    strip_rc_block "$rc" '# >>> openviking claude-code memory plugin >>>' '# <<< openviking claude-code memory plugin <<<'
    strip_rc_block "$rc" '# >>> openviking-codex-plugin >>>' '# <<< openviking-codex-plugin <<<'
  done
}

# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

has_plugin_subcommand() {
  command claude plugin --help >/dev/null 2>&1
}

# Current registered source string for our Claude marketplace ("" if absent).
claude_marketplace_current_source() {
  node -e '
    try {
      const m = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"))[process.argv[2]];
      const s = m && m.source ? m.source : null;
      if (s) process.stdout.write(String(s.path || s.repo || s.url || ""));
    } catch {}
  ' "$CC_KNOWN_MARKETPLACES" "$MARKETPLACE_NAME" 2>/dev/null || true
}

migrate_claude_legacy_marketplace() {
  local id
  for id in $CC_OLD_IDS; do
    if command claude plugin list 2>/dev/null | grep -qF "$id"; then
      info "Removing pre-unification plugin install ($id)"
      command claude plugin uninstall "$id" >/dev/null 2>&1 || true
    fi
  done
  if command claude plugin marketplace list 2>/dev/null | grep -qF "$OLD_MARKETPLACE_NAME"; then
    info "Removing pre-unification marketplace ($OLD_MARKETPLACE_NAME)"
    command claude plugin marketplace remove "$OLD_MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
}

write_claude_remote_manifest() {
  mkdir -p "$(dirname "$CC_REMOTE_MANIFEST")"
  # Pre-directory-layout leftover (a bare .json registered as a file-type
  # marketplace); superseded by the directory registration below.
  rm -f "$OV_HOME/marketplaces/openviking-claude.json"
  node - "$CC_REMOTE_MANIFEST" "$MARKETPLACE_NAME" "$REPO_URL" "$REPO_REF" <<'NODE'
const fs = require("node:fs");
const [file, name, url, ref] = process.argv.slice(2);
const manifest = {
  name,
  description: `OpenViking plugins for Claude Code (remote: ${url} @ ${ref}).`,
  owner: { name: "OpenViking" },
  plugins: [
    {
      name: "openviking-memory",
      description: "Long-term semantic memory for Claude Code, powered by OpenViking",
      source: { source: "git-subdir", url, path: "examples/claude-code-memory-plugin", ref },
      category: "productivity",
    },
  ],
};
fs.writeFileSync(file, JSON.stringify(manifest, null, 2) + "\n");
NODE
}

claude_marketplace_sync() { # claude_marketplace_sync <add-target> <expected-source>
  local target="$1" needle="$2" current
  current="$(claude_marketplace_current_source)"
  if [ -n "$current" ] && [ "$current" = "$needle" ]; then
    info "claude plugin marketplace update ($MARKETPLACE_NAME)"
    command claude plugin marketplace update "$MARKETPLACE_NAME" || \
      warn 'marketplace update returned non-zero — continuing'
    return 0
  fi
  if [ -n "$current" ]; then
    info "Marketplace $MARKETPLACE_NAME points elsewhere ($current); re-registering"
    command claude plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    command claude plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  info "claude plugin marketplace add ($target)"
  command claude plugin marketplace add "$target" || {
    err 'claude plugin marketplace add failed'
    return 1
  }
}

install_claude_modern() {
  case "$SOURCE_MODE" in
    remote)
      write_claude_remote_manifest
      claude_marketplace_sync "$CC_REMOTE_MKT_DIR" "$CC_REMOTE_MKT_DIR" || return 1
      ;;
    archive|dev)
      claude_marketplace_sync "$MKT_DIR" "$MKT_DIR" || return 1
      ;;
  esac
  if command claude plugin list 2>/dev/null | grep -qF "$PLUGIN_ID"; then
    info "claude plugin update ($PLUGIN_ID)"
    command claude plugin update "$PLUGIN_ID" || warn 'claude plugin update returned non-zero'
  else
    info "claude plugin install ($PLUGIN_ID)"
    command claude plugin install "$PLUGIN_ID" || { err 'claude plugin install failed'; return 1; }
  fi
  command claude plugin enable "$PLUGIN_ID" >/dev/null 2>&1 || true
  info "Claude plugin installed: $PLUGIN_ID"
}

install_claude_legacy() {
  local plugin_dir hooks_src ts
  plugin_dir="$(plugin_dir_on_disk claude-code-memory-plugin)" || {
    err 'legacy install needs the plugin sources on disk and none could be fetched'
    return 1
  }
  hooks_src="$plugin_dir/hooks/hooks.json"
  ts=$(date +%Y%m%d-%H%M%S)

  info "Legacy mode: claude mcp add (stdio proxy) + merging hooks into $CC_SETTINGS"
  command claude mcp remove openviking -s user >/dev/null 2>&1 || true
  command claude mcp add --scope user openviking -- node "$plugin_dir/servers/mcp-proxy.mjs" || {
    err 'claude mcp add failed'
    return 1
  }

  [ -f "$hooks_src" ] || { err "hooks source not found: $hooks_src"; return 1; }
  mkdir -p "$HOME/.claude"
  [ -f "$CC_SETTINGS" ] || echo '{}' > "$CC_SETTINGS"
  cp -p "$CC_SETTINGS" "$CC_SETTINGS.bak.$ts"
  info "Backup: $CC_SETTINGS.bak.$ts"
  node - "$hooks_src" "$CC_SETTINGS" "$plugin_dir" <<'NODE' || { err "merging hooks into $CC_SETTINGS failed; original untouched"; return 1; }
const fs = require("node:fs");
const [hooksSrc, settingsPath, pluginDir] = process.argv.slice(2);
const expand = (v) => {
  if (typeof v === "string") return v.split("${CLAUDE_PLUGIN_ROOT}").join(pluginDir);
  if (Array.isArray(v)) return v.map(expand);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, expand(x)]));
  return v;
};
const hooks = expand(JSON.parse(fs.readFileSync(hooksSrc, "utf8")));
const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
settings.hooks = { ...(settings.hooks || {}), ...(hooks.hooks || {}) };
fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
NODE
  info 'hooks merged'
}

register_statusline() {
  [ "$STATUSLINE_ARG" = "no" ] && return 0
  local plugin_dir cmd existing reply ts tmp
  if [ "$STATUSLINE_ARG" != "yes" ]; then
    [ "$INTERACTIVE" -eq 1 ] || return 0
    heading 'Statusline (optional)'
    info 'OpenViking can show a one-line server/recall status under the input box.'
    info 'Sample: "OV ✓ │ ↩ 6 mem (0.92) · 50ms │ ✎ 573/20k · 2 arch │ +3 today"'
    ask 'Enable OpenViking statusline? [y/N] '
    read_tty reply
    case "$reply" in
      y|Y|yes|Yes|YES) ;;
      *) info 'Skipped statusline registration. Re-run the installer to enable it later.'; return 0 ;;
    esac
  fi
  plugin_dir="$(plugin_dir_on_disk claude-code-memory-plugin)" || {
    warn 'statusline needs the plugin sources on disk and none could be fetched; skipping'
    return 0
  }
  cmd="node \"$plugin_dir/scripts/statusline.mjs\""
  mkdir -p "$HOME/.claude"
  [ -f "$CC_SETTINGS" ] || echo '{}' > "$CC_SETTINGS"
  existing=$(node -e '
    try {
      const s = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
      if (s.statusLine && s.statusLine.command) process.stdout.write(String(s.statusLine.command));
    } catch {}
  ' "$CC_SETTINGS" 2>/dev/null || true)
  if [ "$existing" = "$cmd" ]; then
    info 'Statusline already registered.'
    return 0
  fi
  if [ -n "$existing" ] && [ "$STATUSLINE_ARG" != "yes" ]; then
    warn "Existing statusline detected: $existing"
    ask 'Replace it with OpenViking statusline? [y/N] '
    read_tty reply
    case "$reply" in
      y|Y|yes|Yes|YES) ;;
      *) info 'Kept the existing statusline.'; return 0 ;;
    esac
  fi
  ts=$(date +%Y%m%d-%H%M%S)
  cp -p "$CC_SETTINGS" "$CC_SETTINGS.bak.$ts"
  node - "$CC_SETTINGS" "$cmd" <<'NODE' || { err "writing statusline into $CC_SETTINGS failed"; return 1; }
const fs = require("node:fs");
const [settingsPath, cmd] = process.argv.slice(2);
const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
settings.statusLine = { type: "command", command: cmd, padding: 0 };
fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
NODE
  info "statusline registered (backup: $CC_SETTINGS.bak.$ts)"
  info 'Silence it anytime with: export OPENVIKING_STATUSLINE=off'
}

install_claude() {
  heading "4. Claude Code plugin"
  if [ "$HAVE_CLAUDE" -ne 1 ]; then
    warn "claude CLI not found; skipping Claude Code install."
    return 0
  fi
  if has_plugin_subcommand; then
    migrate_claude_legacy_marketplace
    install_claude_modern || return 1
  else
    warn "This Claude Code build doesn't expose 'claude plugin' (introduced in 2.0)."
    local reply="y"
    if [ "$INTERACTIVE" -eq 1 ]; then
      ask 'Use legacy compatibility mode (claude mcp add + settings.json merge)? [Y/n] '
      read_tty reply
      reply="${reply:-y}"
    fi
    case "$reply" in
      n|N|no|No|NO) info 'Skipped Claude Code install.'; return 0 ;;
    esac
    install_claude_legacy || return 1
  fi
  register_statusline || true
}

# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

codex_marketplace_current_source() {
  command codex plugin marketplace list --json 2>/dev/null | node -e '
    let raw = "";
    process.stdin.on("data", (d) => { raw += d; });
    process.stdin.on("end", () => {
      try {
        const list = JSON.parse(raw).marketplaces || [];
        const m = list.find((x) => x.name === process.argv[1]);
        if (m && m.marketplaceSource) process.stdout.write(String(m.marketplaceSource.source || ""));
      } catch {}
    });
  ' "$MARKETPLACE_NAME" 2>/dev/null || true
}

migrate_codex_legacy_marketplace() {
  command codex plugin remove "$CODEX_OLD_ID" >/dev/null 2>&1 || true
  if command codex plugin marketplace list 2>/dev/null | grep -qF "$OLD_MARKETPLACE_NAME"; then
    info "Removing pre-unification marketplace ($OLD_MARKETPLACE_NAME)"
    command codex plugin marketplace remove "$OLD_MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  [ -d "$CODEX_OLD_MARKETPLACE_ROOT" ] && rm -rf "$CODEX_OLD_MARKETPLACE_ROOT"
  # Drop the old plugin id's config.toml section; the unified id gets its own.
  if [ -f "$CODEX_CONFIG" ] && grep -qF "plugins.\"$CODEX_OLD_ID\"" "$CODEX_CONFIG"; then
    node - "$CODEX_CONFIG" "$CODEX_OLD_ID" <<'NODE' || true
const fs = require("node:fs");
const [path, oldId] = process.argv.slice(2);
const lines = fs.readFileSync(path, "utf8").split(/\n/);
const out = [];
let skip = false;
for (const line of lines) {
  const trimmed = line.trim();
  if (/^\[/.test(trimmed)) skip = trimmed.startsWith(`[plugins."${oldId}"`);
  if (!skip) out.push(line);
}
fs.writeFileSync(path, out.join("\n").replace(/\n*$/, "\n"));
NODE
    info "Removed old config.toml section for $CODEX_OLD_ID"
  fi
}

codex_marketplace_sync() { # codex_marketplace_sync <expected-source> <add-args...>
  local needle="$1" current
  shift
  current="$(codex_marketplace_current_source)"
  if [ -n "$current" ] && [ "$current" = "$needle" ]; then
    info "codex plugin marketplace upgrade ($MARKETPLACE_NAME)"
    command codex plugin marketplace upgrade "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
    return 0
  fi
  if [ -n "$current" ]; then
    info "Marketplace $MARKETPLACE_NAME points elsewhere ($current); re-registering"
    command codex plugin remove "$PLUGIN_ID" >/dev/null 2>&1 || true
    command codex plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  info "codex plugin marketplace add $*"
  command codex plugin marketplace add "$@" >/dev/null || {
    err 'codex plugin marketplace add failed'
    return 1
  }
}

ensure_codex_config() {
  node - "$CODEX_CONFIG" "$PLUGIN_ID" <<'NODE'
const fs = require("node:fs");
const path = process.argv[2];
const pluginId = process.argv[3];
let text = "";
try { text = fs.readFileSync(path, "utf8"); } catch {}
function ensureSectionLine(src, section, key, value) {
  const lines = src.split(/\n/);
  const header = `[${section}]`;
  const start = lines.findIndex((line) => line.trim() === header);
  if (start === -1) {
    const prefix = src.trimEnd();
    return `${prefix}${prefix ? "\n\n" : ""}${header}\n${key} = ${value}\n`;
  }
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i += 1) if (/^\s*\[/.test(lines[i])) { end = i; break; }
  for (let i = start + 1; i < end; i += 1) {
    if (new RegExp(`^\\s*${key}\\s*=`).test(lines[i])) {
      lines[i] = `${key} = ${value}`;
      return lines.join("\n").replace(/\n*$/, "\n");
    }
  }
  lines.splice(end, 0, `${key} = ${value}`);
  return lines.join("\n").replace(/\n*$/, "\n");
}
text = ensureSectionLine(text, `plugins."${pluginId}"`, "enabled", "true");
text = ensureSectionLine(text, "features", "plugin_hooks", "true");
fs.mkdirSync(require("node:path").dirname(path), { recursive: true });
fs.writeFileSync(path, text);
NODE
}

install_codex() {
  heading "4. Codex plugin"
  if [ "$HAVE_CODEX" -ne 1 ]; then
    warn "codex CLI not found; skipping Codex install."
    return 0
  fi
  migrate_codex_legacy_marketplace
  case "$SOURCE_MODE" in
    remote)
      codex_marketplace_sync "$REPO_URL" "$REPO_URL" --ref "$REPO_REF" --sparse examples/codex-memory-plugin || \
        codex_marketplace_sync "$REPO_URL" "$REPO_URL" --ref "$REPO_REF" || return 1
      ;;
    archive|dev)
      codex_marketplace_sync "$MKT_DIR" "$MKT_DIR" || return 1
      ;;
  esac
  if ! command codex plugin add "$PLUGIN_ID" >/dev/null 2>&1; then
    command codex plugin install "$PLUGIN_ID" >/dev/null 2>&1 || \
      warn "codex plugin add/install returned non-zero for $PLUGIN_ID; config was still updated"
  fi
  ensure_codex_config
  info "Codex plugin enabled in $CODEX_CONFIG"
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

validate_install() {
  heading "5. Validation"
  local ok=1 cached
  if contains_harness claude && [ "$HAVE_CLAUDE" -eq 1 ] && has_plugin_subcommand; then
    if command claude plugin list 2>/dev/null | grep -qF "$PLUGIN_NAME"; then
      info "claude: $PLUGIN_NAME visible in plugin list"
    else
      warn "claude: $PLUGIN_NAME not visible in plugin list"
      ok=0
    fi
  fi
  if contains_harness codex && [ "$HAVE_CODEX" -eq 1 ]; then
    if command codex plugin list 2>/dev/null | grep -qiF "$PLUGIN_NAME"; then
      info "codex: $PLUGIN_NAME visible in plugin list"
    else
      warn "codex: $PLUGIN_NAME not visible in plugin list"
      ok=0
    fi
    cached=$(find "$HOME/.codex/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME" -name 'mcp-proxy.mjs' -path '*/servers/*' 2>/dev/null | sort | tail -n 1)
    if [ -n "$cached" ]; then
      node --check "$cached" && info "codex: cached stdio proxy parses ($cached)" || ok=0
    fi
  fi
  if [ -n "$MKT_DIR" ] && [ -f "$MKT_DIR/claude-code-memory-plugin/scripts/marketplace.test.mjs" ] && [ -d "$MKT_DIR/../.git" ]; then
    node --test "$MKT_DIR/claude-code-memory-plugin/scripts/marketplace.test.mjs" \
      "$MKT_DIR/codex-memory-plugin/scripts/marketplace.test.mjs" || ok=0
  fi
  if [ "$ok" -ne 1 ]; then
    warn "Validation reported issues — the install may still work; check the messages above."
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

heading "1. Environment check"
case "$(uname -s)" in
  Darwin|Linux) info "OS: $(uname -s)" ;;
  *) err "Unsupported OS: $(uname -s). Only macOS and Linux are supported."; exit 1 ;;
esac
command -v node >/dev/null 2>&1 || { err "node not found. Install Node.js 18+."; exit 1; }
NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
[ "$NODE_MAJOR" -ge 18 ] || { err "Node.js 18+ required; found $(node --version)."; exit 1; }
command -v curl >/dev/null 2>&1 || warn "curl not found; archive installs may fail."

resolve_self_checkout
select_harnesses
validate_selected_harnesses
info "Selected harnesses: $(printf '%s' "$SELECTED_HARNESSES" | tr ',' ' ')"

configure_ovcli
resolve_source_mode
prepare_marketplace_dir
cleanup_rc_wrappers

if contains_harness claude; then install_claude; fi
if contains_harness codex; then install_codex; fi
validate_install

heading "Done"
info "Config: $OVCLI_CONF"
info "MCP: stdio proxy reads ovcli.conf/env at runtime"
case "$SOURCE_MODE" in
  remote) info "Marketplace: remote ($REPO_URL @ $REPO_REF)" ;;
  *) info "Marketplace: $MKT_DIR" ;;
esac
if contains_harness claude; then info "Claude: $PLUGIN_ID"; fi
if contains_harness codex; then info "Codex:  $PLUGIN_ID"; fi
