#!/usr/bin/env bash
#
# OpenViking Memory Plugin shared installer for Claude Code, Codex, Cursor,
# TRAE / TRAE CN, OpenCode, and pi.
#
# One-liner (GitHub):
#   bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
# One-liner (TOS mirror, for regions where GitHub is unreachable):
#   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --dist tos
# Non-interactive:
#   bash install.sh --harness claude,codex,cursor,trae,trae-cn,opencode,pi --dist github --lang en --url http://127.0.0.1:1933 --api-key ''
# Format-compatible CLI aliases:
#   bash install.sh --harness codex --codex-bin codex,traex
#   bash install.sh --harness claude --claude-bin claude,seed
# Fork / branch verification:
#   OPENVIKING_REPO_URL=https://github.com/you/OpenViking.git \
#   OPENVIKING_REPO_REF=my-branch bash install.sh --source remote
#
# Distribution channels (--dist, prompted interactively):
#   github  Remote marketplaces straight from GitHub (default). Claude Code
#           uses a synthesized git-subdir manifest; Codex adds the repo as a
#           git marketplace. No repo clone, updates via plugin/marketplace
#           update commands.
#   tos     Volcengine TOS mirror, zero GitHub access. Codex adds a TOS-hosted
#           git repo (dumb HTTP) and CAN update remotely; Claude Code uses a
#           downloaded archive as a local directory marketplace and must
#           re-run this installer to update (git dumb HTTP can't serve Claude
#           Code's shallow clones).
#
# Source modes (--source, advanced override; auto-detected when omitted):
#   remote   Remote marketplaces (see --dist). Default.
#   archive  Download the marketplace archive and register it as a local
#            directory marketplace for both harnesses.
#   dev      Register this checkout's examples/ directory as the marketplace.
#            Auto-selected when running from a repo checkout.
#
# Legacy Claude Code (< 2.0, no `claude plugin`) is still supported: the
# installer falls back to `claude mcp add` (stdio proxy) + a hooks merge into
# ~/.claude/settings.json. That path needs a local copy of the plugin, so it
# fetches the source even in remote mode.
#
# Targets bash 3.2+ (macOS /bin/bash) and Linux.

set -Eeuo pipefail

OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
REPO_URL="${OPENVIKING_REPO_URL:-https://github.com/volcengine/OpenViking.git}"
REPO_DIR="${OPENVIKING_REPO_DIR:-$OV_HOME/openviking-repo}"
REPO_REF="${OPENVIKING_REPO_REF:-${OPENVIKING_REPO_BRANCH:-main}}"
REPO_ARCHIVE_URL="${OPENVIKING_REPO_ARCHIVE_URL:-}"
MKT_ARCHIVE_URL="${OPENVIKING_MARKETPLACE_ARCHIVE_URL:-}"
TOS_BASE="${OPENVIKING_TOS_BASE:-https://ovrelease.tos-cn-beijing.volces.com}"
TOS_BASE="${TOS_BASE%/}"
CODEX_TOS_GIT_URL="${OPENVIKING_CODEX_TOS_GIT_URL:-$TOS_BASE/plugins/memory-plugins.git}"
ARCHIVE_MARKER='.openviking-archive-source'
OVCLI_CONF="${OPENVIKING_CLI_CONFIG_FILE:-$OV_HOME/ovcli.conf}"

# One marketplace name everywhere. Claude Code and Codex keep separate
# registries, and within one harness the source modes are alternative channels
# for the same plugin — a single name keeps the plugin id
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
CLAUDE_BINS_ARG="${OPENVIKING_CLAUDE_BINS:-${OPENVIKING_CLAUDE_BIN:-}}"
CODEX_BINS_ARG="${OPENVIKING_CODEX_BINS:-${OPENVIKING_CODEX_BIN:-}}"
SOURCE_ARG=""
DIST_ARG=""
LANG_ARG=""
URL_ARG=""
API_KEY_ARG="__OPENVIKING_UNSET__"
ACCOUNT_ARG="__OPENVIKING_UNSET__"
USER_ARG="__OPENVIKING_UNSET__"
STATUSLINE_ARG=""   # "", yes, no
YES=0
UNINSTALL=0
NODE_BIN=""

CHECKOUT_DIR=""     # repo checkout the script itself lives in, when applicable
SRC_ROOT=""         # local source root once ensured (checkout or $REPO_DIR)
MKT_DIR=""          # directory marketplace root for archive/dev modes
SOURCE_MODE=""
DIST="github"
UI_LANG="en"

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

# t <english> <chinese> — pick the UI language variant.
t() { if [ "$UI_LANG" = "zh" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

report_unexpected_error() { # report_unexpected_error <status> <line> <command>
  local status="$1" line="$2" command="$3"
  # Avoid duplicate reports while the same failure unwinds through nested
  # functions. EXIT traps still run afterwards and restore any active TUI.
  trap - ERR
  if [ "${BASH_SUBSHELL:-0}" -gt 0 ]; then
    return "$status"
  fi
  printf '\033[?25h' >/dev/tty 2>/dev/null || true
  printf '\n' >&2
  err "$(t 'OpenViking installer stopped unexpectedly.' 'OpenViking 安装程序意外退出。')"
  printf '    %s: %s\n' "$(t 'Exit status' '状态码')" "$status" >&2
  printf '    %s: %s\n' "$(t 'Script line' '脚本行号')" "$line" >&2
  printf '    %s: %s\n' "$(t 'Command' '失败命令')" "$command" >&2
  return "$status"
}

trap 'report_unexpected_error "$?" "$LINENO" "$BASH_COMMAND"' ERR

# Pure-bash substring test. Never pipe `claude/codex plugin list` into
# `grep -q`: with pipefail, grep exiting early SIGPIPEs the producer and the
# pipeline reads as a miss even though the entry is there.
str_contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

usage() {
  cat <<EOF
Usage: install.sh [options]

Options:
  --harness LIST     Comma-separated harnesses: claude, codex, cursor, trae, trae-cn, opencode, pi.
  --claude-bin LIST  Comma-separated Claude-format CLI commands (default: claude).
  --codex-bin LIST   Comma-separated Codex-format CLI commands (default: codex).
  --dist CHANNEL     github (default) | tos (mirror for GitHub-blocked regions).
  --lang LANG        en | zh (interactive prompts language; auto-detected).
  --source MODE      Advanced: remote | archive | dev (default: auto-detect).
  --url URL          OpenViking server base URL.
  --api-key KEY      OpenViking API key. Pass '' for unauthenticated local mode.
  --account ID       Optional OpenViking account.
  --user ID          Optional OpenViking user.
  --statusline       Register the Claude Code statusline without asking.
  --no-statusline    Skip the statusline prompt.
  --uninstall        Remove Cursor/TRAE OpenViking integration files and config.
  --yes, -y          Use defaults for prompts when possible.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --harness) REQUESTED_HARNESSES="${2:-}"; shift 2 ;;
    --claude-bin|--claude-bins) CLAUDE_BINS_ARG="${2:-}"; shift 2 ;;
    --codex-bin|--codex-bins) CODEX_BINS_ARG="${2:-}"; shift 2 ;;
    --dist) DIST_ARG="${2:-}"; shift 2 ;;
    --lang) LANG_ARG="${2:-}"; shift 2 ;;
    --source) SOURCE_ARG="${2:-}"; shift 2 ;;
    --url) URL_ARG="${2:-}"; shift 2 ;;
    --api-key) API_KEY_ARG="${2-}"; shift 2 ;;
    --account) ACCOUNT_ARG="${2-}"; shift 2 ;;
    --user) USER_ARG="${2-}"; shift 2 ;;
    --statusline) STATUSLINE_ARG="yes"; shift ;;
    --no-statusline) STATUSLINE_ARG="no"; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
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

# ---------------------------------------------------------------------------
# Single-select TUI menu (arrow keys + digit shortcuts; numbered fallback when
# /dev/tty can't be drawn on; default choice when non-interactive)
# ---------------------------------------------------------------------------

TUI_MENU_CHOICE=0

tui_menu() { # tui_menu <title> <default-index> <option...>  -> TUI_MENU_CHOICE
  local title="$1" def="$2"
  shift 2
  local opts
  opts=("$@")
  local n=${#opts[@]} cursor="$def" key rest reply i lines=0
  TUI_MENU_CHOICE="$def"
  [ "$INTERACTIVE" -eq 1 ] || return 0
  if [ ! -w /dev/tty ]; then
    printf '%s%s%s\n' "$BOLD" "$title" "$RESET"
    i=0
    while [ "$i" -lt "$n" ]; do
      printf '  %d) %s\n' $((i + 1)) "${opts[$i]}"
      i=$((i + 1))
    done
    ask "[1-$n, $(t 'default' '默认') $((def + 1))]: "
    read_tty reply
    case "$reply" in
      ''|*[!0-9]*) ;;
      *) [ "$reply" -ge 1 ] && [ "$reply" -le "$n" ] && TUI_MENU_CHOICE=$((reply - 1)) ;;
    esac
    return 0
  fi
  printf '%s%s%s\n' "$BOLD" "$title" "$RESET" >/dev/tty
  printf '\033[?25l' >/dev/tty
  trap 'printf "\033[?25h" >/dev/tty' EXIT
  while :; do
    [ "$lines" -gt 0 ] && printf '\033[%dA' "$lines" >/dev/tty
    i=0
    while [ "$i" -lt "$n" ]; do
      if [ "$i" -eq "$cursor" ]; then
        printf '\r\033[K %s>%s (%s•%s) %s\n' "$CYAN" "$RESET" "$GREEN" "$RESET" "${opts[$i]}" >/dev/tty
      else
        printf '\r\033[K   ( ) %s\n' "${opts[$i]}" >/dev/tty
      fi
      i=$((i + 1))
    done
    printf '\r\033[K   %s%s%s\n' "$CYAN" "$(t '↑/↓ move · 1-9 jump · enter confirm' '↑/↓ 移动 · 数字直选 · 回车确认')" "$RESET" >/dev/tty
    lines=$((n + 1))
    IFS= read -rsn1 key <&3 || key=""
    case "$key" in
      $'\x1b')
        rest=""
        IFS= read -rsn2 -t 1 rest <&3 || rest=""
        case "$rest" in
          '[A') cursor=$(( (cursor + n - 1) % n )) ;;
          '[B') cursor=$(( (cursor + 1) % n )) ;;
        esac
        ;;
      k) cursor=$(( (cursor + n - 1) % n )) ;;
      j) cursor=$(( (cursor + 1) % n )) ;;
      [1-9])
        if [ "$key" -le "$n" ]; then
          cursor=$((key - 1))
          break
        fi
        ;;
      ''|$'\n'|$'\r') break ;;
      q|Q) cursor="$def"; break ;;
    esac
  done
  printf '\033[?25h' >/dev/tty
  trap - EXIT
  TUI_MENU_CHOICE="$cursor"
}

# ---------------------------------------------------------------------------
# UI language
# ---------------------------------------------------------------------------

detect_lang_default() {
  case "${OPENVIKING_LANG:-${LC_ALL:-${LANG:-}}}" in
    zh*|*zh_CN*|*zh_TW*|*zh_HK*) printf 'zh' ;;
    *) printf 'en' ;;
  esac
}

select_language() {
  local detected def
  detected="$(detect_lang_default)"
  if [ -n "$LANG_ARG" ]; then
    UI_LANG="$LANG_ARG"
  elif [ "$INTERACTIVE" -eq 1 ]; then
    def=0
    [ "$detected" = "zh" ] && def=1
    tui_menu "Language / 语言" "$def" "English" "中文"
    if [ "$TUI_MENU_CHOICE" -eq 1 ]; then UI_LANG="zh"; else UI_LANG="en"; fi
  else
    UI_LANG="$detected"
  fi
  case "$UI_LANG" in
    en|zh) ;;
    *) err "Invalid --lang: $UI_LANG (expected en or zh)"; exit 2 ;;
  esac
}

split_harnesses() {
  printf '%s\n' "$1" | tr ',' '\n' | while IFS= read -r h; do
    h=$(printf '%s' "$h" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$h" ] && printf '%s\n' "$h"
  done
}

split_csv_list() {
  printf '%s\n' "$1" | tr ',' '\n' | while IFS= read -r item; do
    item=$(printf '%s' "$item" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$item" ] && printf '%s\n' "$item"
  done
}

normalize_bin_list() { # normalize_bin_list <raw-list> <default-command>
  local raw="${1:-}" def="$2"
  [ -n "$raw" ] || raw="$def"
  split_csv_list "$raw"
}

append_csv_list() { # append_csv_list <existing-lines> <extra-csv>
  local existing="$1" extra="$2" item out=""
  while IFS= read -r item; do
    [ -n "$item" ] || continue
    list_contains_line "$out" "$item" || out="${out:+$out
}$item"
  done <<EOF
$existing
$(split_csv_list "$extra")
EOF
  printf '%s\n' "$out"
}

list_contains_line() {
  local list="$1" want="$2" item
  while IFS= read -r item; do
    [ "$item" = "$want" ] && return 0
  done <<EOF
$list
EOF
  return 1
}

list_words() {
  local item out=""
  while IFS= read -r item; do
    [ -n "$item" ] || continue
    out="${out:+$out }$item"
  done <<EOF
$1
EOF
  printf '%s' "$out"
}

has_available_bin() {
  local bins="$1" bin
  while IFS= read -r bin; do
    [ -n "$bin" ] || continue
    command -v "$bin" >/dev/null 2>&1 && return 0
  done <<EOF
$bins
EOF
  return 1
}

refresh_available_harnesses() {
  HAVE_CLAUDE=0; HAVE_CODEX=0; HAVE_CURSOR=0; HAVE_TRAE=0; HAVE_TRAE_CN=0; HAVE_OPENCODE=0; HAVE_PI=0
  has_available_bin "$CLAUDE_BINS" && HAVE_CLAUDE=1
  has_available_bin "$CODEX_BINS" && HAVE_CODEX=1
  { command -v cursor >/dev/null 2>&1 || command -v cursor-agent >/dev/null 2>&1 || [ -d "/Applications/Cursor.app" ] || [ -d "$HOME/.cursor" ]; } && HAVE_CURSOR=1
  { [ -d "/Applications/Trae.app" ] || [ -d "/Applications/TRAE.app" ] || [ -d "$HOME/.trae" ]; } && HAVE_TRAE=1
  { [ -d "/Applications/Trae CN.app" ] || [ -d "/Applications/TRAE SOLO CN.app" ] || [ -d "$HOME/.trae-cn" ]; } && HAVE_TRAE_CN=1
  command -v opencode >/dev/null 2>&1 && HAVE_OPENCODE=1
  command -v pi >/dev/null 2>&1 && HAVE_PI=1
  return 0
}

bin_basename() {
  local bin="$1"
  bin="${bin##*/}"
  printf '%s' "$bin"
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

mask_secret() {
  local s="$1"
  if [ -z "$s" ]; then printf '%s' "$(t '(not set)' '（未设置）')"; return; fi
  if [ "${#s}" -le 8 ]; then printf '****'; return; fi
  printf '%s…%s (%s)' "$(printf '%s' "$s" | cut -c1-4)" "$(printf '%s' "$s" | tail -c 4)" "${#s}"
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

CLAUDE_BINS="$(normalize_bin_list "$CLAUDE_BINS_ARG" claude)"
CODEX_BINS="$(normalize_bin_list "$CODEX_BINS_ARG" codex)"

HAVE_CLAUDE=0; HAVE_CODEX=0; HAVE_CURSOR=0; HAVE_TRAE=0; HAVE_TRAE_CN=0; HAVE_OPENCODE=0; HAVE_PI=0
refresh_available_harnesses

TUI_CLAUDE_BINS="$CLAUDE_BINS"
TUI_CODEX_BINS="$CODEX_BINS"
SEL_CLAUDE_BINS=""
SEL_CODEX_BINS=""
SEL_OPENCODE=0
SEL_PI=0
SEL_CURSOR_APP=0
SEL_TRAE=0
SEL_TRAE_CN=0
TUI_CURSOR=0; TUI_LINES=0

list_count() {
  local list="$1" item n=0
  while IFS= read -r item; do
    [ -n "$item" ] && n=$((n + 1))
  done <<EOF
$list
EOF
  printf '%s' "$n"
}

tui_selectable_count() {
  printf '%s' $(( $(list_count "$TUI_CLAUDE_BINS") + $(list_count "$TUI_CODEX_BINS") + 5 ))
}

tui_total_count() {
  printf '%s' $(( $(tui_selectable_count) + 1 ))
}

tui_item_at() { # tui_item_at <index> -> kind|bin, or add|
  local idx="$1" i=0 bin
  while IFS= read -r bin; do
    [ -n "$bin" ] || continue
    if [ "$i" -eq "$idx" ]; then printf 'claude|%s' "$bin"; return 0; fi
    i=$((i + 1))
  done <<EOF
$TUI_CLAUDE_BINS
EOF
  while IFS= read -r bin; do
    [ -n "$bin" ] || continue
    if [ "$i" -eq "$idx" ]; then printf 'codex|%s' "$bin"; return 0; fi
    i=$((i + 1))
  done <<EOF
$TUI_CODEX_BINS
EOF
  if [ "$i" -eq "$idx" ]; then printf 'opencode|opencode'; return 0; fi
  i=$((i + 1))
  if [ "$i" -eq "$idx" ]; then printf 'pi|pi'; return 0; fi
  i=$((i + 1))
  if [ "$i" -eq "$idx" ]; then printf 'cursor|cursor'; return 0; fi
  i=$((i + 1))
  if [ "$i" -eq "$idx" ]; then printf 'trae|trae'; return 0; fi
  i=$((i + 1))
  if [ "$i" -eq "$idx" ]; then printf 'trae-cn|trae-cn'; return 0; fi
  printf 'add|'
}

tui_find_bin_index() { # tui_find_bin_index <kind> <bin>
  local want_kind="$1" want_bin="$2" idx=0 spec kind bin total
  total="$(tui_selectable_count)"
  while [ "$idx" -lt "$total" ]; do
    spec="$(tui_item_at "$idx")"
    kind="${spec%%|*}"
    bin="${spec#*|}"
    if [ "$kind" = "$want_kind" ] && [ "$bin" = "$want_bin" ]; then
      printf '%s' "$idx"
      return 0
    fi
    idx=$((idx + 1))
  done
  printf '0'
}

tui_bin_label() {
  local kind="$1" bin="$2"
  case "$kind:$bin" in
    claude:claude) printf 'Claude Code' ;;
    codex:codex) printf 'Codex' ;;
    opencode:*) printf 'OpenCode' ;;
    pi:*) printf 'pi' ;;
    cursor:*) printf 'Cursor' ;;
    trae:*) printf 'TRAE' ;;
    trae-cn:*) printf 'TRAE CN' ;;
    claude:*) printf '%s %s' "$bin" "$(t '(Claude-format)' '（Claude 格式）')" ;;
    codex:*) printf '%s %s' "$bin" "$(t '(Codex-format)' '（Codex 格式）')" ;;
  esac
}

tui_bin_selected() {
  local kind="$1" bin="$2"
  if [ "$kind" = "claude" ]; then
    list_contains_line "$SEL_CLAUDE_BINS" "$bin"
  elif [ "$kind" = "codex" ]; then
    list_contains_line "$SEL_CODEX_BINS" "$bin"
  elif [ "$kind" = "opencode" ]; then
    [ "$SEL_OPENCODE" -eq 1 ]
  elif [ "$kind" = "pi" ]; then
    [ "$SEL_PI" -eq 1 ]
  elif [ "$kind" = "cursor" ]; then
    [ "$SEL_CURSOR_APP" -eq 1 ]
  elif [ "$kind" = "trae" ]; then
    [ "$SEL_TRAE" -eq 1 ]
  else
    [ "$SEL_TRAE_CN" -eq 1 ]
  fi
}

tui_bin_detected() { # tui_bin_detected <kind> <bin>
  case "$1" in
    cursor) [ "$HAVE_CURSOR" -eq 1 ] ;;
    trae) [ "$HAVE_TRAE" -eq 1 ] ;;
    trae-cn) [ "$HAVE_TRAE_CN" -eq 1 ] ;;
    *) command -v "$2" >/dev/null 2>&1 ;;
  esac
}

tui_set_all_bins() {
  SEL_CLAUDE_BINS="$TUI_CLAUDE_BINS"
  SEL_CODEX_BINS="$TUI_CODEX_BINS"
  SEL_OPENCODE=1
  SEL_PI=1
  SEL_CURSOR_APP=1
  SEL_TRAE=1
  SEL_TRAE_CN=1
}

tui_toggle_bin() {
  local kind="$1" bin="$2" item out="" selected
  if [ "$kind" = "claude" ]; then
    selected="$SEL_CLAUDE_BINS"
  elif [ "$kind" = "codex" ]; then
    selected="$SEL_CODEX_BINS"
  elif [ "$kind" = "opencode" ]; then
    SEL_OPENCODE=$((1 - SEL_OPENCODE))
    return 0
  elif [ "$kind" = "pi" ]; then
    SEL_PI=$((1 - SEL_PI))
    return 0
  elif [ "$kind" = "cursor" ]; then
    SEL_CURSOR_APP=$((1 - SEL_CURSOR_APP)); return 0
  elif [ "$kind" = "trae" ]; then
    SEL_TRAE=$((1 - SEL_TRAE)); return 0
  else
    SEL_TRAE_CN=$((1 - SEL_TRAE_CN)); return 0
  fi
  if list_contains_line "$selected" "$bin"; then
    while IFS= read -r item; do
      [ -n "$item" ] || continue
      [ "$item" = "$bin" ] && continue
      out="${out:+$out
}$item"
    done <<EOF
$selected
EOF
  else
    out="$(append_csv_list "$selected" "$bin")"
  fi
  if [ "$kind" = "claude" ]; then
    SEL_CLAUDE_BINS="$out"
  else
    SEL_CODEX_BINS="$out"
  fi
}

tui_item_line() { # tui_item_line <index> <kind> <bin>
  local idx="$1" kind="$2" bin="$3" mark='[ ]' cur='  ' note='' label
  label="$(tui_bin_label "$kind" "$bin")"
  tui_bin_selected "$kind" "$bin" && mark="[${GREEN}x${RESET}]"
  [ "$TUI_CURSOR" -eq "$idx" ] && cur="${CYAN}>${RESET} "
  if tui_bin_detected "$kind" "$bin"; then
    note="  ${GREEN}$(t '(detected)' '（已检测到）')${RESET}"
  else
    note="  ${YELLOW}$(t '(not found in PATH)' '（PATH 中未找到）')${RESET}"
  fi
  printf '\r\033[K %s%s %s%s\n' "$cur" "$mark" "$label" "$note" >/dev/tty
}

tui_add_item_line() {
  local idx="$1" cur='  '
  [ "$TUI_CURSOR" -eq "$idx" ] && cur="${CYAN}>${RESET} "
  printf '\r\033[K %s%s %s\n' "$cur" "${CYAN}+${RESET}" "$(t 'Add compatible CLI...' '新增兼容 CLI...')" >/dev/tty
}

tui_draw() {
  local idx=0 spec kind bin total
  [ "$TUI_LINES" -gt 0 ] && printf '\033[%dA' "$TUI_LINES" >/dev/tty
  total="$(tui_total_count)"
  while [ "$idx" -lt "$total" ]; do
    spec="$(tui_item_at "$idx")"
    kind="${spec%%|*}"
    bin="${spec#*|}"
    if [ "$kind" = "add" ]; then
      add_idx="$idx"
      tui_add_item_line "$idx"
    else
      tui_item_line "$idx" "$kind" "$bin"
    fi
    idx=$((idx + 1))
  done
  printf '\r\033[K   %s%s%s\n' "$CYAN" "$(t '↑/↓ move · space toggle · enter confirm · enter on + to add · a all' '↑/↓ 移动 · 空格勾选 · 回车确认 · 在 + 上回车新增 · a 全选')" "$RESET" >/dev/tty
  TUI_LINES=$((total + 1))
}

tui_reset_bin_selection() {
  local bin any=0
  SEL_CLAUDE_BINS=""
  SEL_CODEX_BINS=""
  SEL_OPENCODE=0
  SEL_PI=0
  SEL_CURSOR_APP=0
  SEL_TRAE=0
  SEL_TRAE_CN=0
  while IFS= read -r bin; do
    [ -n "$bin" ] || continue
    if command -v "$bin" >/dev/null 2>&1; then
      SEL_CLAUDE_BINS="${SEL_CLAUDE_BINS:+$SEL_CLAUDE_BINS
}$bin"; any=1
    fi
  done <<EOF
$TUI_CLAUDE_BINS
EOF
  while IFS= read -r bin; do
    [ -n "$bin" ] || continue
    if command -v "$bin" >/dev/null 2>&1; then
      SEL_CODEX_BINS="${SEL_CODEX_BINS:+$SEL_CODEX_BINS
}$bin"; any=1
    fi
  done <<EOF
$TUI_CODEX_BINS
EOF
  if command -v opencode >/dev/null 2>&1; then SEL_OPENCODE=1; any=1; fi
  if command -v pi >/dev/null 2>&1; then SEL_PI=1; any=1; fi
  if [ "$HAVE_CURSOR" -eq 1 ]; then SEL_CURSOR_APP=1; any=1; fi
  if [ "$HAVE_TRAE" -eq 1 ]; then SEL_TRAE=1; any=1; fi
  if [ "$HAVE_TRAE_CN" -eq 1 ]; then SEL_TRAE_CN=1; any=1; fi
  if [ "$any" -ne 1 ]; then
    SEL_CLAUDE_BINS="$TUI_CLAUDE_BINS"
    SEL_CODEX_BINS="$TUI_CODEX_BINS"
  fi
}

tui_choose_cli_format() {
  local cursor=0 key rest lines=0
  TUI_FORMAT_CHOICE=""
  printf '%s%s%s\n' "$BOLD" "$(t 'Choose compatible format:' '选择兼容格式：')" "$RESET" >/dev/tty
  printf '\033[?25l' >/dev/tty
  while :; do
    [ "$lines" -gt 0 ] && printf '\033[%dA' "$lines" >/dev/tty
    if [ "$cursor" -eq 0 ]; then
      printf '\r\033[K %s>%s (%s•%s) %s\n' "$CYAN" "$RESET" "$GREEN" "$RESET" "$(t 'Claude-format' 'Claude 格式')" >/dev/tty
      printf '\r\033[K   ( ) %s\n' "$(t 'Codex-format' 'Codex 格式')" >/dev/tty
    else
      printf '\r\033[K   ( ) %s\n' "$(t 'Claude-format' 'Claude 格式')" >/dev/tty
      printf '\r\033[K %s>%s (%s•%s) %s\n' "$CYAN" "$RESET" "$GREEN" "$RESET" "$(t 'Codex-format' 'Codex 格式')" >/dev/tty
    fi
    printf '\r\033[K   %s%s%s\n' "$CYAN" "$(t '↑/↓ move · enter confirm · q cancel' '↑/↓ 移动 · 回车确认 · q 取消')" "$RESET" >/dev/tty
    lines=3
    if ! IFS= read -rsn1 key <&3; then
      continue
    fi
    case "$key" in
      $'\x1b')
        rest=""
        IFS= read -rsn2 -t 1 rest <&3 || rest=""
        case "$rest" in
          '[A'|'[B') cursor=$((1 - cursor)) ;;
        esac
        ;;
      k|j) cursor=$((1 - cursor)) ;;
      1) TUI_FORMAT_CHOICE="claude"; break ;;
      2) TUI_FORMAT_CHOICE="codex"; break ;;
      ''|$'\n'|$'\r')
        if [ "$cursor" -eq 0 ]; then TUI_FORMAT_CHOICE="claude"; else TUI_FORMAT_CHOICE="codex"; fi
        break
        ;;
      q|Q) TUI_FORMAT_CHOICE=""; break ;;
    esac
  done
  printf '\033[?25h' >/dev/tty
}

tui_add_compatible_cli() {
  local kind bin
  printf '\033[?25h' >/dev/tty
  printf '\n%s%s%s\n' "$BOLD" "$(t 'Add compatible CLI' '新增兼容 CLI')" "$RESET" >/dev/tty
  tui_choose_cli_format
  kind="$TUI_FORMAT_CHOICE"
  if [ -z "$kind" ]; then
    warn "$(t 'Skipped adding compatible CLI.' '已跳过新增兼容 CLI。')"
    TUI_LINES=0
    printf '%s%s%s\n' "$BOLD" "$(t 'Select the harnesses to install for:' '选择要安装的 harness：')" "$RESET" >/dev/tty
    printf '\033[?25l' >/dev/tty
    return 0
  fi
  ask "$(t 'Command name or path: ' '命令名或路径: ')"
  read_tty bin
  bin="$(printf '%s' "$bin" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [ -z "$bin" ]; then
    warn "$(t 'Skipped adding compatible CLI.' '已跳过新增兼容 CLI。')"
    TUI_LINES=0
    printf '%s%s%s\n' "$BOLD" "$(t 'Select the harnesses to install for:' '选择要安装的 harness：')" "$RESET" >/dev/tty
    printf '\033[?25l' >/dev/tty
    return 0
  fi
  if [ "$kind" = "claude" ]; then
    TUI_CLAUDE_BINS="$(append_csv_list "$TUI_CLAUDE_BINS" "$bin")"
    SEL_CLAUDE_BINS="$(append_csv_list "$SEL_CLAUDE_BINS" "$bin")"
    info "$(t 'Added Claude-format CLI:' '已新增 Claude 格式 CLI：') $bin"
  else
    TUI_CODEX_BINS="$(append_csv_list "$TUI_CODEX_BINS" "$bin")"
    SEL_CODEX_BINS="$(append_csv_list "$SEL_CODEX_BINS" "$bin")"
    info "$(t 'Added Codex-format CLI:' '已新增 Codex 格式 CLI：') $bin"
  fi
  TUI_CURSOR="$(tui_find_bin_index "$kind" "$bin")"
  TUI_LINES=0
  printf '%s%s%s\n' "$BOLD" "$(t 'Select the harnesses to install for:' '选择要安装的 harness：')" "$RESET" >/dev/tty
  printf '\033[?25l' >/dev/tty
}

tui_has_selection() {
  [ -n "$(list_words "$SEL_CLAUDE_BINS")" ] || [ -n "$(list_words "$SEL_CODEX_BINS")" ] \
    || [ "$SEL_OPENCODE" -eq 1 ] || [ "$SEL_PI" -eq 1 ] || [ "$SEL_CURSOR_APP" -eq 1 ] \
    || [ "$SEL_TRAE" -eq 1 ] || [ "$SEL_TRAE_CN" -eq 1 ]
}

tui_finish_selection() {
  CLAUDE_BINS="$SEL_CLAUDE_BINS"
  CODEX_BINS="$SEL_CODEX_BINS"
  SELECTED_HARNESSES=""
  [ -n "$(list_words "$CLAUDE_BINS")" ] && SELECTED_HARNESSES="claude"
  [ -n "$(list_words "$CODEX_BINS")" ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}codex"
  [ "$SEL_OPENCODE" -eq 1 ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}opencode"
  [ "$SEL_PI" -eq 1 ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}pi"
  [ "$SEL_CURSOR_APP" -eq 1 ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}cursor"
  [ "$SEL_TRAE" -eq 1 ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}trae"
  [ "$SEL_TRAE_CN" -eq 1 ] && SELECTED_HARNESSES="${SELECTED_HARNESSES:+$SELECTED_HARNESSES,}trae-cn"
  return 0
}

tui_select_harnesses() {
  local key rest spec kind bin total add_idx
  tui_reset_bin_selection
  printf '%s%s%s\n' "$BOLD" "$(t 'Select the harnesses to install for:' '选择要安装的 harness：')" "$RESET" >/dev/tty
  printf '\033[?25l' >/dev/tty
  trap 'printf "\033[?25h" >/dev/tty' EXIT
  TUI_LINES=0
  tui_draw
  while :; do
    total="$(tui_total_count)"
    add_idx=$((total - 1))
    if ! IFS= read -rsn1 key <&3; then
      tui_draw
      continue
    fi
    case "$key" in
      $'\x1b')
        rest=""
        IFS= read -rsn2 -t 1 rest <&3 || rest=""
        case "$rest" in
          '[A') TUI_CURSOR=$(( (TUI_CURSOR + total - 1) % total )) ;;
          '[B') TUI_CURSOR=$(( (TUI_CURSOR + 1) % total )) ;;
        esac
        ;;
      k) TUI_CURSOR=$(( (TUI_CURSOR + total - 1) % total )) ;;
      j) TUI_CURSOR=$(( (TUI_CURSOR + 1) % total )) ;;
      ' ')
        if [ "$TUI_CURSOR" -eq "$add_idx" ]; then
          tui_add_compatible_cli
        else
          spec="$(tui_item_at "$TUI_CURSOR")"; kind="${spec%%|*}"; bin="${spec#*|}"
          tui_toggle_bin "$kind" "$bin"
        fi
        ;;
      a|A) tui_set_all_bins ;;
      n|N|+)
        TUI_CURSOR="$add_idx"
        tui_add_compatible_cli
        ;;
      ''|$'\n'|$'\r')
        if [ "$TUI_CURSOR" -eq "$add_idx" ]; then
          tui_add_compatible_cli
        else
          tui_has_selection || { tui_draw; continue; }
          break
        fi
        ;;
      q|Q) tui_has_selection && break ;;
    esac
    total="$(tui_total_count)"
    [ "$TUI_CURSOR" -lt "$total" ] || TUI_CURSOR=$((total - 1))
    tui_draw
  done
  printf '\033[?25h' >/dev/tty
  trap - EXIT
  tui_finish_selection
}

select_harnesses() {
  local detected="" reply default
  [ "$HAVE_CLAUDE" -eq 1 ] && detected="claude"
  [ "$HAVE_CODEX" -eq 1 ] && detected="${detected:+$detected,}codex"
  [ "$HAVE_CURSOR" -eq 1 ] && detected="${detected:+$detected,}cursor"
  [ "$HAVE_TRAE" -eq 1 ] && detected="${detected:+$detected,}trae"
  [ "$HAVE_TRAE_CN" -eq 1 ] && detected="${detected:+$detected,}trae-cn"
  [ "$HAVE_OPENCODE" -eq 1 ] && detected="${detected:+$detected,}opencode"
  [ "$HAVE_PI" -eq 1 ] && detected="${detected:+$detected,}pi"

  if [ -n "$REQUESTED_HARNESSES" ]; then
    SELECTED_HARNESSES="$REQUESTED_HARNESSES"
    return
  fi
  default="${detected:-claude,codex}"
  if [ "$INTERACTIVE" -eq 1 ] && [ -w /dev/tty ]; then
    tui_select_harnesses
  elif [ "$INTERACTIVE" -eq 1 ]; then
    info "$(t 'Detected harnesses:' '检测到的 harness：') ${detected:-none}"
    ask "$(t 'Install harnesses' '要安装的 harness') [${default}]: "
    read_tty reply
    SELECTED_HARNESSES="${reply:-$default}"
  else
    SELECTED_HARNESSES="$default"
  fi
}

select_compatible_bins() {
  local reply
  [ "$INTERACTIVE" -eq 1 ] || return 0
  [ -w /dev/tty ] && return 0
  if contains_harness claude && [ -z "$CLAUDE_BINS_ARG" ]; then
    ask "$(t 'Extra Claude-format CLI commands, comma-separated (e.g. seed)' '额外 Claude 格式 CLI 命令，逗号分隔（如 seed）') [$(t 'none' '无')]: "
    read_tty reply
    if [ -n "$reply" ]; then
      CLAUDE_BINS="$(append_csv_list "$CLAUDE_BINS" "$reply")"
      info "$(t 'Claude-format commands:' 'Claude 格式命令：') $(list_words "$CLAUDE_BINS")"
    fi
  fi
  if contains_harness codex && [ -z "$CODEX_BINS_ARG" ]; then
    ask "$(t 'Extra Codex-format CLI commands, comma-separated (e.g. traex)' '额外 Codex 格式 CLI 命令，逗号分隔（如 traex）') [$(t 'none' '无')]: "
    read_tty reply
    if [ -n "$reply" ]; then
      CODEX_BINS="$(append_csv_list "$CODEX_BINS" "$reply")"
      info "$(t 'Codex-format commands:' 'Codex 格式命令：') $(list_words "$CODEX_BINS")"
    fi
  fi
  refresh_available_harnesses
}

validate_selected_harnesses() {
  local h bad=0
  while IFS= read -r h; do
    case "$h" in
      claude|codex|cursor|trae|trae-cn|opencode|pi) ;;
      *) err "Unsupported harness: $h"; bad=1 ;;
    esac
  done <<EOF
$(split_harnesses "$SELECTED_HARNESSES")
EOF
  [ "$bad" -eq 0 ] || exit 2
}

validate_selected_bins() {
  local bin ok=0
  if contains_harness claude; then
    while IFS= read -r bin; do
      [ -n "$bin" ] || continue
      if command -v "$bin" >/dev/null 2>&1; then
        ok=1
      else
        warn "$(t 'Selected Claude-format CLI not found in PATH:' '已选择的 Claude 格式 CLI 不在 PATH 中：') $bin"
      fi
    done <<EOF
$CLAUDE_BINS
EOF
  fi
  if contains_harness codex; then
    while IFS= read -r bin; do
      [ -n "$bin" ] || continue
      if command -v "$bin" >/dev/null 2>&1; then
        ok=1
      else
        warn "$(t 'Selected Codex-format CLI not found in PATH:' '已选择的 Codex 格式 CLI 不在 PATH 中：') $bin"
      fi
    done <<EOF
$CODEX_BINS
EOF
  fi
  if contains_harness opencode && command -v opencode >/dev/null 2>&1; then ok=1; fi
  if contains_harness pi && command -v pi >/dev/null 2>&1; then ok=1; fi
  # Cursor and TRAE are config-driven integrations. They may be installed
  # before the desktop app itself, so a CLI in PATH is not required.
  if contains_harness cursor || contains_harness trae || contains_harness trae-cn; then ok=1; fi
  if [ "$ok" -ne 1 ]; then
    err "$(t 'No selected compatible CLI command was found in PATH.' '未在 PATH 中找到任何已选择的兼容 CLI 命令。')"
    exit 2
  fi
}

# ---------------------------------------------------------------------------
# Distribution channel (github vs tos mirror)
# ---------------------------------------------------------------------------

select_dist() {
  if [ -n "$DIST_ARG" ]; then
    DIST="$DIST_ARG"
  elif [ "$INTERACTIVE" -eq 1 ] && [ -z "$SOURCE_ARG" ]; then
    if [ -n "$CHECKOUT_DIR" ]; then
      tui_menu "$(t 'Install source' '安装源模式')" 2 \
        "GitHub  $(t '(remote marketplace; supports remote updates)' '（远程 marketplace；支持远程更新）')" \
        "$(t 'Volcengine TOS mirror (use when GitHub is unreachable)' '火山引擎 TOS 镜像（无法访问 GitHub 时使用）')" \
        "$(t 'This checkout (development; edits take effect live)' '当前 checkout（开发模式；改动即时生效）')"
      case "$TUI_MENU_CHOICE" in
        0) DIST="github" ;;
        1) DIST="tos" ;;
        *) SOURCE_ARG="dev" ;;
      esac
    else
      tui_menu "$(t 'Install source' '安装源模式')" 0 \
        "GitHub  $(t '(default; supports remote updates)' '（默认；支持远程更新）')" \
        "$(t 'Volcengine TOS mirror (use when GitHub is unreachable)' '火山引擎 TOS 镜像（无法访问 GitHub 时使用）')"
      if [ "$TUI_MENU_CHOICE" -eq 1 ]; then DIST="tos"; else DIST="github"; fi
    fi
  fi
  case "$DIST" in
    github|tos) ;;
    *) err "Invalid --dist: $DIST (expected github or tos)"; exit 2 ;;
  esac
}

# ---------------------------------------------------------------------------
# ovcli.conf wizard
# ---------------------------------------------------------------------------

prompt_connection() { # sets WIZ_URL / WIZ_KEY (WIZ_KEY may stay __OPENVIKING_KEEP__)
  local current_url="$1" current_key="$2" url_input reply
  tui_menu "$(t 'Where do you connect to OpenViking?' '连接到哪个 OpenViking 服务？')" 2 \
    "$(t 'Self-hosted / local' '自建 / 本地')  [http://127.0.0.1:1933]" \
    "$(t 'Volcengine OpenViking Cloud' '火山引擎 OpenViking 云服务')  [api.vikingdb.cn-beijing.volces.com]" \
    "$(t 'Custom URL / keep current' '自定义 URL / 保持当前')  [${current_url:-http://127.0.0.1:1933}]"
  case "$TUI_MENU_CHOICE" in
    0) WIZ_URL="http://127.0.0.1:1933" ;;
    1) WIZ_URL="https://api.vikingdb.cn-beijing.volces.com/openviking" ;;
    *)
      ask "$(t 'Server URL' '服务地址') [${current_url:-http://127.0.0.1:1933}]: "
      read_tty url_input
      WIZ_URL="${url_input:-${current_url:-http://127.0.0.1:1933}}"
      ;;
  esac

  if [ -n "$current_key" ]; then
    ask "$(t "API key [enter = keep $(mask_secret "$current_key"), '-' = clear]: " "API key [回车 = 保留 $(mask_secret "$current_key")，输入 '-' 清空]: ")"
  else
    ask "$(t 'API key (leave empty for unauthenticated local mode): ' 'API key（本地免鉴权模式请直接回车）: ')"
  fi
  read_tty reply -s
  if [ "$reply" = "-" ]; then
    WIZ_KEY=""
  elif [ -n "$reply" ]; then
    WIZ_KEY="$reply"
  else
    WIZ_KEY="__OPENVIKING_KEEP__"
    [ -z "$current_key" ] && WIZ_KEY=""
  fi
}

configure_ovcli() {
  local current_url current_key current_account current_user url key account user reply
  heading "$(t '2. OpenViking credentials' '2. OpenViking 凭据配置') ($OVCLI_CONF)"
  mkdir -p "$OV_HOME"
  chmod 700 "$OV_HOME" 2>/dev/null || true

  current_url="$(json_get "$OVCLI_CONF" url)"
  current_key="$(json_get "$OVCLI_CONF" api_key)"
  current_account="$(json_get "$OVCLI_CONF" account)"
  current_user="$(json_get "$OVCLI_CONF" user)"

  url="$current_url"
  key="__OPENVIKING_KEEP__"
  account="__OPENVIKING_KEEP__"
  user="__OPENVIKING_KEEP__"

  [ -n "$URL_ARG" ] && url="$URL_ARG"
  [ "$API_KEY_ARG" != "__OPENVIKING_UNSET__" ] && key="$API_KEY_ARG"
  [ "$ACCOUNT_ARG" != "__OPENVIKING_UNSET__" ] && account="$ACCOUNT_ARG"
  [ "$USER_ARG" != "__OPENVIKING_UNSET__" ] && user="$USER_ARG"

  # Show what is configured today, then offer to keep or reconfigure.
  if [ -n "$current_url" ] || [ -n "$current_key" ]; then
    info "$(t 'Current config:' '当前配置：')"
    info "  url:     ${current_url:-$(t '(not set)' '（未设置）')}"
    info "  api_key: $(mask_secret "$current_key")"
    [ -n "$current_account" ] && info "  account: $current_account"
    [ -n "$current_user" ] && info "  user:    $current_user"
  else
    info "$(t 'No existing config found.' '未找到已有配置。')"
  fi

  if [ "$INTERACTIVE" -eq 1 ] && [ -z "$URL_ARG" ] && [ "$API_KEY_ARG" = "__OPENVIKING_UNSET__" ]; then
    if [ -n "$current_url" ] || [ -n "$current_key" ]; then
      tui_menu "$(t 'Existing credentials found — what next?' '检测到已有凭据——如何处理？')" 0 \
        "$(t 'Keep current credentials' '沿用当前凭据')" \
        "$(t 'Reconfigure (server URL / API key)' '重新配置（服务地址 / API key）')"
      if [ "$TUI_MENU_CHOICE" -eq 1 ]; then
        prompt_connection "$current_url" "$current_key"
        url="$WIZ_URL"; key="$WIZ_KEY"
      fi
    else
      prompt_connection "" ""
      url="$WIZ_URL"; key="$WIZ_KEY"
    fi
  fi
  [ -z "$url" ] && url="${current_url:-http://127.0.0.1:1933}"

  if [ -f "$OVCLI_CONF" ]; then
    cp "$OVCLI_CONF" "$OVCLI_CONF.bak.$(date +%s)"
  fi
  json_merge_ovcli "$OVCLI_CONF" "$url" "$key" "$account" "$user"
  if [ "$url" != "$current_url" ] || { [ "$key" != "__OPENVIKING_KEEP__" ] && [ "$key" != "$current_key" ]; }; then
    info "$(t 'Updated:' '已更新：') url: ${current_url:-—} -> $url"
  fi
  info "$(t 'Credentials ready:' '凭据已就绪：') $OVCLI_CONF"
  info "$(t 'Reconfigure later by re-running this installer.' '之后可重跑本安装脚本重新配置。')"
}

# ---------------------------------------------------------------------------
# Source acquisition
# ---------------------------------------------------------------------------

fetch_archive() { # fetch_archive <url> <dest> <required-subpath>
  local url="$1" dest="$2" need="$3" tmp_zip tmp_dir top
  command -v unzip >/dev/null 2>&1 || { err 'unzip not found; required to install from an archive.'; exit 1; }
  tmp_zip=$(mktemp "${TMPDIR:-/tmp}/ov-src.XXXXXX") || { err 'mktemp failed'; exit 1; }
  tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ov-src.XXXXXX") || { err 'mktemp failed'; rm -f "$tmp_zip"; exit 1; }
  info "$(t 'Downloading archive' '下载归档')"
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
  elif [ "$DIST" = "tos" ]; then
    SOURCE_MODE="archive"
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
  info "$(t 'Source mode:' '安装源模式：') $SOURCE_MODE ($(t 'channel' '渠道'): $DIST)"
  if [ "$SOURCE_MODE" = "archive" ] && [ "$HAVE_CLAUDE" -eq 1 ] && contains_harness claude; then
    warn "$(t 'TOS/archive installs cannot auto-update Claude Code (local directory marketplace); re-run this installer to update. Codex keeps remote updates via its TOS git marketplace.' 'TOS/归档方式安装的 Claude Code 插件无法自动更新（本地目录 marketplace），更新请重跑本安装脚本；Codex 走 TOS git marketplace 仍可远程更新。')"
  fi
}

# Ensure a local copy of the plugin sources exists (legacy Claude Code and the
# statusline need real files on disk even in remote mode).
ensure_checkout() {
  [ -n "$SRC_ROOT" ] && return 0
  if [ -n "$CHECKOUT_DIR" ]; then
    SRC_ROOT="$CHECKOUT_DIR"
    info "$(t 'Using current checkout:' '使用当前 checkout：') $SRC_ROOT"
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
    info "$(t 'Refreshing checkout' '刷新 checkout') ($REPO_REF)"
    git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_REF"
    git -C "$REPO_DIR" reset --hard FETCH_HEAD
  else
    if [ -e "$REPO_DIR" ] && [ ! -f "$REPO_DIR/$ARCHIVE_MARKER" ]; then
      err "$REPO_DIR exists but is not a git checkout."
      exit 1
    fi
    command -v git >/dev/null 2>&1 || { err "git not found (needed to fetch sources)."; exit 1; }
    info "$(t 'Cloning' '克隆') $REPO_URL (ref $REPO_REF)"
    rm -rf "$REPO_DIR"
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$REPO_DIR"
  fi
  SRC_ROOT="$REPO_DIR"
}

# Locate the plugin dir with real files on disk (for legacy / statusline).
# Callers capture stdout ($(...)), so any progress output from the fetch has
# to stay on stderr or it corrupts the captured path.
plugin_dir_on_disk() { # plugin_dir_on_disk <plugin-subdir>
  if [ -n "$MKT_DIR" ] && [ -d "$MKT_DIR/$1" ]; then
    printf '%s' "$MKT_DIR/$1"
    return 0
  fi
  ensure_checkout 1>&2 || true
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
      heading "$(t '3. Marketplace archive' '3. Marketplace 归档')"
      [ -z "$MKT_ARCHIVE_URL" ] && [ "$DIST" = "tos" ] && MKT_ARCHIVE_URL="$TOS_BASE/releases/latest/memory-plugin-marketplace.zip"
      [ -z "$REPO_ARCHIVE_URL" ] && [ "$DIST" = "tos" ] && REPO_ARCHIVE_URL="$TOS_BASE/releases/latest/openviking-source.zip"
      if [ -n "$MKT_ARCHIVE_URL" ] && fetch_archive "$MKT_ARCHIVE_URL" "$MKT_DIR_ARCHIVE" ".claude-plugin/marketplace.json"; then
        MKT_DIR="$MKT_DIR_ARCHIVE"
      elif [ -n "$REPO_ARCHIVE_URL" ]; then
        [ -n "$MKT_ARCHIVE_URL" ] && warn "$(t 'marketplace archive unavailable; falling back to the full source archive' 'marketplace 归档不可用，回退到完整源码归档')"
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
  info "$(t 'Removed legacy rc block from' '已移除旧的 rc 注入块：') $rc"
}

cleanup_rc_wrappers() {
  local rc
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if contains_harness claude; then
      strip_rc_block "$rc" '# >>> openviking claude-code memory plugin >>>' '# <<< openviking claude-code memory plugin <<<'
    fi
    if contains_harness codex; then
      strip_rc_block "$rc" '# >>> openviking-codex-plugin >>>' '# <<< openviking-codex-plugin <<<'
    fi
  done
}

# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

CLAUDE_BIN="claude"

is_native_claude_bin() {
  [ "$(bin_basename "$CLAUDE_BIN")" = "claude" ]
}

claude_cmd() {
  command "$CLAUDE_BIN" "$@"
}

has_plugin_subcommand() {
  claude_cmd plugin --help >/dev/null 2>&1
}

# Current registered source string for our Claude marketplace ("" if absent).
claude_marketplace_current_source() {
  local raw
  raw="$(claude_cmd plugin marketplace list --json 2>/dev/null || true)"
  if [ -n "$raw" ]; then
    printf '%s' "$raw" | node -e '
      let raw = "";
      process.stdin.on("data", (d) => { raw += d; });
      process.stdin.on("end", () => {
        try {
          const parsed = JSON.parse(raw);
          const list = Array.isArray(parsed) ? parsed : (parsed.marketplaces || []);
          const m = list.find((x) => x.name === process.argv[1]);
          if (m) process.stdout.write(String(m.path || m.repo || m.url || m.source || ""));
        } catch {}
      });
    ' "$MARKETPLACE_NAME" 2>/dev/null || true
    return 0
  fi
  is_native_claude_bin || return 0
  node -e '
    try {
      const m = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"))[process.argv[2]];
      const s = m && m.source ? m.source : null;
      if (s) process.stdout.write(String(s.path || s.repo || s.url || ""));
    } catch {}
  ' "$CC_KNOWN_MARKETPLACES" "$MARKETPLACE_NAME" 2>/dev/null || true
}

migrate_claude_legacy_marketplace() {
  local id plugin_list marketplace_list
  plugin_list="$(claude_cmd plugin list 2>/dev/null || true)"
  for id in $CC_OLD_IDS; do
    if str_contains "$plugin_list" "$id"; then
      info "$(t 'Removing pre-unification plugin install' '移除旧命名的插件安装') ($id)"
      claude_cmd plugin uninstall "$id" >/dev/null 2>&1 || true
    fi
  done
  marketplace_list="$(claude_cmd plugin marketplace list 2>/dev/null || true)"
  if str_contains "$marketplace_list" "$OLD_MARKETPLACE_NAME"; then
    info "$(t 'Removing pre-unification marketplace' '移除旧命名的 marketplace') ($OLD_MARKETPLACE_NAME)"
    claude_cmd plugin marketplace remove "$OLD_MARKETPLACE_NAME" >/dev/null 2>&1 || true
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
    info "$CLAUDE_BIN plugin marketplace update ($MARKETPLACE_NAME)"
    claude_cmd plugin marketplace update "$MARKETPLACE_NAME" || \
      warn 'marketplace update returned non-zero — continuing'
    return 0
  fi
  if [ -n "$current" ]; then
    info "$(t 'Marketplace points elsewhere; re-registering' 'marketplace 指向其他来源，重新注册') ($current)"
    claude_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    claude_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  elif ! is_native_claude_bin; then
    claude_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    claude_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  info "$CLAUDE_BIN plugin marketplace add ($target)"
  claude_cmd plugin marketplace add "$target" || {
    err "$CLAUDE_BIN plugin marketplace add failed"
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
  if str_contains "$(claude_cmd plugin list 2>/dev/null || true)" "$PLUGIN_ID"; then
    info "$CLAUDE_BIN plugin update ($PLUGIN_ID)"
    claude_cmd plugin update "$PLUGIN_ID" || warn "$CLAUDE_BIN plugin update returned non-zero"
  else
    info "$CLAUDE_BIN plugin install ($PLUGIN_ID)"
    claude_cmd plugin install "$PLUGIN_ID" || { err "$CLAUDE_BIN plugin install failed"; return 1; }
  fi
  claude_cmd plugin enable "$PLUGIN_ID" >/dev/null 2>&1 || true
  info "$(t 'Claude-format plugin installed:' 'Claude 格式插件已安装：') $CLAUDE_BIN -> $PLUGIN_ID"
}

install_claude_legacy() {
  local plugin_dir hooks_src ts
  plugin_dir="$(plugin_dir_on_disk claude-code-memory-plugin)" || {
    err 'legacy install needs the plugin sources on disk and none could be fetched'
    return 1
  }
  hooks_src="$plugin_dir/hooks/hooks.json"
  ts=$(date +%Y%m%d-%H%M%S)

  info "Legacy mode: $CLAUDE_BIN mcp add (stdio proxy) + merging hooks into $CC_SETTINGS"
  claude_cmd mcp remove openviking -s user >/dev/null 2>&1 || true
  claude_cmd mcp add --scope user openviking -- node "$plugin_dir/servers/mcp-proxy.mjs" || {
    err "$CLAUDE_BIN mcp add failed"
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
  local plugin_dir cmd existing reply ts
  if [ "$STATUSLINE_ARG" != "yes" ]; then
    [ "$INTERACTIVE" -eq 1 ] || return 0
    heading "$(t 'Statusline (optional)' 'Statusline 状态栏（可选）')"
    info "$(t 'OpenViking can show a one-line server/recall status under the input box.' 'OpenViking 可以在输入框下方显示一行服务/召回状态。')"
    info 'Sample: "OV ✓ │ Fable 5 · ctx 42% │ ↩ 6 mem (0.92) · 50ms │ ✎ 573/20k · 2 arch │ +3 today"'
    tui_menu "$(t 'Enable the OpenViking statusline?' '启用 OpenViking statusline？')" 1 \
      "$(t 'Enable' '启用')" \
      "$(t 'Skip' '跳过')"
    if [ "$TUI_MENU_CHOICE" -ne 0 ]; then
      info "$(t 'Skipped statusline registration. Re-run the installer to enable it later.' '跳过 statusline 注册，之后重跑安装脚本可启用。')"
      return 0
    fi
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
    info "$(t 'Statusline already registered.' 'Statusline 已注册。')"
    return 0
  fi
  if [ -n "$existing" ] && [ "$STATUSLINE_ARG" != "yes" ]; then
    warn "$(t 'Existing statusline detected:' '检测到已有 statusline：') $existing"
    tui_menu "$(t 'Replace it with the OpenViking statusline?' '替换为 OpenViking statusline？')" 1 \
      "$(t 'Replace' '替换')" \
      "$(t 'Keep existing' '保留现有')"
    if [ "$TUI_MENU_CHOICE" -ne 0 ]; then
      info "$(t 'Kept the existing statusline.' '保留了已有 statusline。')"
      return 0
    fi
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
  heading "$(t '4. Claude Code plugin' '4. Claude Code 插件')"
  command -v "$CLAUDE_BIN" >/dev/null 2>&1 || {
    warn "$(t 'Claude-format CLI not found; skipping:' '未找到 Claude 格式 CLI，跳过：') $CLAUDE_BIN"
    return 0
  }
  if has_plugin_subcommand; then
    migrate_claude_legacy_marketplace
    install_claude_modern || return 1
  else
    warn "$(t "This Claude-format CLI doesn't expose 'plugin'." '当前 Claude 格式 CLI 没有 plugin 子命令。') ($CLAUDE_BIN)"
    if ! is_native_claude_bin; then
      warn "$(t 'Legacy compatibility mode is only supported for the native claude command; skipping this custom CLI.' '旧版兼容模式仅支持原生 claude 命令；跳过这个自定义 CLI。')"
      return 0
    fi
    if [ "$INTERACTIVE" -eq 1 ]; then
      tui_menu "$(t 'Use legacy compatibility mode (claude mcp add + settings.json merge)?' '使用旧版兼容模式（claude mcp add + settings.json 合并）？')" 0 \
        "$(t 'Yes, install in legacy mode' '是，用兼容模式安装')" \
        "$(t 'Skip Claude Code' '跳过 Claude Code')"
      if [ "$TUI_MENU_CHOICE" -eq 1 ]; then
        info "$(t 'Skipped Claude Code install.' '跳过 Claude Code 安装。')"
        return 0
      fi
    fi
    install_claude_legacy || return 1
  fi
  if is_native_claude_bin; then
    register_statusline || true
  fi
}

# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

CODEX_BIN="codex"

is_native_codex_bin() {
  [ "$(bin_basename "$CODEX_BIN")" = "codex" ]
}

codex_cmd() {
  command "$CODEX_BIN" "$@"
}

codex_marketplace_current_source() {
  local raw
  raw="$(codex_cmd plugin marketplace list --json 2>/dev/null || true)"
  [ -n "$raw" ] || return 0
  printf '%s' "$raw" | node -e '
    let raw = "";
    process.stdin.on("data", (d) => { raw += d; });
    process.stdin.on("end", () => {
      try {
        const parsed = JSON.parse(raw);
        const list = Array.isArray(parsed) ? parsed : (parsed.marketplaces || []);
        const m = list.find((x) => x.name === process.argv[1]);
        if (m && m.marketplaceSource) process.stdout.write(String(m.marketplaceSource.source || ""));
        else if (m) process.stdout.write(String(m.path || m.repo || m.url || m.source || ""));
      } catch {}
    });
  ' "$MARKETPLACE_NAME" 2>/dev/null || true
}

migrate_codex_legacy_marketplace() {
  codex_cmd plugin remove "$CODEX_OLD_ID" >/dev/null 2>&1 || true
  codex_cmd plugin uninstall "$CODEX_OLD_ID" >/dev/null 2>&1 || true
  if str_contains "$(codex_cmd plugin marketplace list 2>/dev/null || true)" "$OLD_MARKETPLACE_NAME"; then
    info "$(t 'Removing pre-unification marketplace' '移除旧命名的 marketplace') ($OLD_MARKETPLACE_NAME)"
    codex_cmd plugin marketplace remove "$OLD_MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  if is_native_codex_bin; then
    [ -d "$CODEX_OLD_MARKETPLACE_ROOT" ] && rm -rf "$CODEX_OLD_MARKETPLACE_ROOT"
    [ -d "$HOME/.codex/plugins/cache/$OLD_MARKETPLACE_NAME" ] && rm -rf "$HOME/.codex/plugins/cache/$OLD_MARKETPLACE_NAME"
  fi
  # Drop the old plugin id's config.toml section; the unified id gets its own.
  if is_native_codex_bin && [ -f "$CODEX_CONFIG" ] && grep -qF "plugins.\"$CODEX_OLD_ID\"" "$CODEX_CONFIG"; then
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
    info "$CODEX_BIN plugin marketplace upgrade ($MARKETPLACE_NAME)"
    codex_cmd plugin marketplace upgrade "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
    return 0
  fi
  if [ -n "$current" ]; then
    info "$(t 'Marketplace points elsewhere; re-registering' 'marketplace 指向其他来源，重新注册') ($current)"
    codex_cmd plugin remove "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  elif ! is_native_codex_bin; then
    codex_cmd plugin remove "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  info "$CODEX_BIN plugin marketplace add $*"
  codex_cmd plugin marketplace add "$@" >/dev/null || {
    err "$CODEX_BIN plugin marketplace add failed"
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
  heading "$(t '4. Codex plugin' '4. Codex 插件')"
  command -v "$CODEX_BIN" >/dev/null 2>&1 || {
    warn "$(t 'Codex-format CLI not found; skipping:' '未找到 Codex 格式 CLI，跳过：') $CODEX_BIN"
    return 0
  }
  migrate_codex_legacy_marketplace
  case "$SOURCE_MODE" in
    remote)
      # Codex doesn't expose which --ref a registered git marketplace is
      # pinned to (`marketplace upgrade` silently refreshes the OLD ref), so
      # a matching URL is not enough — re-register deterministically.
      codex_cmd plugin remove "$PLUGIN_ID" >/dev/null 2>&1 || true
      codex_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
      codex_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
      info "$CODEX_BIN plugin marketplace add $REPO_URL --ref $REPO_REF"
      # Sparse must include .agents/ — the marketplace manifest lives there,
      # and a plugin-dir-only sparse checkout fails manifest resolution.
      codex_cmd plugin marketplace add "$REPO_URL" --ref "$REPO_REF" \
        --sparse examples/codex-memory-plugin --sparse .agents >/dev/null 2>&1 || \
        codex_cmd plugin marketplace add "$REPO_URL" --ref "$REPO_REF" >/dev/null || {
          err "$CODEX_BIN plugin marketplace add failed"
          return 1
        }
      ;;
    archive)
      if [ "$DIST" = "tos" ] && install_codex_tos_git; then
        :
      else
        codex_marketplace_sync "$MKT_DIR" "$MKT_DIR" || return 1
      fi
      ;;
    dev)
      codex_marketplace_sync "$MKT_DIR" "$MKT_DIR" || return 1
      ;;
  esac
  if ! codex_cmd plugin add "$PLUGIN_ID" >/dev/null 2>&1; then
    codex_cmd plugin install "$PLUGIN_ID" >/dev/null 2>&1 || \
      warn "$CODEX_BIN plugin add/install returned non-zero for $PLUGIN_ID"
  fi
  codex_cmd plugin enable "$PLUGIN_ID" >/dev/null 2>&1 || true
  if is_native_codex_bin; then
    ensure_codex_config
    info "$(t 'Codex plugin enabled in' 'Codex 插件已在配置中启用：') $CODEX_CONFIG"
  else
    info "$(t 'Codex-format plugin installed:' 'Codex 格式插件已安装：') $CODEX_BIN -> $PLUGIN_ID"
  fi
}

# Codex can clone git repos served over dumb HTTP from static hosting, so the
# TOS mirror hosts a slim marketplace git repo — unlike Claude Code, Codex
# keeps remote update support (`codex plugin marketplace upgrade`) on TOS.
install_codex_tos_git() {
  info "$CODEX_BIN plugin marketplace add $CODEX_TOS_GIT_URL"
  local current
  current="$(codex_marketplace_current_source)"
  if [ -n "$current" ] && [ "$current" = "$CODEX_TOS_GIT_URL" ]; then
    codex_cmd plugin marketplace upgrade "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
    return 0
  fi
  if [ -n "$current" ]; then
    codex_cmd plugin remove "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  elif ! is_native_codex_bin; then
    codex_cmd plugin remove "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin uninstall "$PLUGIN_ID" >/dev/null 2>&1 || true
    codex_cmd plugin marketplace remove "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  fi
  if ! codex_cmd plugin marketplace add "$CODEX_TOS_GIT_URL" >/dev/null 2>&1; then
    warn "$(t 'TOS git marketplace unavailable; falling back to the archive directory.' 'TOS git marketplace 不可用，回退到归档目录方式。')"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Cursor / TRAE lifecycle hooks
# ---------------------------------------------------------------------------

copy_agent_integration() { # copy_agent_integration <source-subdir> <dest-name>
  local source_subdir="$1" dest_name="$2" source dest tmp
  source="$(plugin_dir_on_disk "$source_subdir")" || {
    err "$(t 'Agent integration sources not found:' '未找到 Agent 接入源码：') $source_subdir"
    return 1
  }
  dest="$OV_HOME/agent-integrations/$dest_name"
  tmp="$dest.tmp"
  rm -rf "$tmp"
  mkdir -p "$tmp"
  (cd "$source" && tar --exclude node_modules --exclude .git -cf - .) | (cd "$tmp" && tar -xf -)
  # Preserve the first-install timestamp across managed upgrades. The package
  # descriptor is copied from source; integration.json records this machine's
  # installation and must survive replacing the runtime directory.
  [ -f "$dest/integration.json" ] && cp "$dest/integration.json" "$tmp/integration.json"
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  mv "$tmp" "$dest"
  printf '%s' "$dest"
}

# Cursor and TRAE keep only their client-specific adapters in the repository.
# Assemble a self-contained installation by adding the canonical shared runtime
# at install time instead of committing generated copies for every client.
assemble_agent_integration() { # assemble_agent_integration <source-subdir> <dest-name>
  local source_subdir="$1" dest_name="$2" root shared shared_dest file
  root="$(copy_agent_integration "$source_subdir" "$dest_name")" || return 1
  shared="$(plugin_dir_on_disk memory-plugin-shared)" || {
    err "$(t 'Shared agent runtime not found.' '未找到共享 Agent 运行时。')"
    return 1
  }
  shared_dest="$OV_HOME/agent-integrations/memory-plugin-shared/lib"
  rm -rf "$shared_dest.tmp"
  mkdir -p "$shared_dest.tmp"
  for file in \
    agent-hook-runtime.mjs agent-uri-guard.mjs credentials.mjs debug-log.mjs \
    mcp-proxy-core.mjs pending-queue.mjs profile-inject.mjs recall-core.mjs \
    session-model.mjs uri-guard.mjs workspace-peer.mjs; do
    cp "$shared/lib/$file" "$shared_dest.tmp/$file"
  done
  rm -rf "$shared_dest"
  mkdir -p "$(dirname "$shared_dest")"
  mv "$shared_dest.tmp" "$shared_dest"
  printf '%s' "$root"
}

agent_write_json_configs() { # agent_write_json_configs <kind> <hooks> <mcp> <root> <client-id> <node-bin>
  local kind="$1" hooks_path="$2" mcp_path="$3" root="$4" client_id="$5" node_bin="$6"
  "$NODE_BIN" - "$kind" "$hooks_path" "$mcp_path" "$root" "$client_id" "$node_bin" "$SOURCE_MODE" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const [kind, hooksPath, mcpPath, root, clientId, nodeBin, sourceMode] = process.argv.slice(2);

function readJson(file) {
  if (!fs.existsSync(file)) return {};
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("top-level value must be an object");
    }
    return parsed;
  } catch (error) {
    throw new Error(`Cannot safely update ${file}: ${error.message}`);
  }
}

function atomicWrite(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const next = JSON.stringify(value, null, 2) + "\n";
  let previous = "";
  try { previous = fs.readFileSync(file, "utf8"); } catch {}
  if (previous === next) return;
  if (previous) fs.writeFileSync(`${file}.bak`, previous, { mode: 0o600 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, next, { mode: 0o600 });
  fs.renameSync(tmp, file);
}

function shellArg(value) {
  return `'${String(value).replace(/'/g, `'"'"'`)}'`;
}

function isOpenVikingHook(value) {
  const text = JSON.stringify(value || {});
  return text.includes("OPENVIKING_INTEGRATION_ID") || (text.includes("openviking") && [
    "cursor-hook.mjs",
    "trae-hook.mjs",
    "session-start.mjs",
    "auto-recall.mjs",
    "auto-capture.mjs",
    "pre-compact.mjs",
    "session-end.mjs",
    "trae-auto-recall.mjs",
    "trae-auto-capture.mjs",
    "claude-code-memory-plugin/scripts/session-start.mjs",
  ].some((name) => text.includes(name)));
}

const packageManifest = readJson(path.join(root, "openviking.integration.json"));
if (packageManifest.id !== "openviking-memory" || !Array.isArray(packageManifest.clients)
  || !packageManifest.clients.includes(clientId)) {
  throw new Error(`Invalid OpenViking integration manifest for ${clientId}`);
}
const integrationEnv = {
  OPENVIKING_INTEGRATION_ID: packageManifest.id,
  OPENVIKING_INTEGRATION_VERSION: packageManifest.version,
  OPENVIKING_HOOK_SOURCE: clientId,
};
const envPrefix = Object.entries(integrationEnv)
  .map(([key, value]) => `${key}=${shellArg(value)}`)
  .join(" ");

function renderHookCommand(command) {
  let rendered = command;
  const cursorMatch = /^node\s+\$\{CURSOR_PLUGIN_ROOT\}\/(.+)$/u.exec(rendered);
  if (cursorMatch) {
    rendered = `${shellArg(nodeBin)} ${shellArg(path.join(root, cursorMatch[1]))}`;
  } else {
    const traeMatch = /^node\s+__OPENVIKING_TRAE_ROOT__\/(\S+)\s+(.+)$/u.exec(rendered);
    if (!traeMatch) throw new Error(`Unsupported ${clientId} hook command template: ${command}`);
    rendered = `${shellArg(nodeBin)} ${shellArg(path.join(root, traeMatch[1]))} ${traeMatch[2]
      .replaceAll("__OPENVIKING_CLIENT_ID__", clientId)}`;
  }
  return `${envPrefix} ${rendered} # openviking-memory`;
}

function renderHookValue(value) {
  if (Array.isArray(value)) return value.map(renderHookValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, child]) => [
    key,
    key === "command" && typeof child === "string" ? renderHookCommand(child) : renderHookValue(child),
  ]));
}

const hookTemplate = readJson(path.join(root, "hooks", "hooks.json"));
if (!hookTemplate.hooks || typeof hookTemplate.hooks !== "object" || Array.isArray(hookTemplate.hooks)) {
  throw new Error(`Invalid ${clientId} hooks template`);
}
const hooksConfig = readJson(hooksPath);
hooksConfig.version = Number.isFinite(Number(hooksConfig.version)) ? Number(hooksConfig.version) : 1;
hooksConfig.hooks = hooksConfig.hooks && typeof hooksConfig.hooks === "object" && !Array.isArray(hooksConfig.hooks)
  ? hooksConfig.hooks : {};

for (const [event, entries] of Object.entries(hookTemplate.hooks)) {
  if (!Array.isArray(entries)) throw new Error(`Invalid ${clientId} hook entries for ${event}`);
  const current = Array.isArray(hooksConfig.hooks[event]) ? hooksConfig.hooks[event] : [];
  hooksConfig.hooks[event] = [
    ...current.filter((item) => !isOpenVikingHook(item)),
    ...renderHookValue(entries),
  ];
}
if (kind === "cursor") {
  if (Array.isArray(hooksConfig.hooks.postToolUse)) {
    const remaining = hooksConfig.hooks.postToolUse.filter((item) => !isOpenVikingHook(item));
    if (remaining.length) hooksConfig.hooks.postToolUse = remaining;
    else delete hooksConfig.hooks.postToolUse;
  }
}
atomicWrite(hooksPath, hooksConfig);

const mcpTemplate = readJson(path.join(root, ".mcp.json"));
const templateServer = mcpTemplate.mcpServers?.openviking;
if (!templateServer || typeof templateServer !== "object" || Array.isArray(templateServer)) {
  throw new Error(`Invalid ${clientId} MCP template`);
}
const mcp = readJson(mcpPath);
mcp.mcpServers = mcp.mcpServers && typeof mcp.mcpServers === "object" && !Array.isArray(mcp.mcpServers)
  ? mcp.mcpServers : {};
function isKnownLegacyOpenVikingServer(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (value.env?.OPENVIKING_INTEGRATION_ID === "openviking-memory") return true;
  if (typeof value.url !== "string") return false;
  try {
    const url = new URL(value.url);
    const local = ["127.0.0.1", "localhost", "::1"].includes(url.hostname)
      && url.port === "1933" && url.pathname.replace(/\/$/u, "") === "/mcp";
    const cloud = url.hostname === "api.vikingdb.cn-beijing.volces.com"
      && url.pathname.replace(/\/$/u, "") === "/openviking/mcp";
    return local || cloud;
  } catch {
    return false;
  }
}
// Migrate only the exact OpenViking endpoints published by the earlier manual
// guides. A coincidentally named third-party server must remain untouched.
if (isKnownLegacyOpenVikingServer(mcp.mcpServers["ov-mcp-server"])) {
  delete mcp.mcpServers["ov-mcp-server"];
}
const server = {
  ...templateServer,
  command: nodeBin,
  args: [path.join(root, "servers", "mcp-proxy.mjs")],
  env: { ...(templateServer.env || {}), ...integrationEnv },
};
mcp.mcpServers.openviking = server;
atomicWrite(mcpPath, mcp);

const installedManifestPath = path.join(root, "integration.json");
const previousManifest = readJson(installedManifestPath);
const now = new Date().toISOString();
const unchangedInstall = previousManifest.version === packageManifest.version
  && previousManifest.source === sourceMode
  && previousManifest.hooksConfig === hooksPath
  && previousManifest.mcpConfig === mcpPath;
atomicWrite(installedManifestPath, {
  schemaVersion: 1,
  id: packageManifest.id,
  version: packageManifest.version,
  client: clientId,
  installMode: "managed-native",
  source: sourceMode,
  capabilities: packageManifest.capabilities,
  hooksConfig: hooksPath,
  mcpConfig: mcpPath,
  installedAt: previousManifest.installedAt || now,
  updatedAt: unchangedInstall ? previousManifest.updatedAt || previousManifest.installedAt || now : now,
});
NODE
}

agent_remove_json_configs() { # agent_remove_json_configs <hooks> <mcp>
  local hooks_path="$1" mcp_path="$2"
  "$NODE_BIN" - "$hooks_path" "$mcp_path" <<'NODE'
const fs = require("node:fs");
const [hooksPath, mcpPath] = process.argv.slice(2);
function read(file) {
  if (!fs.existsSync(file)) return null;
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("top-level value must be an object");
    }
    return parsed;
  } catch (error) {
    throw new Error(`Cannot safely update ${file}: ${error.message}`);
  }
}
function write(file, value) {
  const next = JSON.stringify(value, null, 2) + "\n";
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, next, { mode: 0o600 });
  fs.renameSync(tmp, file);
}
function ownsHook(value) {
  const text = JSON.stringify(value || {});
  return text.includes("openviking") && [
    "cursor-hook.mjs",
    "trae-hook.mjs",
    "session-start.mjs",
    "auto-recall.mjs",
    "auto-capture.mjs",
    "pre-compact.mjs",
    "session-end.mjs",
    "trae-auto-recall.mjs",
    "trae-auto-capture.mjs",
    "claude-code-memory-plugin/scripts/session-start.mjs",
  ].some((name) => text.includes(name));
}
const hooks = read(hooksPath);
const mcp = read(mcpPath);
if (hooks?.hooks && typeof hooks.hooks === "object") {
  for (const event of Object.keys(hooks.hooks)) {
    if (!Array.isArray(hooks.hooks[event])) continue;
    hooks.hooks[event] = hooks.hooks[event].filter((item) => !ownsHook(item));
    if (hooks.hooks[event].length === 0) delete hooks.hooks[event];
  }
  write(hooksPath, hooks);
}
if (mcp?.mcpServers?.openviking) {
  const text = JSON.stringify(mcp.mcpServers.openviking);
  if (text.includes("agent-integrations") && text.includes("mcp-proxy.mjs")) {
    delete mcp.mcpServers.openviking;
    write(mcpPath, mcp);
  }
}
NODE
}

uninstall_agent_integrations() {
  if contains_harness cursor; then
    agent_remove_json_configs "$HOME/.cursor/hooks.json" "$(cursor_mcp_path)"
    rm -f "$HOME/.cursor/rules/openviking-memory.mdc"
    rm -rf "$HOME/.cursor/skills/openviking-memory"
    rm -rf "$OV_HOME/agent-integrations/cursor"
    info "$(t 'Removed the Cursor OpenViking integration.' '已移除 Cursor OpenViking 集成。')"
  fi
  if contains_harness trae; then
    agent_remove_json_configs "$HOME/.trae/hooks.json" "$(trae_mcp_path trae)"
    rm -rf "$OV_HOME/agent-integrations/trae"
    info "$(t 'Removed TRAE OpenViking hooks and MCP config.' '已移除 TRAE OpenViking hooks 与 MCP 配置。')"
  fi
  if contains_harness trae-cn; then
    agent_remove_json_configs "$HOME/.trae-cn/hooks.json" "$(trae_mcp_path trae-cn)"
    rm -rf "$OV_HOME/agent-integrations/trae-cn"
    info "$(t 'Removed TRAE CN OpenViking hooks and MCP config.' '已移除 TRAE CN OpenViking hooks 与 MCP 配置。')"
  fi
  if [ ! -d "$OV_HOME/agent-integrations/cursor" ] \
    && [ ! -d "$OV_HOME/agent-integrations/trae" ] \
    && [ ! -d "$OV_HOME/agent-integrations/trae-cn" ]; then
    rm -rf "$OV_HOME/agent-integrations/memory-plugin-shared"
  fi
}

cursor_mcp_path() {
  printf '%s' "$HOME/.cursor/mcp.json"
}

cursor_legacy_claude_plugins() {
  local registry="$HOME/.claude/plugins/installed_plugins.json"
  [ -f "$registry" ] || return 0
  "$NODE_BIN" - "$registry" "$PLUGIN_ID" <<'NODE' 2>/dev/null || true
const fs = require("node:fs");
const [file, currentId] = process.argv.slice(2);
const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
const ids = Object.keys(parsed.plugins || {}).filter((id) => /openviking/i.test(id) && id !== currentId);
process.stdout.write(ids.join(", "));
NODE
}

trae_mcp_path() { # trae_mcp_path <client-id>
  local client_id="$1"
  if [ "$(uname -s)" = "Darwin" ]; then
    if [ "$client_id" = "trae-cn" ]; then
      printf '%s' "$HOME/Library/Application Support/Trae CN/User/mcp.json"
    else
      printf '%s' "$HOME/Library/Application Support/Trae/User/mcp.json"
    fi
  elif [ "$client_id" = "trae-cn" ]; then
    printf '%s' "$HOME/.trae-cn/mcp.json"
  else
    printf '%s' "$HOME/.trae/mcp.json"
  fi
}

install_cursor() {
  heading "$(t '4. Cursor integration' '4. Cursor 集成')"
  local root hooks_path mcp_path skill_tmp legacy_plugins
  root="$(assemble_agent_integration cursor-memory-plugin cursor)" || return 1
  hooks_path="$HOME/.cursor/hooks.json"
  mcp_path="$(cursor_mcp_path)"
  agent_write_json_configs cursor "$hooks_path" "$mcp_path" "$root" cursor "$NODE_BIN"
  mkdir -p "$HOME/.cursor/rules" "$HOME/.cursor/skills"
  cp "$root/rules/openviking-memory.mdc" "$HOME/.cursor/rules/openviking-memory.mdc"
  skill_tmp="$HOME/.cursor/skills/openviking-memory.tmp"
  rm -rf "$skill_tmp"
  cp -R "$root/skills/openviking-memory" "$skill_tmp"
  rm -rf "$HOME/.cursor/skills/openviking-memory"
  mv "$skill_tmp" "$HOME/.cursor/skills/openviking-memory"
  info "$(t 'Cursor hooks installed:' 'Cursor hooks 已安装：') $hooks_path"
  info "$(t 'Cursor MCP installed:' 'Cursor MCP 已安装：') $mcp_path"
  info "$(t 'Cursor Rule and Skill installed under ~/.cursor.' 'Cursor Rule 与 Skill 已安装到 ~/.cursor。')"
  legacy_plugins="$(cursor_legacy_claude_plugins)"
  if [ -n "$legacy_plugins" ]; then
    warn "$(t 'Cursor may also import these older Claude OpenViking plugins and run duplicate Hooks:' 'Cursor 还可能导入以下旧版 Claude OpenViking 插件并重复执行 Hook：') $legacy_plugins"
    warn "$(t 'Upgrade or remove those legacy plugin ids, then restart Cursor.' '请升级或移除这些旧插件 id，然后重启 Cursor。')"
  fi
}

install_trae_variant() { # install_trae_variant <trae|trae-cn>
  local client_id="$1" root hooks_path mcp_path
  root="$(assemble_agent_integration trae-memory-hooks "$client_id")" || return 1
  hooks_path="$HOME/.$client_id/hooks.json"
  mcp_path="$(trae_mcp_path "$client_id")"
  agent_write_json_configs trae "$hooks_path" "$mcp_path" "$root" "$client_id" "$NODE_BIN"
  info "$client_id hooks: $hooks_path"
  info "$client_id MCP: $mcp_path"
}

# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

install_opencode() {
  heading "$(t '4. OpenCode plugin' '4. OpenCode 插件')"
  if ! command -v opencode >/dev/null 2>&1; then
    warn "$(t 'opencode CLI not found; skipping OpenCode install.' '未找到 opencode 命令，跳过 OpenCode 安装。')"
    return 0
  fi
  case "$SOURCE_MODE" in
    remote)
      opencode_register_npm_plugin
      ;;
    archive|dev)
      opencode_install_file_plugin
      ;;
  esac
}

opencode_config_file() {
  local json="$HOME/.config/opencode/opencode.json" jsonc="$HOME/.config/opencode/opencode.jsonc"
  if [ -f "$jsonc" ] && grep -q '"plugin"' "$jsonc" 2>/dev/null; then printf '%s' "$jsonc"; return; fi
  if [ -f "$json" ]; then printf '%s' "$json"; return; fi
  if [ -f "$jsonc" ]; then printf '%s' "$jsonc"; return; fi
  printf '%s' "$json"
}

opencode_register_npm_plugin() {
  local cfg plugin_dir proxy_root proxy
  cfg="$(opencode_config_file)"
  plugin_dir="$(plugin_dir_on_disk opencode-plugin)" || {
    warn "$(t 'OpenCode plugin sources not found; registering npm package without MCP fallback.' '未找到 OpenCode 插件源码；仅注册 npm 包，不写 MCP fallback。')"
    opencode_write_config "$cfg" "@openviking/opencode-plugin" ""
    return 0
  }
  proxy_root="$OV_HOME/opencode-mcp-proxy/openviking"
  proxy="$(opencode_install_mcp_proxy_snapshot "$plugin_dir" "$proxy_root")"
  opencode_write_config "$cfg" "@openviking/opencode-plugin" "$proxy"
  info "$(t 'OpenCode plugin registered in' 'OpenCode 插件已注册到：') $cfg"
}

opencode_install_mcp_proxy_snapshot() {
  local plugin_dir="$1" dest="$2"
  rm -rf "$dest.tmp"
  mkdir -p "$dest.tmp"
  (cd "$plugin_dir" && tar --exclude node_modules --exclude .git -cf - package.json lib servers) | (cd "$dest.tmp" && tar -xf -)
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  mv "$dest.tmp" "$dest"
  printf '%s' "$dest/servers/mcp-proxy.mjs"
}

opencode_write_config() {
  local cfg="$1" plugin_spec="$2" mcp_proxy="$3"
  mkdir -p "$(dirname "$cfg")"
  [ -f "$cfg" ] || printf '{\n}\n' > "$cfg"
  cp "$cfg" "$cfg.bak.$(date +%Y%m%d-%H%M%S)"
  node - "$cfg" "$plugin_spec" "$mcp_proxy" <<'NODE'
const fs = require("node:fs");
const file = process.argv[2];
const pluginSpec = process.argv[3] || "";
const mcpProxy = process.argv[4] || "";
let raw = "";
try { raw = fs.readFileSync(file, "utf8"); } catch {}

function stripJsonc(s) {
  let out = "";
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    const next = s[i + 1];
    if (ch === '"' || ch === "'") {
      const end = readStringEnd(s, i);
      out += s.slice(i, end);
      i = end;
    } else if (ch === "/" && next === "/") {
      i += 2;
      while (i < s.length && s[i] !== "\n") i++;
    } else if (ch === "/" && next === "*") {
      i += 2;
      while (i < s.length && !(s[i] === "*" && s[i + 1] === "/")) i++;
      i = Math.min(s.length, i + 2);
    } else {
      out += ch;
      i++;
    }
  }
  return out.replace(/,\s*([}\]])/g, "$1");
}

function readStringEnd(s, start) {
  const quote = s[start];
  let i = start + 1;
  while (i < s.length) {
    if (s[i] === "\\") {
      i += 2;
    } else if (s[i] === quote) {
      return i + 1;
    } else {
      i++;
    }
  }
  return s.length;
}

function skipTrivia(s, i, end = s.length) {
  while (i < end) {
    if (/\s/.test(s[i])) {
      i++;
    } else if (s[i] === "/" && s[i + 1] === "/") {
      i += 2;
      while (i < end && s[i] !== "\n") i++;
    } else if (s[i] === "/" && s[i + 1] === "*") {
      i += 2;
      while (i < end && !(s[i] === "*" && s[i + 1] === "/")) i++;
      i = Math.min(end, i + 2);
    } else {
      break;
    }
  }
  return i;
}

function parseStringLiteral(s, start) {
  const end = readStringEnd(s, start);
  try {
    return { value: JSON.parse(s.slice(start, end)), end };
  } catch {
    return { value: "", end };
  }
}

function findTopLevelObject(s) {
  const start = skipTrivia(s, 0);
  if (s[start] !== "{") return null;
  let depth = 0;
  let i = start;
  while (i < s.length) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i);
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") {
      depth--;
      if (depth === 0 && s[i] === "}") return { start, end: i };
    }
    i++;
  }
  return null;
}

function findObjectRangeAt(s, start, end = s.length) {
  const objectStart = skipTrivia(s, start, end);
  if (s[objectStart] !== "{") return null;
  let depth = 0;
  let i = objectStart;
  while (i < end) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, end);
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") {
      depth--;
      if (depth === 0 && s[i] === "}") return { start: objectStart, end: i };
    }
    i++;
  }
  return null;
}

function findArrayRangeAt(s, start, end = s.length) {
  const arrayStart = skipTrivia(s, start, end);
  if (s[arrayStart] !== "[") return null;
  let depth = 0;
  let i = arrayStart;
  while (i < end) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, end);
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") {
      depth--;
      if (depth === 0 && s[i] === "]") return { start: arrayStart, end: i };
    }
    i++;
  }
  return null;
}

function findTopLevelProperty(s, objectRange, name) {
  let depth = 1;
  let i = objectRange.start + 1;
  while (i < objectRange.end) {
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, objectRange.end);
      continue;
    }
    if (s[i] === '"' || s[i] === "'") {
      const keyStart = i;
      const parsed = parseStringLiteral(s, i);
      i = parsed.end;
      const afterKey = skipTrivia(s, i, objectRange.end);
      if (depth === 1 && parsed.value === name && s[afterKey] === ":") {
        const valueStart = skipTrivia(s, afterKey + 1, objectRange.end);
        return {
          keyStart,
          valueStart,
          replaceEnd: findPropertyReplaceEnd(s, valueStart, objectRange.end),
        };
      }
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") depth--;
    i++;
  }
  return null;
}

function findPropertyReplaceEnd(s, valueStart, objectEnd) {
  let depth = 0;
  let i = skipTrivia(s, valueStart, objectEnd);
  let lastTokenEnd = i;
  while (i < objectEnd) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      lastTokenEnd = i;
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, objectEnd);
      continue;
    }
    if (depth === 0 && s[i] === ",") return lastTokenEnd;
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") depth--;
    if (!/\s/.test(s[i])) lastTokenEnd = i + 1;
    i++;
  }
  return lastTokenEnd;
}

function findLineIndent(s, index) {
  const lineStart = s.lastIndexOf("\n", index - 1) + 1;
  const prefix = s.slice(lineStart, index);
  return /^[ \t]*$/.test(prefix) ? prefix : "";
}

function detectPropertyIndent(s, objectRange) {
  let i = objectRange.start + 1;
  while (i < objectRange.end) {
    i = skipTrivia(s, i, objectRange.end);
    if (s[i] === '"' || s[i] === "'") return findLineIndent(s, i) || "  ";
    if (s[i] === "{" || s[i] === "[") break;
    i++;
  }
  const closeIndent = findLineIndent(s, objectRange.end);
  return `${closeIndent}  `;
}

function hasTopLevelProperty(s, objectRange) {
  let i = objectRange.start + 1;
  while (i < objectRange.end) {
    i = skipTrivia(s, i, objectRange.end);
    if (s[i] === '"' || s[i] === "'") return true;
    i++;
  }
  return false;
}

function objectEndsWithComma(s, objectRange) {
  const body = s.slice(objectRange.start + 1, objectRange.end);
  return body.trimEnd().endsWith(",");
}

function rangeHasValue(s, range) {
  let i = range.start + 1;
  while (i < range.end) {
    i = skipTrivia(s, i, range.end);
    if (i < range.end) return true;
  }
  return false;
}

function formatProperty(name, value, indent) {
  const json = JSON.stringify(value, null, 2);
  const formatted = json.split("\n").map((line, idx) => idx === 0 ? line : `${indent}${line}`).join("\n");
  return `${JSON.stringify(name)}: ${formatted}`;
}

function setPropertyInObject(s, objectRange, name, value) {
  const existing = findTopLevelProperty(s, objectRange, name);
  if (existing) {
    const indent = findLineIndent(s, existing.keyStart) || detectPropertyIndent(s, objectRange);
    return `${s.slice(0, existing.keyStart)}${formatProperty(name, value, indent)}${s.slice(existing.replaceEnd)}`;
  }

  const indent = detectPropertyIndent(s, objectRange);
  const closeIndent = findLineIndent(s, objectRange.end);
  const needsComma = hasTopLevelProperty(s, objectRange) && !objectEndsWithComma(s, objectRange);
  const prefix = needsComma ? "," : "";
  const insertion = `${prefix}\n${indent}${formatProperty(name, value, indent)}\n${closeIndent}`;
  return `${s.slice(0, objectRange.end)}${insertion}${s.slice(objectRange.end)}`;
}

function setTopLevelProperty(s, name, value) {
  let objectRange = findTopLevelObject(s);
  if (!objectRange) {
    s = "{\n}\n";
    objectRange = findTopLevelObject(s);
  }
  return setPropertyInObject(s, objectRange, name, value);
}

function setNestedObjectProperty(s, parentName, childName, childValue, fallbackParentValue) {
  let objectRange = findTopLevelObject(s);
  if (!objectRange) {
    s = "{\n}\n";
    objectRange = findTopLevelObject(s);
  }
  const parent = findTopLevelProperty(s, objectRange, parentName);
  if (!parent) return setPropertyInObject(s, objectRange, parentName, fallbackParentValue);
  const parentRange = findObjectRangeAt(s, parent.valueStart, parent.replaceEnd);
  if (!parentRange) return setPropertyInObject(s, objectRange, parentName, fallbackParentValue);
  return setPropertyInObject(s, parentRange, childName, childValue);
}

function appendStringToTopLevelArray(s, name, value) {
  let objectRange = findTopLevelObject(s);
  if (!objectRange) {
    s = "{\n}\n";
    objectRange = findTopLevelObject(s);
  }
  const prop = findTopLevelProperty(s, objectRange, name);
  if (!prop) return setPropertyInObject(s, objectRange, name, [value]);
  const arrayRange = findArrayRangeAt(s, prop.valueStart, prop.replaceEnd);
  if (!arrayRange) return setPropertyInObject(s, objectRange, name, [value]);
  const propIndent = findLineIndent(s, prop.keyStart) || detectPropertyIndent(s, objectRange);
  const itemIndent = `${propIndent}  `;
  const closeIndent = findLineIndent(s, arrayRange.end) || propIndent;
  const needsComma = rangeHasValue(s, arrayRange) && !s.slice(arrayRange.start + 1, arrayRange.end).trimEnd().endsWith(",");
  const prefix = needsComma ? "," : "";
  const insertion = `${prefix}\n${itemIndent}${JSON.stringify(value)}\n${closeIndent}`;
  return `${s.slice(0, arrayRange.end)}${insertion}${s.slice(arrayRange.end)}`;
}

let data = {};
try { data = raw.trim() ? JSON.parse(stripJsonc(raw)) : {}; } catch { data = {}; }
let nextRaw = raw.trim() ? raw : "{\n}\n";
if (pluginSpec) {
  const next = Array.isArray(data.plugin) ? data.plugin.slice() : [];
  if (!next.includes(pluginSpec)) {
    next.push(pluginSpec);
    nextRaw = appendStringToTopLevelArray(nextRaw, "plugin", pluginSpec);
  }
  data.plugin = next;
}
if (mcpProxy) {
  data.mcp = data.mcp && typeof data.mcp === "object" && !Array.isArray(data.mcp) ? data.mcp : {};
  if (!data.mcp.openviking || data.mcp.openviking.enabled !== false) {
    data.mcp.openviking = {
      type: "local",
      command: ["node", mcpProxy],
      enabled: true,
      timeout: 15000,
    };
    nextRaw = setNestedObjectProperty(nextRaw, "mcp", "openviking", data.mcp.openviking, data.mcp);
  }
}
if (!nextRaw.endsWith("\n")) nextRaw += "\n";
fs.writeFileSync(file, nextRaw);
NODE
}

opencode_install_file_plugin() {
  local plugin_dir dest
  plugin_dir="$(plugin_dir_on_disk opencode-plugin)" || {
    warn "$(t 'OpenCode plugin sources not found; skipping.' '未找到 OpenCode 插件源码，跳过。')"
    return 0
  }
  dest="$HOME/.config/opencode/plugins/openviking"
  mkdir -p "$(dirname "$dest")"
  if [ "$SOURCE_MODE" = "dev" ]; then
    rm -rf "$dest"
    ln -sfn "$plugin_dir" "$dest"
  else
    rm -rf "$dest.tmp" "$dest"
    mkdir -p "$dest.tmp"
    (cd "$plugin_dir" && tar --exclude node_modules --exclude .git -cf - .) | (cd "$dest.tmp" && tar -xf -)
    mv "$dest.tmp" "$dest"
  fi
  if [ -f "$plugin_dir/wrappers/openviking.js" ]; then
    cp "$plugin_dir/wrappers/openviking.js" "$HOME/.config/opencode/plugins/openviking.js"
  else
    printf '%s\n' 'export { OpenVikingPlugin, default } from "./openviking/index.mjs"' > "$HOME/.config/opencode/plugins/openviking.js"
  fi
  opencode_write_config "$(opencode_config_file)" "" "$dest/servers/mcp-proxy.mjs"
  info "$(t 'OpenCode file plugin installed:' 'OpenCode 文件插件已安装：') $dest"
}

# ---------------------------------------------------------------------------
# pi
# ---------------------------------------------------------------------------

install_pi() {
  heading "$(t '4. pi extension' '4. pi 扩展')"
  if ! command -v pi >/dev/null 2>&1; then
    warn "$(t 'pi CLI not found; skipping pi extension install.' '未找到 pi 命令，跳过 pi 扩展安装。')"
    return 0
  fi
  local plugin_dir dest tmp keep_config
  plugin_dir="$(plugin_dir_on_disk pi-coding-agent-extension)" || {
    warn "$(t 'pi extension sources not found; skipping.' '未找到 pi 扩展源码，跳过。')"
    return 0
  }
  dest="$HOME/.pi/agent/extensions/openviking"
  tmp="$dest.tmp"
  keep_config=""
  [ -f "$dest/config.json" ] && keep_config="$dest/config.json"
  rm -rf "$tmp"
  mkdir -p "$tmp"
  (cd "$plugin_dir" && tar --exclude node_modules --exclude .git -cf - .) | (cd "$tmp" && tar -xf -)
  if [ -n "$keep_config" ]; then
    cp "$keep_config" "$tmp/config.json"
  fi
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  mv "$tmp" "$dest"
  pi install "$dest" || warn "$(t 'pi extension copied but pi install registration failed; run pi install manually.' 'pi 扩展文件已复制，但 pi install 注册失败；请手动运行 pi install。')"
  info "$(t 'pi extension installed:' 'pi 扩展已安装：') $dest"
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

validate_install() {
  heading "$(t '5. Validation' '5. 安装校验')"
  local ok=1 agent_fatal=0 cached list bin
  if contains_harness claude; then
    while IFS= read -r bin; do
      [ -n "$bin" ] || continue
      command -v "$bin" >/dev/null 2>&1 || continue
      CLAUDE_BIN="$bin"
      if has_plugin_subcommand; then
        list="$(claude_cmd plugin list 2>/dev/null || true)"
        if str_contains "$list" "$PLUGIN_NAME"; then
          info "$CLAUDE_BIN: $PLUGIN_NAME $(t 'visible in plugin list' '已出现在插件列表')"
        else
          warn "$CLAUDE_BIN: $PLUGIN_NAME $(t 'not visible in plugin list' '未出现在插件列表')"
          ok=0
        fi
      fi
    done <<EOF
$CLAUDE_BINS
EOF
  fi
  if contains_harness codex; then
    while IFS= read -r bin; do
      [ -n "$bin" ] || continue
      command -v "$bin" >/dev/null 2>&1 || continue
      CODEX_BIN="$bin"
      list="$(codex_cmd plugin list 2>/dev/null || true)"
      if str_contains "$list" "$PLUGIN_NAME"; then
        info "$CODEX_BIN: $PLUGIN_NAME $(t 'visible in plugin list' '已出现在插件列表')"
      else
        warn "$CODEX_BIN: $PLUGIN_NAME $(t 'not visible in plugin list' '未出现在插件列表')"
        ok=0
      fi
      if is_native_codex_bin; then
        cached=$(find "$HOME/.codex/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME" -name 'mcp-proxy.mjs' -path '*/servers/*' 2>/dev/null | sort | tail -n 1)
        if [ -n "$cached" ]; then
          node --check "$cached" && info "codex: $(t 'cached stdio proxy parses' '缓存中的 stdio 代理语法正常') ($cached)" || ok=0
        fi
      fi
    done <<EOF
$CODEX_BINS
EOF
  fi
  if contains_harness cursor; then
    if grep -q 'scripts/session-start.mjs' "$HOME/.cursor/hooks.json" 2>/dev/null \
      && grep -q 'scripts/auto-recall.mjs' "$HOME/.cursor/hooks.json" 2>/dev/null \
      && grep -q 'scripts/auto-capture.mjs' "$HOME/.cursor/hooks.json" 2>/dev/null \
      && grep -q 'scripts/uri-guard.mjs' "$HOME/.cursor/hooks.json" 2>/dev/null \
      && grep -q 'OPENVIKING_INTEGRATION_ID' "$HOME/.cursor/hooks.json" 2>/dev/null \
      && grep -q 'mcp-proxy.mjs' "$HOME/.cursor/mcp.json" 2>/dev/null \
      && [ -f "$OV_HOME/agent-integrations/cursor/scripts/cursor-hook.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/cursor/scripts/uri-guard.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/memory-plugin-shared/lib/agent-uri-guard.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/cursor/.cursor-plugin/plugin.json" ] \
      && [ -f "$OV_HOME/agent-integrations/cursor/integration.json" ] \
      && [ -f "$HOME/.cursor/rules/openviking-memory.mdc" ] \
      && [ -f "$HOME/.cursor/skills/openviking-memory/SKILL.md" ]; then
      "$NODE_BIN" --check "$OV_HOME/agent-integrations/cursor/scripts/cursor-hook.mjs" \
        || { ok=0; agent_fatal=1; }
      "$NODE_BIN" --check "$OV_HOME/agent-integrations/cursor/scripts/uri-guard.mjs" \
        || { ok=0; agent_fatal=1; }
      if printf '%s' '{}' | env HOME="$HOME" OPENVIKING_MEMORY_ENABLED=0 \
        "$NODE_BIN" "$OV_HOME/agent-integrations/cursor/scripts/session-start.mjs" >/dev/null; then
        info "cursor: $(t 'installed Hook runtime passed its smoke test' '已安装的 Hook 运行时通过 smoke test')"
      else
        warn "cursor: $(t 'installed Hook runtime failed its smoke test' '已安装的 Hook 运行时 smoke test 失败')"
        ok=0; agent_fatal=1
      fi
      info "cursor: $(t 'integration installed (Hooks, MCP, Rule, Skill)' '集成已安装（Hook、MCP、Rule、Skill）')"
    else
      warn "cursor: $(t 'OpenViking integration installation is incomplete' 'OpenViking 集成安装不完整')"
      ok=0; agent_fatal=1
    fi
  fi
  if contains_harness trae; then
    local trae_mcp
    trae_mcp="$(trae_mcp_path trae)"
    if grep -q 'scripts/session-start.mjs' "$HOME/.trae/hooks.json" 2>/dev/null \
      && grep -q 'scripts/auto-recall.mjs' "$HOME/.trae/hooks.json" 2>/dev/null \
      && grep -q 'scripts/auto-capture.mjs' "$HOME/.trae/hooks.json" 2>/dev/null \
      && grep -q 'scripts/uri-guard.mjs' "$HOME/.trae/hooks.json" 2>/dev/null \
      && grep -q 'OPENVIKING_INTEGRATION_ID' "$HOME/.trae/hooks.json" 2>/dev/null \
      && grep -q 'mcp-proxy.mjs' "$trae_mcp" 2>/dev/null \
      && [ -f "$OV_HOME/agent-integrations/trae/scripts/trae-hook.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/trae/scripts/uri-guard.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/trae/integration.json" ]; then
      "$NODE_BIN" --check "$OV_HOME/agent-integrations/trae/scripts/trae-hook.mjs" \
        || { ok=0; agent_fatal=1; }
      "$NODE_BIN" --check "$OV_HOME/agent-integrations/trae/scripts/uri-guard.mjs" \
        || { ok=0; agent_fatal=1; }
      if ! printf '%s' '{}' | env HOME="$HOME" OPENVIKING_MEMORY_ENABLED=0 \
        "$NODE_BIN" "$OV_HOME/agent-integrations/trae/scripts/session-start.mjs" trae >/dev/null; then
        warn "trae: $(t 'installed Hook runtime failed its smoke test' '已安装的 Hook 运行时 smoke test 失败')"
        ok=0; agent_fatal=1
      fi
      info "trae: $(t 'hooks and MCP are configured' 'hooks 与 MCP 已配置')"
    else
      warn "trae: $(t 'OpenViking hook or MCP config is incomplete' 'OpenViking hook 或 MCP 配置不完整')"
      ok=0; agent_fatal=1
    fi
  fi
  if contains_harness trae-cn; then
    local trae_cn_mcp
    trae_cn_mcp="$(trae_mcp_path trae-cn)"
    if grep -q 'scripts/session-start.mjs' "$HOME/.trae-cn/hooks.json" 2>/dev/null \
      && grep -q 'scripts/auto-recall.mjs' "$HOME/.trae-cn/hooks.json" 2>/dev/null \
      && grep -q 'scripts/auto-capture.mjs' "$HOME/.trae-cn/hooks.json" 2>/dev/null \
      && grep -q 'scripts/uri-guard.mjs' "$HOME/.trae-cn/hooks.json" 2>/dev/null \
      && grep -q 'OPENVIKING_INTEGRATION_ID' "$HOME/.trae-cn/hooks.json" 2>/dev/null \
      && grep -q 'mcp-proxy.mjs' "$trae_cn_mcp" 2>/dev/null \
      && [ -f "$OV_HOME/agent-integrations/trae-cn/scripts/trae-hook.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/trae-cn/scripts/uri-guard.mjs" ] \
      && [ -f "$OV_HOME/agent-integrations/trae-cn/integration.json" ]; then
      "$NODE_BIN" --check "$OV_HOME/agent-integrations/trae-cn/scripts/trae-hook.mjs" \
        || { ok=0; agent_fatal=1; }
      "$NODE_BIN" --check "$OV_HOME/agent-integrations/trae-cn/scripts/uri-guard.mjs" \
        || { ok=0; agent_fatal=1; }
      if ! printf '%s' '{}' | env HOME="$HOME" OPENVIKING_MEMORY_ENABLED=0 \
        "$NODE_BIN" "$OV_HOME/agent-integrations/trae-cn/scripts/session-start.mjs" trae-cn >/dev/null; then
        warn "trae-cn: $(t 'installed Hook runtime failed its smoke test' '已安装的 Hook 运行时 smoke test 失败')"
        ok=0; agent_fatal=1
      fi
      info "trae-cn: $(t 'hooks and MCP are configured' 'hooks 与 MCP 已配置')"
    else
      warn "trae-cn: $(t 'OpenViking hook or MCP config is incomplete' 'OpenViking hook 或 MCP 配置不完整')"
      ok=0; agent_fatal=1
    fi
  fi
  if contains_harness opencode; then
    local ocfg="$HOME/.config/opencode/opencode.json"
    local ocfgc="$HOME/.config/opencode/opencode.jsonc"
    if grep -q '@openviking/opencode-plugin' "$ocfg" "$ocfgc" 2>/dev/null || { [ -f "$HOME/.config/opencode/plugins/openviking.js" ] && [ -f "$HOME/.config/opencode/plugins/openviking/index.mjs" ]; }; then
      info "opencode: $PLUGIN_NAME $(t 'appears installed' '看起来已安装')"
    else
      warn "opencode: $PLUGIN_NAME $(t 'not found in config/plugin dir' '未在配置或插件目录中找到')"
      ok=0
    fi
    if [ -f "$HOME/.config/opencode/plugins/openviking.js" ]; then
      node --check "$HOME/.config/opencode/plugins/openviking.js" || ok=0
    fi
    if [ -f "$HOME/.config/opencode/plugins/openviking/index.mjs" ]; then
      node --check "$HOME/.config/opencode/plugins/openviking/index.mjs" || ok=0
    fi
    if [ -f "$HOME/.config/opencode/plugins/openviking/servers/mcp-proxy.mjs" ]; then
      node --check "$HOME/.config/opencode/plugins/openviking/servers/mcp-proxy.mjs" || ok=0
    elif [ -f "$OV_HOME/opencode-mcp-proxy/openviking/servers/mcp-proxy.mjs" ]; then
      node --check "$OV_HOME/opencode-mcp-proxy/openviking/servers/mcp-proxy.mjs" || ok=0
    else
      warn "opencode: $(t 'OpenViking MCP proxy not found' '未找到 OpenViking MCP proxy')"
      ok=0
    fi
    if grep -q '"openviking"' "$ocfg" "$ocfgc" 2>/dev/null && grep -q '"mcp"' "$ocfg" "$ocfgc" 2>/dev/null; then
      info "opencode: $(t 'MCP server registered' 'MCP server 已注册')"
    else
      warn "opencode: $(t 'MCP server not found in config' '配置中未找到 MCP server')"
      ok=0
    fi
  fi
  if contains_harness pi; then
    if [ -f "$HOME/.pi/agent/extensions/openviking/index.ts" ] || [ -f "$HOME/.pi/agent/extensions/openviking/index.js" ]; then
      info "pi: $PLUGIN_NAME $(t 'extension files present' '扩展文件已存在')"
    else
      warn "pi: $PLUGIN_NAME $(t 'extension files not found' '未找到扩展文件')"
      ok=0
    fi
    if command -v pi >/dev/null 2>&1; then
      if pi list 2>/dev/null | grep -q 'extensions/openviking'; then
        info "pi: $PLUGIN_NAME $(t 'registered in pi settings' '已注册到 pi settings')"
      else
        warn "pi: $PLUGIN_NAME $(t 'not registered in pi settings' '未注册到 pi settings')"
        ok=0
      fi
    fi
    if [ -f "$HOME/.pi/agent/extensions/openviking/shared/recall-core.mjs" ]; then
      node --check "$HOME/.pi/agent/extensions/openviking/shared/recall-core.mjs" || ok=0
    fi
  fi
  if [ -n "$MKT_DIR" ] && [ -f "$MKT_DIR/claude-code-memory-plugin/scripts/marketplace.test.mjs" ] && [ -d "$MKT_DIR/../.git" ]; then
    node --test "$MKT_DIR/claude-code-memory-plugin/scripts/marketplace.test.mjs" \
      "$MKT_DIR/codex-memory-plugin/scripts/marketplace.test.mjs" || ok=0
  fi
  if [ "$agent_fatal" -ne 0 ]; then
    err "$(t 'Installation validation failed. No success result will be reported.' '安装校验失败，不会报告安装成功。')"
    return 1
  fi
  if [ "$ok" -ne 1 ]; then
    warn "$(t 'Validation reported issues — the install may still work; check the messages above.' '校验发现问题——安装可能仍然可用，请检查上方输出。')"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

select_language

heading "$(t '1. Environment check' '1. 环境检查')"
case "$(uname -s)" in
  Darwin|Linux) info "OS: $(uname -s)" ;;
  *) err "Unsupported OS: $(uname -s). Only macOS and Linux are supported."; exit 1 ;;
esac
command -v node >/dev/null 2>&1 || { err "$(t 'node not found. Install Node.js 18+.' '未找到 node，请先安装 Node.js 18+。')"; exit 1; }
NODE_BIN="$(command -v node)"
NODE_MAJOR="$("$NODE_BIN" -p 'Number(process.versions.node.split(".")[0])')"
[ "$NODE_MAJOR" -ge 18 ] || { err "Node.js 18+ required; found $("$NODE_BIN" --version)."; exit 1; }
command -v curl >/dev/null 2>&1 || warn "curl not found; archive installs may fail."

resolve_self_checkout
select_harnesses
validate_selected_harnesses
select_compatible_bins
refresh_available_harnesses
info "$(t 'Selected harnesses:' '已选择：') $(printf '%s' "$SELECTED_HARNESSES" | tr ',' ' ')"
if contains_harness claude; then info "$(t 'Claude-format commands:' 'Claude 格式命令：') $(list_words "$CLAUDE_BINS")"; fi
if contains_harness codex; then info "$(t 'Codex-format commands:' 'Codex 格式命令：') $(list_words "$CODEX_BINS")"; fi
validate_selected_bins
if [ "$UNINSTALL" -eq 1 ]; then
  uninstall_agent_integrations
  exit 0
fi
select_dist

configure_ovcli
resolve_source_mode
prepare_marketplace_dir
cleanup_rc_wrappers

if contains_harness claude; then
  while IFS= read -r CLAUDE_BIN; do
    [ -n "$CLAUDE_BIN" ] || continue
    install_claude
  done <<EOF
$CLAUDE_BINS
EOF
fi
if contains_harness codex; then
  while IFS= read -r CODEX_BIN; do
    [ -n "$CODEX_BIN" ] || continue
    install_codex
  done <<EOF
$CODEX_BINS
EOF
fi
if contains_harness cursor; then install_cursor; fi
if contains_harness trae; then install_trae_variant trae; fi
if contains_harness trae-cn; then install_trae_variant trae-cn; fi
if contains_harness opencode; then install_opencode; fi
if contains_harness pi; then install_pi; fi
validate_install

heading "$(t 'Done' '完成')"
info "$(t 'Credentials:' '凭据：') $OVCLI_CONF"
case "$SOURCE_MODE" in
  remote) if contains_harness claude || contains_harness codex; then info "Marketplace: remote ($REPO_URL @ $REPO_REF)"; fi ;;
  *) if contains_harness claude || contains_harness codex; then info "Marketplace: ${MKT_DIR:-$CODEX_TOS_GIT_URL}"; fi ;;
esac
if contains_harness claude; then info "Claude-format: $(list_words "$CLAUDE_BINS") -> $PLUGIN_ID"; fi
if contains_harness codex; then info "Codex-format:  $(list_words "$CODEX_BINS") -> $PLUGIN_ID"; fi
if contains_harness cursor; then info "Cursor: Hooks + MCP + Rule + Skill"; fi
if contains_harness trae; then info "TRAE: ~/.trae/hooks.json + MCP"; fi
if contains_harness trae-cn; then info "TRAE CN: ~/.trae-cn/hooks.json + MCP"; fi
if contains_harness opencode; then info "OpenCode: @openviking/opencode-plugin"; fi
if contains_harness pi; then info "pi: ~/.pi/agent/extensions/openviking"; fi
