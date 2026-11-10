#!/bin/sh
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

# OpenViking SuperAgent installer.
#
#   curl -fsSL <download base>/install.sh | sh
#   sh scripts/install.sh --file dist/superagent-<version>-<platform>.tar.gz   # a local build
#
# Installs the release for this machine under ~/.ov-superagent (releases/<version>,
# the `current` link, bin/ov-superagent) and then runs `ov-superagent setup` when the machine
# has no instance yet. Needs only sh, curl, tar and sha256sum or shasum: the
# release brings its own Node, pm2 and lark-cli, so nothing else on the
# machine is used or changed.
#
# Options: --version <v> (default: latest), --file <archive>, --no-setup,
# --lang <zh|en>.
# Environment: OV_SUPERAGENT_DOWNLOAD_BASE overrides where releases are downloaded from;
# OV_SUPERAGENT_LANG picks the language; NO_COLOR turns the colours off.
set -eu
# Always ${NAME} before non-ASCII text: macOS /bin/sh (bash 3.2) counts
# UTF-8 lead bytes as name characters, so "$VERSION（" names another variable.

BASE="${OV_SUPERAGENT_DOWNLOAD_BASE:-__OV_SUPERAGENT_DOWNLOAD_BASE__}"
OV_SUPERAGENT_HOME="$HOME/.ov-superagent"
VERSION="${OV_SUPERAGENT_VERSION:-}"
FILE=""
RUN_SETUP=1
LANG_FLAG=""

# Chinese or English, as the ov-superagent command picks it (packages/cli/src/i18n.mjs):
# OV_SUPERAGENT_LANG or --lang, else what setup saved, else Chinese.
# m <Chinese> <English> prints the one in use.
UI_LANG=${OV_SUPERAGENT_LANG:-}
case "$UI_LANG" in zh|en) ;; *) UI_LANG=$(cat "$OV_SUPERAGENT_HOME/language" 2>/dev/null || true) ;; esac
case "$UI_LANG" in zh|en) ;; *) UI_LANG=zh ;; esac
m() { if [ "$UI_LANG" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# Colour and motion only on a terminal; piped output stays plain lines.
if [ -t 2 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ]; then FANCY=1; else FANCY=0; fi
ESC=$(printf '\033')
if [ "$FANCY" = 1 ]; then
  RESET="${ESC}[0m"; BOLD="${ESC}[1m"; DIM="${ESC}[2m"
  PINK="${ESC}[38;5;213m"; VIOLET="${ESC}[38;5;177m"; LILAC="${ESC}[38;5;141m"; SKY="${ESC}[38;5;117m"
  SEA="${ESC}[38;5;39m"; HULL="${ESC}[38;5;216m"; SKIN="${ESC}[38;5;223m"; GRAY="${ESC}[38;5;244m"
  GREEN="${ESC}[38;5;114m"; YELLOW="${ESC}[38;5;221m"; RED="${ESC}[38;5;203m"
else
  RESET=""; BOLD=""; DIM=""; PINK=""; VIOLET=""; LILAC=""; SKY=""; SEA=""; HULL=""; SKIN=""; GRAY=""
  GREEN=""; YELLOW=""; RED=""
fi

say() { printf '%s\n' "$*" >&2; }
ok() { if [ "$FANCY" = 1 ]; then say "  ${GREEN}✔${RESET} $*"; else say "$*"; fi; }
note() { if [ "$FANCY" = 1 ]; then say "  ${GRAY}›${RESET} $*"; else say "$*"; fi; }
warn() { if [ "$FANCY" = 1 ]; then say "  ${YELLOW}⚠${RESET} $*"; else say "$*"; fi; }
die() {
  failed=$(m 'OpenViking SuperAgent 安装失败：' 'OpenViking SuperAgent install failed: ')
  if [ "$FANCY" = 1 ]; then say "  ${RED}✖${RESET} ${failed}$*"; else say "${failed}$*"; fi
  exit 1
}
# ~ for the home directory, so paths stay short.
short() { case "$1" in "$HOME"/*) printf '~%s' "${1#"$HOME"}" ;; *) printf '%s' "$1" ;; esac; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "$(m '--version 需要一个值' '--version needs a value')"; VERSION="$2"; shift 2 ;;
    --file) [ $# -ge 2 ] || die "$(m '--file 需要一个文件' '--file needs a file')"; FILE="$2"; shift 2 ;;
    --no-setup) RUN_SETUP=0; shift ;;
    --lang)
      case "${2:-}" in zh|en) UI_LANG="$2"; LANG_FLAG="$2" ;; *) die "$(m '--lang 只能是 zh 或 en' '--lang takes zh or en')" ;; esac
      shift 2
      ;;
    *) die "$(m "不认识的参数 $1" "unknown option $1")" ;;
  esac
done

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) die "$(m "不支持 $(uname -s)；Windows 请用 install-preview.ps1（预览包），或在 WSL2 里安装" "$(uname -s) is not supported; on Windows, use install-preview.ps1 (the preview package), or install inside WSL2")" ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) die "$(m "不支持 CPU 架构 $(uname -m)" "the $(uname -m) CPU architecture is not supported")" ;;
esac
if [ "$os" = linux ]; then
  # The release's Node is linked against glibc; it does not start on musl (Alpine).
  if (ldd --version 2>&1 || true) | grep -qi musl; then die "$(m '不支持 musl（Alpine 等）系统' 'musl systems (Alpine and others) are not supported')"; fi
fi
PLATFORM="$os-$arch"

command -v tar >/dev/null 2>&1 || die "$(m '需要 tar' 'tar is needed')"
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else die "$(m '需要 sha256sum 或 shasum' 'sha256sum or shasum is needed')"; fi
}
fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }
# The archive's size, for the progress bar; 0 when the server does not say.
size_of() {
  curl -fsSIL "$1" 2>/dev/null | tr -d '\r' | awk 'tolower($1) == "content-length:" { size = $2 } END { print size + 0 }'
}

TMP=$(mktemp -d)
LOCK=""
BUSY=""
cleanup() {
  [ -z "$BUSY" ] || kill "$BUSY" 2>/dev/null || true
  [ "$FANCY" = 0 ] || printf '%s[?25h' "$ESC" >&2
  rm -rf "$TMP"
  [ -z "$LOCK" ] || rmdir "$LOCK" 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

mb() { printf '%d.%d' $(($1 / 1048576)) $(($1 % 1048576 * 10 / 1048576)); }

# A run of sea, moving with the tick: the water the boat has crossed.
waves() {
  n=0
  run=""
  while [ "$n" -lt "$1" ]; do
    case $(((n + $2) % 5)) in 1) run="$run-" ;; 3) run="$run." ;; *) run="$run~" ;; esac
    n=$((n + 1))
  done
  printf '%s' "$run"
}
gap() { printf "%$1s" ''; }
dots() {
  n=0
  run=""
  while [ "$n" -lt "$1" ]; do run="${run}·"; n=$((n + 1)); done
  printf '%s' "$run"
}

# The download as a boat crossing the sea: three lines redrawn in place.
LANE=36
sail() { # bytes total tick first
  if [ "$2" -gt 0 ]; then
    at=$(($1 * LANE / $2))
    [ "$at" -le "$LANE" ] || at=$LANE
    pct=$(($1 * 100 / $2))
    [ "$pct" -le 100 ] || pct=100
    status="${BOLD}${pct}%${RESET}  ${GRAY}$(mb "$1") / $(mb "$2") MB${RESET}"
  else
    at=$(($3 % LANE))
    status="${GRAY}$(mb "$1") MB${RESET}"
  fi
  if [ $(($3 / 3 % 2)) = 0 ]; then arm='o'; else arm='\o'; fi
  [ "$4" = 1 ] || printf '%s[3A' "$ESC" >&2
  printf '\r%s[2K  %s%s|\\%s\n' "$ESC" "$(gap $((at + 2)))" "$PINK" "$RESET" >&2
  printf '\r%s[2K  %s%s%s%s|%s_\\%s\n' "$ESC" "$(gap $((at + 2 - ${#arm})))" "$SKIN" "$arm" "$VIOLET" "$LILAC" "$RESET" >&2
  printf '\r%s[2K  %s%s%s\\____/%s%s%s  %s\n' "$ESC" "$SEA" "$(waves "$at" "$3")" "$HULL" "$GRAY" "$(dots $((LANE - at)))" "$RESET" "$status" >&2
}

# Downloads $1 to $2, drawing the boat while it runs.
download() {
  if [ "$FANCY" = 0 ]; then fetch "$1" "$2"; return; fi
  total=$(size_of "$1")
  fetch "$1" "$2" &
  BUSY=$!
  tick=0
  printf '%s[?25l' "$ESC" >&2
  sail 0 "$total" 0 1
  while kill -0 "$BUSY" 2>/dev/null; do
    sleep 0.1
    tick=$((tick + 1))
    got=0
    [ ! -f "$2" ] || got=$(wc -c < "$2" | tr -d ' ')
    sail "$got" "$total" "$tick" 0
  done
  status=0
  wait "$BUSY" || status=$?
  BUSY=""
  printf '%s[3A\r%s[J%s[?25h' "$ESC" "$ESC" "$ESC" >&2
  return "$status"
}

# Runs a command in the background with a spinner; its status is returned.
spin() { # label command...
  label=$1
  shift
  if [ "$FANCY" = 0 ]; then "$@"; return; fi
  "$@" &
  BUSY=$!
  i=0
  printf '%s[?25l' "$ESC" >&2
  while kill -0 "$BUSY" 2>/dev/null; do
    case $((i % 10)) in
      0) f='⠋' ;; 1) f='⠙' ;; 2) f='⠹' ;; 3) f='⠸' ;; 4) f='⠼' ;;
      5) f='⠴' ;; 6) f='⠦' ;; 7) f='⠧' ;; 8) f='⠇' ;; *) f='⠏' ;;
    esac
    printf '\r%s[2K  %s%s%s %s' "$ESC" "$VIOLET" "$f" "$RESET" "$label" >&2
    i=$((i + 1))
    sleep 0.1
  done
  status=0
  wait "$BUSY" || status=$?
  BUSY=""
  printf '\r%s[2K%s[?25h' "$ESC" "$ESC" >&2
  return "$status"
}

header() {
  if [ "$FANCY" = 1 ]; then
    say ""
    say "  ${BOLD}${PINK}V${VIOLET}I${LILAC}K${SKY}A${RESET}  ${GRAY}$(m '安装' 'Installing') ${1} · ${PLATFORM}${RESET}"
    say ""
  else
    say "$(m "安装 OpenViking SuperAgent ${1}（${PLATFORM}）" "Installing OpenViking SuperAgent ${1} (${PLATFORM})")"
  fi
}

if [ -n "$FILE" ]; then
  [ -f "$FILE" ] || die "$(m "找不到 $FILE" "$FILE is not there")"
  ARCHIVE="$FILE"
  header "$(basename "$FILE")"
  if [ -f "$FILE.sha256" ]; then EXPECTED=$(cut -d' ' -f1 < "$FILE.sha256"); else EXPECTED=""; fi
else
  command -v curl >/dev/null 2>&1 || die "$(m '需要 curl' 'curl is needed')"
  case "$BASE" in http://*|https://*) ;; *) die "$(m '没有下载地址：设置 OV_SUPERAGENT_DOWNLOAD_BASE' 'no download address: set OV_SUPERAGENT_DOWNLOAD_BASE')" ;; esac
  if [ -z "$VERSION" ]; then
    fetch "$BASE/latest" "$TMP/latest" || die "$(m "读不到最新版本：${BASE}/latest" "cannot read the latest version: ${BASE}/latest")"
    VERSION=$(tr -d ' \r\n' < "$TMP/latest")
  fi
  case "$VERSION" in ''|*[!0-9A-Za-z._-]*) die "$(m "版本号无效：${VERSION}" "invalid version: ${VERSION}")" ;; esac
  NAME="superagent-$VERSION-$PLATFORM.tar.gz"
  header "$VERSION"
  fetch "$BASE/releases/$VERSION/$NAME.sha256" "$TMP/sha256" || die "$(m "这个版本没有 ${PLATFORM} 的安装包" "this version has no package for ${PLATFORM}")"
  EXPECTED=$(cut -d' ' -f1 < "$TMP/sha256")
  STARTED=$(date +%s)
  download "$BASE/releases/$VERSION/$NAME" "$TMP/$NAME" || die "$(m "下载失败：${NAME}" "download failed: ${NAME}")"
  ok "$(m '下载' 'Download')  ${GRAY}$(mb "$(wc -c < "$TMP/$NAME" | tr -d ' ')") MB · $(($(date +%s) - STARTED))s${RESET}"
  ARCHIVE="$TMP/$NAME"
fi
if [ -n "$EXPECTED" ]; then
  [ "$(sha256 "$ARCHIVE")" = "$EXPECTED" ] || die "$(m '安装包校验失败（SHA-256 不一致）' 'the package failed verification (SHA-256 mismatch)')"
  ok "$(m '校验' 'Verify  ')  ${GRAY}$(m 'SHA-256 一致' 'SHA-256 matches')${RESET}"
else
  note "$(m "注意：${FILE} 旁边没有 .sha256，跳过校验" "note: there is no .sha256 next to ${FILE}, so it is not verified")"
fi

# Safety net for machines that ran a pre-release build (~/.vika, ~/.ovsa or
# ~/.superagent). Nothing is migrated and no legacy data is deleted: this only
# stops the old processes and turns off their autostart so they do not run next
# to the new install. Best effort; it never fails the install, and does nothing
# when no legacy install is found.
stop_legacy_instances() {
  for legacy_name in vika ovsa superagent; do
    legacy_home="$HOME/.$legacy_name"
    [ -d "$legacy_home" ] || continue
    note "$(m "发现旧版安装 ~/.${legacy_name}：停止它的进程和开机自启（数据保留，不迁移）" "found a legacy install at ~/.${legacy_name}: stopping its processes and autostart (its data is kept, not migrated)")"
    # Autostart first, so nothing restarts what is stopped below.
    legacy_unit="$HOME/.config/systemd/user/${legacy_name}.service"
    if [ -f "$legacy_unit" ] && { [ "$legacy_name" != superagent ] || grep -q "/\.superagent/" "$legacy_unit" 2>/dev/null; }; then
      if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now "${legacy_name}.service" >/dev/null 2>&1 || true
      fi
    fi
    legacy_plist="$HOME/Library/LaunchAgents/com.${legacy_name}.agent.plist"
    if [ -f "$legacy_plist" ] && { [ "$legacy_name" != superagent ] || grep -q "/\.superagent/" "$legacy_plist" 2>/dev/null; }; then
      if command -v launchctl >/dev/null 2>&1; then
        launchctl bootout "gui/$(id -u)" "$legacy_plist" >/dev/null 2>&1 || true
      fi
      mv -f "$legacy_plist" "$legacy_plist.disabled" 2>/dev/null || true
    fi
    # The legacy release's own pm2, else one on PATH, against the legacy PM2_HOME.
    [ -d "$legacy_home/pm2" ] || continue
    legacy_pm2=""
    for candidate in "$legacy_home/current/bin/pm2" "$legacy_home"/releases/*/bin/pm2; do
      if [ -x "$candidate" ]; then legacy_pm2="$candidate"; break; fi
    done
    [ -n "$legacy_pm2" ] || legacy_pm2=$(command -v pm2 2>/dev/null || true)
    [ -n "$legacy_pm2" ] || continue
    legacy_apps=$(PM2_HOME="$legacy_home/pm2" "$legacy_pm2" jlist 2>/dev/null \
      | grep -oE '"name":"(vika|ovsa|superagent)-[A-Za-z0-9._-]*"' | sed 's/^"name":"//; s/"$//' | sort -u || true)
    for app in $legacy_apps; do
      PM2_HOME="$legacy_home/pm2" "$legacy_pm2" delete "$app" >/dev/null 2>&1 || true
    done
  done
}
stop_legacy_instances

mkdir -p "$OV_SUPERAGENT_HOME/releases"
# The updater takes the same lock while it upgrades.
mkdir "$OV_SUPERAGENT_HOME/deploy.lock" 2>/dev/null || die "$(m "另一个安装或升级正在进行（${OV_SUPERAGENT_HOME}/deploy.lock）" "another install or upgrade is running (${OV_SUPERAGENT_HOME}/deploy.lock)")"
LOCK="$OV_SUPERAGENT_HOME/deploy.lock"

PENDING="$OV_SUPERAGENT_HOME/releases/.pending-$$"
rm -rf "$PENDING"
mkdir "$PENDING"
spin "$(m '解压' 'Unpacking')" tar -xzf "$ARCHIVE" -C "$PENDING" || { rm -rf "$PENDING"; die "$(m '解压失败' 'unpacking failed')"; }
DIR=$(ls "$PENDING")
case "$DIR" in
  superagent-*-"$PLATFORM") ;;
  *) rm -rf "$PENDING"; die "$(m "安装包不是给 ${PLATFORM} 的：${DIR}" "the package is not for ${PLATFORM}: ${DIR}")" ;;
esac
VERSION=${DIR#superagent-}
VERSION=${VERSION%-"$PLATFORM"}
# Run the new release's own Node before anything points at it: a glibc or
# CPU mismatch surfaces here and the existing install is left untouched.
if ! "$PENDING/$DIR/node/bin/node" "$PENDING/$DIR/packages/cli/bin/ov-superagent.mjs" --version >/dev/null 2>&1; then
  rm -rf "$PENDING"
  die "$(m '这台机器运行不了这个版本（系统库太旧或 CPU 不匹配）' 'this machine cannot run this release (its system libraries are too old, or the CPU differs)')"
fi
ok "$(m '自检' 'Check   ')  ${GRAY}$(m '自带的 Node 能在这台机器上运行' 'the bundled Node runs on this machine')${RESET}"

CONFIG_FILE="${OV_SUPERAGENT_CONFIG:-$OV_SUPERAGENT_HOME/config.json}"
if [ ! -f "$CONFIG_FILE" ] && [ "$RUN_SETUP" = 1 ] && [ -t 1 ] && { : </dev/tty; } 2>/dev/null; then SETUP_NEXT=1; else SETUP_NEXT=0; fi
# Setup, when it runs next, asks for OpenViking itself. Otherwise nobody is
# asked: each instance's own connection, or for a first install the default
# one, must work before the release is switched.
if [ "$SETUP_NEXT" = 0 ]; then
  if ! OV_SUPERAGENT_LANG="$UI_LANG" "$PENDING/$DIR/node/bin/node" "$PENDING/$DIR/packages/cli/src/check-ov.mjs"; then
    rm -rf "$PENDING"
    die "$(m 'OpenViking 检查未通过，未切换安装版本' 'OpenViking check failed; the installed release was not changed')"
  fi
  ok "$(m 'OpenViking 已就绪' 'OpenViking is ready')"
fi

# Physical paths: /home may be a symlink, and the updater links real paths.
physical() { (cd "$1" 2>/dev/null && pwd -P) || true; }
TARGET="$OV_SUPERAGENT_HOME/releases/$VERSION"
PREVIOUS=$(physical "$OV_SUPERAGENT_HOME/current")
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" = "$(physical "$TARGET")" ]; then
  rm -rf "$PENDING"
  ok "$(m "已经是 OpenViking SuperAgent ${VERSION}" "OpenViking SuperAgent ${VERSION} is installed already")"
  CHANGED=0
else
  rm -rf "$TARGET"
  mv "$PENDING/$DIR" "$TARGET"
  rmdir "$PENDING"
  [ -z "$PREVIOUS" ] || ln -sfn "$PREVIOUS" "$OV_SUPERAGENT_HOME/rollback"
  ln -sfn "$(physical "$TARGET")" "$OV_SUPERAGENT_HOME/current"
  ok "$(m '安装' 'Install ')  ${GRAY}OpenViking SuperAgent $VERSION → $(short "$TARGET")${RESET}"
  CHANGED=1
fi

# --lang is kept, as `ov-superagent --lang` keeps it.
if [ -n "$LANG_FLAG" ]; then printf '%s\n' "$LANG_FLAG" > "$OV_SUPERAGENT_HOME/language"; fi

mkdir -p "$OV_SUPERAGENT_HOME/bin"
cat > "$OV_SUPERAGENT_HOME/bin/ov-superagent" <<'LAUNCHER'
#!/bin/sh
# Always the installed release: `current` moves on upgrade, this file does not.
exec "$HOME/.ov-superagent/current/node/bin/node" "$HOME/.ov-superagent/current/packages/cli/bin/ov-superagent.mjs" "$@"
LAUNCHER
chmod 755 "$OV_SUPERAGENT_HOME/bin/ov-superagent"

case ":$PATH:" in
  *":$OV_SUPERAGENT_HOME/bin:"*) ;;
  *)
    case "$(basename "${SHELL:-sh}")" in
      zsh) RC="$HOME/.zshrc" ;;
      bash) if [ "$os" = darwin ]; then RC="$HOME/.bash_profile"; else RC="$HOME/.bashrc"; fi ;;
      *) RC="$HOME/.profile" ;;
    esac
    if ! grep -q '/.ov-superagent/bin' "$RC" 2>/dev/null; then
      if { printf '\n# ov-superagent\nexport PATH="$HOME/.ov-superagent/bin:$PATH"\n' >> "$RC"; } 2>/dev/null; then
        ok "$(m '命令' 'Command ')  ${GRAY}$(m "~/.ov-superagent/bin 已加进 $(short "$RC")，新开的终端里可以直接用 ov-superagent" "~/.ov-superagent/bin is added to $(short "$RC"); new terminals know ov-superagent")${RESET}"
      else
        warn "$(m "写不了 $(short "$RC")；OpenViking SuperAgent 已安装完成。新终端要直接用 ov-superagent，请手动加入：export PATH=\"\$HOME/.ov-superagent/bin:\$PATH\"" "Could not write $(short "$RC"); OpenViking SuperAgent is installed. To use ov-superagent directly in new terminals, add this yourself: export PATH=\"\$HOME/.ov-superagent/bin:\$PATH\"")"
      fi
    fi
    ;;
esac

rmdir "$LOCK" 2>/dev/null || true
LOCK=""
# Instances live in the config; without one this machine still needs setup,
# however often the installer has run.
if [ -f "$CONFIG_FILE" ]; then
  if [ "$CHANGED" = 1 ] && [ -n "$PREVIOUS" ]; then
    note "$(m "运行中的实例还在旧版本上：执行 ${BOLD}ov-superagent restart${RESET} 换到 ${VERSION}" "running instances are on the old release: ${BOLD}ov-superagent restart${RESET} moves them to ${VERSION}")"
  fi
elif [ "$SETUP_NEXT" = 1 ]; then
  cleanup
  trap - EXIT INT TERM
  if [ -n "$LANG_FLAG" ]; then exec "$OV_SUPERAGENT_HOME/bin/ov-superagent" setup --lang "$LANG_FLAG" </dev/tty; fi
  exec "$OV_SUPERAGENT_HOME/bin/ov-superagent" setup </dev/tty
else
  note "$(m '下一步：运行' 'next: run') $(short "$OV_SUPERAGENT_HOME/bin/ov-superagent") setup"
fi
