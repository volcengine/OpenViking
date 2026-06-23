#!/usr/bin/env bash

set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  echo "[openviking] ERROR: install.sh requires bash" >&2
  exit 1
fi

VERSION="2026.6.3"
RELEASE_PATH="${INSTALL_RELEASE_PATH:-latest}"
PLUGIN_ID="openviking"
PACKAGE_NAME="openviking"
DEFAULT_BUCKET="arkclaw-ov"
DEFAULT_REGION="cn-beijing"
DEFAULT_TOS_BASE_URL=""

INSTALL_TOS_BASE_URL="${INSTALL_TOS_BASE_URL:-$DEFAULT_TOS_BASE_URL}"
INSTALL_MANIFEST_URL="${INSTALL_MANIFEST_URL:-}"
INSTALL_BUCKET="${INSTALL_BUCKET:-$DEFAULT_BUCKET}"
INSTALL_REGION="${INSTALL_REGION:-}"
INSTALL_TARBALL="${INSTALL_TARBALL:-}"
INSTALL_SOURCE="${INSTALL_SOURCE:-tos}"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
SKILLS_DIR="${SKILLS_DIR:-$HOME/.agents/skills}"
VERIFY_ONLY=0
DRY_RUN=0
RESTART_GATEWAY=1
INTERNAL_DOMAIN=1
TMP_DIR=""

OPENVIKING_BASE_URL="${OPENVIKING_BASE_URL:-}"
OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-}"
OPENVIKING_PEER_ROLE="${OPENVIKING_PEER_ROLE:-}"
OPENVIKING_PEER_PREFIX="${OPENVIKING_PEER_PREFIX:-}"
OPENVIKING_ACCOUNT_ID="${OPENVIKING_ACCOUNT_ID:-}"
OPENVIKING_USER_ID="${OPENVIKING_USER_ID:-}"
OPENVIKING_RECALL_RESOURCES="${OPENVIKING_RECALL_RESOURCES:-0}"
OPENVIKING_RECALL_TARGET_TYPES="${OPENVIKING_RECALL_TARGET_TYPES:-}"

OPENCLAW_DIR="$OPENCLAW_STATE_DIR"
EXTENSION_DIR="$OPENCLAW_DIR/extensions/$PLUGIN_ID"
if [ "$PLUGIN_ID" = "openviking" ]; then
  EXTENSION_DIR="$OPENCLAW_DIR/extensions/openviking"
fi
CONFIG_FILE="$OPENCLAW_DIR/openclaw.json"

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT
trap 'echo "[openviking] ERROR: installation failed" >&2' ERR

usage() {
  cat <<'EOF'
Usage: bash install.sh [options]

This simplified installer downloads and installs openviking.tgz from a TOS
directory. By default it downloads:
  <tos-base-url>/latest/openviking.tgz

Options:
  --tos-base-url <url>       Override TOS base URL.
  --manifest-url <url>       Override manifest URL. The package is still taken
                             from the same release directory unless manifest
                             parsing is added by the caller.
  --bucket <bucket>          Bucket name for generated regional URL.
  --region <region>          Use https://<bucket>.tos-<region>.ivolces.com.
  --internal                 Use ivolces.com domain (default).
  --external                 Use volces.com public domain.
  --latest                   Install from latest/openviking.tgz (default).
  --date <date>              Install from <date>/openviking.tgz, e.g. 2026.6.3.
  --release-path <path>      Install from <path>/openviking.tgz.
  --tarball <path>           Install a local tgz instead of downloading.
  --source tos|tarball|local|existing
                             Install ./openviking.tgz next to this script, or
                             use existing for parse/verify-only smoke checks.
  --source existing          Reuse the currently installed plugin and only run configuration checks.
  --verify-only              Download/validate only; do not install.
  --dry-run                  Print the download/install actions only.
  --no-restart               Do not restart OpenClaw gateway after install.
  --openclaw-state-dir <dir> Override ~/.openclaw.
  --openviking-base-url <url>
  --openviking-api-key <key>
  --peer-role <role>
  --peer-prefix <prefix>
  --account-id <id>
  --user-id <id>
  --recall-resources <0|1>
  --recall-target-types <types>
  -h, --help
EOF
}

info() {
  echo "[openviking] $*"
}

die() {
  echo "[openviking] ERROR: $*" >&2
  exit 1
}

require_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    die "Required file is missing: $path"
  fi
}

require_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    die "Required directory is missing: $path"
  fi
}

script_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

normalize_base_url() {
  printf '%s' "$1" | sed 's#/*$##'
}

discover_region() {
  if [ -n "$INSTALL_REGION" ]; then
    printf '%s' "$INSTALL_REGION"
    return 0
  fi

  local region=""
  if command -v curl >/dev/null 2>&1; then
    region=$(curl --connect-timeout 2 --max-time 5 -fsS "http://100.96.0.96/latest/region_id" 2>/dev/null || true)
  fi

  if [ -n "$region" ]; then
    printf '%s' "$region"
  fi
}

resolve_tos_base_url() {
  if [ -n "$INSTALL_TOS_BASE_URL" ]; then
    normalize_base_url "$INSTALL_TOS_BASE_URL"
    return 0
  fi

  local region
  region=$(discover_region)
  if [ -n "$region" ]; then
    local bucket="$INSTALL_BUCKET"
    if [ "$bucket" = "$DEFAULT_BUCKET" ]; then
      bucket="$DEFAULT_BUCKET-$region"
    fi
    local domain_suffix="ivolces.com"
    if [ "$INTERNAL_DOMAIN" -eq 0 ]; then
      domain_suffix="volces.com"
    fi
    printf 'https://%s.tos-%s.%s' "$bucket" "$region" "$domain_suffix"
  else
    printf 'https://%s.tos-%s.volces.com' "$INSTALL_BUCKET" "$DEFAULT_REGION"
  fi
}

package_url() {
  local base_url
  base_url=$(resolve_tos_base_url)
  printf '%s/%s/openviking.tgz' "$base_url" "$RELEASE_PATH"
}

manifest_url() {
  if [ -n "$INSTALL_MANIFEST_URL" ]; then
    printf '%s' "$INSTALL_MANIFEST_URL"
    return 0
  fi

  local base_url
  base_url=$(resolve_tos_base_url)
  printf '%s/%s/manifest.json' "$base_url" "$RELEASE_PATH"
}

download_manifest() {
  local output="$1"
  local manifest_url
  manifest_url=$(manifest_url)
  info "Downloading OpenViking release manifest: $manifest_url"
  if ! download_url "$manifest_url" "$output"; then
    info "Manifest is unavailable; continuing with direct package download"
    return 1
  fi
  return 0
}

download_url() {
  local url="$1"
  local output="$2"

  if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY RUN: download $url -> $output"
    return 0
  fi

  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 10 --max-time 300 "$url" -o "$output"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$output" "$url"
  else
    die "curl or wget is required to download $url"
  fi
}

mask_secret() {
  local value="${1:-}"
  local length=${#value}
  if [ "$length" -le 8 ]; then
    echo "***"
  else
    printf '%s...%s' "${value:0:4}" "${value: -4}"
  fi
}

redact_arg() {
  local value="${1:-}"
  if [ -n "$OPENVIKING_API_KEY" ] && [ "$value" = "$OPENVIKING_API_KEY" ]; then
    mask_secret "$value"
  else
    printf '%s' "$value"
  fi
}

run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[openviking] DRY RUN:'
    for arg in "$@"; do
      printf ' %s' "$(redact_arg "$arg")"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

write_env_line() {
  local key="$1"
  local value="$2"
  if [ -n "$value" ]; then
    local escaped
    escaped=${value//\'/\'\\\'\'}
    printf "%s='%s'\n" "$key" "$escaped" >> "$ENV_FILE"
  fi
}

ensure_config() {
  mkdir -p "$OPENCLAW_DIR" "$OPENCLAW_DIR/backup"

  if [ -f "$CONFIG_FILE" ]; then
    local backup_id
    if command -v md5 >/dev/null 2>&1; then
      backup_id=$(md5 -q "$CONFIG_FILE")
    else
      backup_id=$(md5sum "$CONFIG_FILE" | awk '{print $1}')
    fi
    cp "$CONFIG_FILE" "$OPENCLAW_DIR/backup/openclaw.json.bak.$backup_id"
    return 0
  fi

  cat > "$CONFIG_FILE" <<'JSON'
{
  "plugins": {
    "allow": [],
    "entries": {},
    "slots": {}
  }
}
JSON
}

update_openclaw_config() {
  if ! command -v jq >/dev/null 2>&1; then
    die "jq is required to update $CONFIG_FILE. Please install jq and retry."
  fi

  local context_engine
  context_engine=$(jq -r '.plugins.slots.contextEngine // ""' "$CONFIG_FILE")

  local jq_filter
  jq_filter='.plugins = (.plugins // {})
| .plugins.allow = (.plugins.allow // [])
| .plugins.allow |= (if index("openviking") then . else . + ["openviking"] end)
| .plugins.entries = (.plugins.entries // {})
| .plugins.entries.openviking = (.plugins.entries.openviking // {})
| .plugins.entries.openviking.enabled = true
| .plugins.entries.openviking.config = (.plugins.entries.openviking.config // {})'

  if [ -z "$context_engine" ] || [ "$context_engine" = "null" ]; then
    jq_filter="$jq_filter | .plugins.slots = (.plugins.slots // {}) | .plugins.slots.contextEngine = \"openviking\""
  fi

  local tmp_config="${CONFIG_FILE}.tmp"
  jq "$jq_filter" "$CONFIG_FILE" > "$tmp_config"
  mv "$tmp_config" "$CONFIG_FILE"

  if [ -n "$context_engine" ] && [ "$context_engine" != "null" ] && [ "$context_engine" != "$PLUGIN_ID" ]; then
    echo "Existing context engine '$context_engine' was preserved."
    echo "To switch to OpenViking, run: openclaw config set plugins.slots.contextEngine openviking"
  fi
}

install_package_from_tgz() {
  local tgz_path="$1"

  if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY RUN: install $tgz_path -> $EXTENSION_DIR"
    return 0
  fi

  require_file "$tgz_path"

  if [ "$VERIFY_ONLY" -eq 1 ]; then
    tar -tzf "$tgz_path" >/dev/null
    info "Verification complete: $tgz_path"
    return 0
  fi

  TMP_DIR=$(mktemp -d)
  tar -xzf "$tgz_path" -C "$TMP_DIR"

  local PACKAGE_DIR="$TMP_DIR/$PACKAGE_NAME"
  require_dir "$PACKAGE_DIR"
  require_file "$PACKAGE_DIR/package.json"
  require_file "$PACKAGE_DIR/openclaw.plugin.json"
  require_file "$PACKAGE_DIR/dist/index.js"
  # Runtime dependency required by dist/index.js when OpenClaw loads this standalone package.
  local typebox_dir="$PACKAGE_DIR/node_modules/@sinclair/typebox"
  require_dir "$typebox_dir"
  require_dir "$PACKAGE_DIR/node_modules/@sinclair/typebox"
  require_dir "$PACKAGE_DIR/skills"

  ensure_config

  info "Deploying plugin files to $EXTENSION_DIR"
  rm -rf "$EXTENSION_DIR"
  mkdir -p "$EXTENSION_DIR"
  cp -R "$PACKAGE_DIR/." "$EXTENSION_DIR/"

  info "Deploying skills to $SKILLS_DIR"
  mkdir -p "$SKILLS_DIR"
  for skill_dir in "$PACKAGE_DIR"/skills/*/; do
    if [ -d "$skill_dir" ]; then
      local skill_name
      skill_name=$(basename "$skill_dir")
      rm -rf "$SKILLS_DIR/$skill_name"
      cp -R "$skill_dir" "$SKILLS_DIR/$skill_name"
    fi
  done

  update_openclaw_config
}

download_and_install() {
  TMP_DIR=$(mktemp -d)
  local tgz_path="$TMP_DIR/openviking.tgz"
  local manifest_path="$TMP_DIR/manifest.json"
  local url
  url=$(package_url)

  download_manifest "$manifest_path" || true
  info "Downloading OpenViking package: $url"
  download_url "$url" "$tgz_path"
  install_package_from_tgz "$tgz_path"
}

configure_openviking_service() {
  if [ -z "$OPENVIKING_BASE_URL" ] && [ -z "$OPENVIKING_API_KEY" ]; then
    return 0
  fi
  [ -n "$OPENVIKING_BASE_URL" ] || die "OPENVIKING_BASE_URL is required when OPENVIKING_API_KEY is set"
  [ -n "$OPENVIKING_API_KEY" ] || die "OPENVIKING_API_KEY is required when OPENVIKING_BASE_URL is set"

  ENV_FILE="$OPENCLAW_DIR/openviking.env"
  if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$OPENCLAW_DIR"
    : > "$ENV_FILE"
    write_env_line "OPENVIKING_BASE_URL" "$OPENVIKING_BASE_URL"
    write_env_line "OPENVIKING_API_KEY" "$OPENVIKING_API_KEY"
    write_env_line "OPENVIKING_PEER_ROLE" "$OPENVIKING_PEER_ROLE"
    write_env_line "OPENVIKING_PEER_PREFIX" "$OPENVIKING_PEER_PREFIX"
    write_env_line "OPENVIKING_ACCOUNT_ID" "$OPENVIKING_ACCOUNT_ID"
    write_env_line "OPENVIKING_USER_ID" "$OPENVIKING_USER_ID"
    write_env_line "OPENVIKING_RECALL_RESOURCES" "$OPENVIKING_RECALL_RESOURCES"
    write_env_line "OPENVIKING_RECALL_TARGET_TYPES" "$OPENVIKING_RECALL_TARGET_TYPES"
    chmod 600 "$ENV_FILE"
  else
    info "DRY RUN: write service env -> $ENV_FILE"
  fi

  info "OpenViking Service URL: $OPENVIKING_BASE_URL"
  info "OpenViking API Key: $(mask_secret "$OPENVIKING_API_KEY")"

  set -- openclaw openviking setup --base-url "$OPENVIKING_BASE_URL" --api-key "$OPENVIKING_API_KEY" --force-slot
  [ -z "$OPENVIKING_PEER_ROLE" ] || set -- "$@" --peer-role "$OPENVIKING_PEER_ROLE"
  [ -z "$OPENVIKING_PEER_PREFIX" ] || set -- "$@" --peer-prefix "$OPENVIKING_PEER_PREFIX"
  [ -z "$OPENVIKING_ACCOUNT_ID" ] || set -- "$@" --account-id "$OPENVIKING_ACCOUNT_ID"
  [ -z "$OPENVIKING_USER_ID" ] || set -- "$@" --user-id "$OPENVIKING_USER_ID"
  if [ "$OPENVIKING_RECALL_RESOURCES" = "1" ] && [ -z "$OPENVIKING_RECALL_TARGET_TYPES" ]; then
    set -- "$@" --recall-target-types resource
    run_cmd openclaw config set plugins.entries.openviking.config.recallTargetTypes '["resource"]'
  fi
  [ -z "$OPENVIKING_RECALL_TARGET_TYPES" ] || set -- "$@" --recall-target-types "$OPENVIKING_RECALL_TARGET_TYPES"
  run_cmd "$@"
}

restart_gateway() {
  if [ "$VERIFY_ONLY" -eq 1 ] || [ "$RESTART_GATEWAY" -eq 0 ]; then
    return 0
  fi

  if command -v openclaw >/dev/null 2>&1; then
    info "Restarting OpenClaw gateway"
    if ! run_cmd openclaw gateway restart; then
      echo "Gateway restart failed. Please run manually: openclaw gateway restart" >&2
    fi
    run_cmd openclaw openviking status --json || true
    run_cmd openclaw config get plugins.slots.contextEngine || true
  else
    echo "openclaw CLI was not found. Please install OpenClaw and run: openclaw gateway restart" >&2
  fi
}

install_source="$INSTALL_SOURCE"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --tos-base-url)
      INSTALL_TOS_BASE_URL="${2:-}"
      shift 2
      ;;
    --manifest-url)
      INSTALL_MANIFEST_URL="${2:-}"
      shift 2
      ;;
    --bucket)
      INSTALL_BUCKET="${2:-}"
      shift 2
      ;;
    --region)
      INSTALL_REGION="${2:-}"
      shift 2
      ;;
    --internal)
      INTERNAL_DOMAIN=1
      shift
      ;;
    --external)
      INTERNAL_DOMAIN=0
      shift
      ;;
    --latest)
      RELEASE_PATH="latest"
      shift
      ;;
    --date|--version)
      RELEASE_PATH="${2:-}"
      shift 2
      ;;
    --release-path)
      RELEASE_PATH="${2:-}"
      shift 2
      ;;
    --tarball)
      INSTALL_TARBALL="${2:-}"
      install_source="local"
      shift 2
      ;;
    --source)
      case "${2:-}" in
        local|tarball) install_source="local" ;;
        existing) install_source="existing" ;;
        tos|remote) install_source="remote" ;;
        *) die "Invalid --source: ${2:-}. Expected tos, remote, local, tarball, or existing." ;;
      esac
      shift 2
      ;;
    --verify-only)
      VERIFY_ONLY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-restart)
      RESTART_GATEWAY=0
      shift
      ;;
    --openclaw-state-dir)
      OPENCLAW_STATE_DIR="${2:-}"
      OPENCLAW_DIR="$OPENCLAW_STATE_DIR"
      EXTENSION_DIR="$OPENCLAW_DIR/extensions/$PLUGIN_ID"
      CONFIG_FILE="$OPENCLAW_DIR/openclaw.json"
      shift 2
      ;;
    --base-url|--openviking-base-url)
      OPENVIKING_BASE_URL="${2:-}"
      shift 2
      ;;
    --api-key|--openviking-api-key)
      OPENVIKING_API_KEY="${2:-}"
      shift 2
      ;;
    --peer-role)
      OPENVIKING_PEER_ROLE="${2:-}"
      shift 2
      ;;
    --peer-prefix)
      OPENVIKING_PEER_PREFIX="${2:-}"
      shift 2
      ;;
    --account-id)
      OPENVIKING_ACCOUNT_ID="${2:-}"
      shift 2
      ;;
    --user-id)
      OPENVIKING_USER_ID="${2:-}"
      shift 2
      ;;
    --recall-resources)
      OPENVIKING_RECALL_RESOURCES="${2:-}"
      shift 2
      ;;
    --recall-target-types)
      OPENVIKING_RECALL_TARGET_TYPES="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      die "Unknown option: $1"
      ;;
    *)
      INSTALL_TARBALL="$1"
      install_source="local"
      shift
      ;;
  esac
done

echo
info "Installing OpenViking plugin release $VERSION from $RELEASE_PATH"

if [ "$install_source" = "existing" ]; then
  info "Using existing OpenViking installation; no package download or file changes will be performed"
elif [ "$install_source" = "local" ]; then
  if [ -z "$INSTALL_TARBALL" ]; then
    INSTALL_TARBALL="$(script_dir)/openviking.tgz"
  fi
  install_package_from_tgz "$INSTALL_TARBALL"
else
  download_and_install
fi

if [ "$VERIFY_ONLY" -eq 0 ]; then
  configure_openviking_service
  restart_gateway
fi

echo
info "OpenViking plugin install complete"
