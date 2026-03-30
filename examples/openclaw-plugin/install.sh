#!/bin/bash
#
# OpenClaw + OpenViking one-click installer
# Usage: curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash
#
# Options:
#   --repo <owner/repo>           - GitHub repository (default: volcengine/OpenViking)
#   --plugin-version <tag>        - Plugin version (Git tag, e.g. v0.2.9, default: latest tag)
#   --openviking-version <ver>    - OpenViking PyPI version (e.g. 0.2.9, default: latest)
#   --workdir <path>              - OpenClaw config directory (default: ~/.openclaw)
#   --update / --upgrade-plugin   - Upgrade only the plugin using native script logic
#   --rollback                    - Roll back the last plugin upgrade
#   -y, --yes                     - Non-interactive mode
#   --zh                          - Chinese prompts
#   -h, --help                    - Show help
#
# Environment variables:
#   REPO=owner/repo               - GitHub repository (same as --repo)
#   BRANCH=branch                 - Git branch/tag/commit (legacy, use --plugin-version instead)
#   PLUGIN_VERSION=tag            - Plugin version (same as --plugin-version)
#   OPENVIKING_VERSION=ver        - OpenViking PyPI version (same as --openviking-version)
#   OPENVIKING_INSTALL_YES=1      - non-interactive mode (same as -y)
#   SKIP_OPENCLAW=1               - skip OpenClaw check
#   SKIP_OPENVIKING=1             - skip OpenViking installation
#   NPM_REGISTRY=url              - npm registry (default: https://registry.npmmirror.com)
#   PIP_INDEX_URL=url             - pip index URL (default: https://pypi.tuna.tsinghua.edu.cn/simple)
#   OPENVIKING_VLM_API_KEY        - VLM model API key (optional)
#   OPENVIKING_EMBEDDING_API_KEY  - Embedding model API key (optional)
#   OPENVIKING_ARK_API_KEY        - legacy fallback for both keys
#   OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1 - if venv unavailable (PEP 668 only), allow pip --break-system-packages
#   GET_PIP_URL=url               - URL for get-pip.py when using venv --without-pip (default: auto)
#
# On Debian/Ubuntu (PEP 668), the script installs OpenViking into a venv at
# ~/.openviking/venv to avoid "externally-managed-environment" errors.
#
# If curl | bash from a branch URL looks stale, pin the commit SHA in the path:
#   .../LinQiang391/OpenViking/<commit>/examples/openclaw-plugin/install.sh
# (raw.githubusercontent.com may cache branch resolution briefly.)
#

set -e
set -o pipefail

# Set by install_openviking when using venv (e.g. on Debian/Ubuntu); used by write_openviking_env
OPENVIKING_PYTHON_PATH=""

REPO="${REPO:-volcengine/OpenViking}"
# BRANCH is legacy, prefer PLUGIN_VERSION. If omitted, resolve the latest tag from GitHub.
PLUGIN_VERSION_EXPLICIT="0"
if [[ -n "${PLUGIN_VERSION:-}" || -n "${BRANCH:-}" ]]; then
  PLUGIN_VERSION_EXPLICIT="1"
fi
PLUGIN_VERSION="${PLUGIN_VERSION:-${BRANCH:-}}"
OPENVIKING_VERSION="${OPENVIKING_VERSION:-}"
INSTALL_YES="${OPENVIKING_INSTALL_YES:-0}"
UPGRADE_PLUGIN_ONLY="${OPENVIKING_UPGRADE_PLUGIN_ONLY:-0}"
ROLLBACK_LAST_UPGRADE="${OPENVIKING_ROLLBACK_LAST_UPGRADE:-0}"
SKIP_OC="${SKIP_OPENCLAW:-0}"
SKIP_OV="${SKIP_OPENVIKING:-0}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.volces.com/pypi/simple/}"
HOME_DIR="${HOME:-$USERPROFILE}"
DEFAULT_OPENCLAW_DIR="${HOME_DIR}/.openclaw"
OPENCLAW_DIR="${DEFAULT_OPENCLAW_DIR}"
OPENVIKING_DIR="${HOME_DIR}/.openviking"
PLUGIN_DEST=""  # Will be set after resolving plugin config
DEFAULT_SERVER_PORT=1933
DEFAULT_AGFS_PORT=1833
DEFAULT_VLM_MODEL="doubao-seed-2-0-pro-260215"
DEFAULT_EMBED_MODEL="doubao-embedding-vision-251215"
SELECTED_SERVER_PORT="${DEFAULT_SERVER_PORT}"
SELECTED_CONFIG_PATH=""
SELECTED_MODE="local"
LANG_UI="en"

# Plugin config (set by resolve_plugin_config)
RESOLVED_PLUGIN_DIR=""
RESOLVED_PLUGIN_ID=""
RESOLVED_PLUGIN_KIND=""
RESOLVED_PLUGIN_SLOT=""
RESOLVED_FILES_REQUIRED=()
RESOLVED_FILES_OPTIONAL=()
RESOLVED_NPM_OMIT_DEV="true"
RESOLVED_MIN_OPENCLAW_VERSION=""
RESOLVED_MIN_OPENVIKING_VERSION=""
RESOLVED_PLUGIN_RELEASE_ID=""

UPGRADE_DETECTED_GENERATION="none"
UPGRADE_DETECTED_FROM_VERSION=""
UPGRADE_RUNTIME_MODE="local"
UPGRADE_RUNTIME_CONFIG_PATH=""
UPGRADE_RUNTIME_PORT=""
UPGRADE_RUNTIME_BASE_URL=""
UPGRADE_RUNTIME_API_KEY=""
UPGRADE_RUNTIME_AGENT_ID=""
UPGRADE_CLAIM_SLOT="1"
UPGRADE_DETECTED_IDS=()
UPGRADE_AUDIT_OPERATION=""
UPGRADE_AUDIT_CREATED_AT=""
UPGRADE_AUDIT_FROM_VERSION=""
UPGRADE_AUDIT_TO_VERSION=""
UPGRADE_AUDIT_CONFIG_BACKUP_PATH=""
UPGRADE_AUDIT_RUNTIME_MODE=""
UPGRADE_AUDIT_COMPLETED_AT=""
UPGRADE_AUDIT_ROLLED_BACK_AT=""
UPGRADE_AUDIT_ROLLBACK_CONFIG_PATH=""
UPGRADE_AUDIT_PLUGIN_BACKUPS=()

# Parse args (supports curl | bash -s -- ...)
_expect_workdir=""
_expect_plugin_version=""
_expect_ov_version=""
_expect_repo=""
for arg in "$@"; do
  if [[ -n "$_expect_workdir" ]]; then
    OPENCLAW_DIR="$arg"
    _expect_workdir=""
    continue
  fi
  if [[ -n "$_expect_plugin_version" ]]; then
    PLUGIN_VERSION="$arg"
    PLUGIN_VERSION_EXPLICIT="1"
    _expect_plugin_version=""
    continue
  fi
  if [[ -n "$_expect_ov_version" ]]; then
    OPENVIKING_VERSION="$arg"
    _expect_ov_version=""
    continue
  fi
  if [[ -n "$_expect_repo" ]]; then
    REPO="$arg"
    _expect_repo=""
    continue
  fi
  [[ "$arg" == "-y" || "$arg" == "--yes" ]] && INSTALL_YES="1"
  [[ "$arg" == "--upgrade-plugin" || "$arg" == "--update" || "$arg" == "--upgrade" ]] && UPGRADE_PLUGIN_ONLY="1"
  [[ "$arg" == "--rollback" || "$arg" == "--rollback-last-upgrade" ]] && ROLLBACK_LAST_UPGRADE="1"
  [[ "$arg" == "--zh" ]] && LANG_UI="zh"
  [[ "$arg" == "--workdir" ]] && { _expect_workdir="1"; continue; }
  [[ "$arg" == "--plugin-version" ]] && { _expect_plugin_version="1"; continue; }
  [[ "$arg" == "--openviking-version" ]] && { _expect_ov_version="1"; continue; }
  [[ "$arg" == "--repo" ]] && { _expect_repo="1"; continue; }
  [[ "$arg" == "-h" || "$arg" == "--help" ]] && {
    echo "Usage: curl -fsSL <INSTALL_URL> | bash [-s -- OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --repo <owner/repo>      GitHub repository (default: volcengine/OpenViking)"
    echo "  --plugin-version <tag>   Plugin version (Git tag, e.g. v0.2.9, default: latest tag)"
    echo "  --openviking-version <v> OpenViking PyPI version (e.g. 0.2.9, default: latest)"
    echo "  --workdir <path>         OpenClaw config directory (default: ~/.openclaw)"
    echo "  --update, --upgrade-plugin"
    echo "                           Upgrade only the plugin to the requested --plugin-version; do not change the OpenViking service"
    echo "  --rollback              Roll back the last plugin upgrade"
    echo "  -y, --yes                Non-interactive mode"
    echo "  --zh                     Chinese prompts"
    echo "  -h, --help               Show this help"
    echo ""
    echo "Examples:"
    echo "  # Install latest version"
    echo "  curl -fsSL <URL> | bash"
    echo ""
    echo "  # Install from a fork repository"
    echo "  curl -fsSL <URL> | bash -s -- --repo yourname/OpenViking --plugin-version dev-branch"
    echo ""
    echo "  # Install specific plugin version"
    echo "  curl -fsSL <URL> | bash -s -- --plugin-version v0.2.8"
    echo ""
    echo "  # Upgrade only the plugin files from main branch"
    echo "  curl -fsSL <URL> | bash -s -- --update --plugin-version main"
    echo ""
    echo "  # Roll back the last plugin upgrade"
    echo "  curl -fsSL <URL> | bash -s -- --rollback"
    echo ""
    echo "Env vars: REPO, PLUGIN_VERSION, OPENVIKING_VERSION, SKIP_OPENCLAW, SKIP_OPENVIKING, NPM_REGISTRY, PIP_INDEX_URL"
    exit 0
  }
done

if [[ "$UPGRADE_PLUGIN_ONLY" == "1" && "$ROLLBACK_LAST_UPGRADE" == "1" ]]; then
  echo "[ERROR] --update/--upgrade-plugin and --rollback cannot be used together"
  exit 1
fi

tr() {
  local en="$1"
  local zh="$2"
  if [[ "$LANG_UI" == "zh" ]]; then
    echo "$zh"
  else
    echo "$en"
  fi
}

legacy_plugin_install_hint() {
  local args=(--plugin-version "<legacy-version>")
  [[ "$OPENCLAW_DIR" != "$DEFAULT_OPENCLAW_DIR" ]] && args+=(--workdir "$OPENCLAW_DIR")
  [[ "$REPO" != "volcengine/OpenViking" ]] && args+=(--repo "$REPO")
  [[ "$LANG_UI" == "zh" ]] && args+=(--zh)

  if [[ -n "${OPENVIKING_INSTALL_LEGACY_HINT:-}" ]]; then
    printf "%s\n" "${OPENVIKING_INSTALL_LEGACY_HINT}"
    return 0
  fi

  if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    local script_path=""
    script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    printf "bash %q" "${script_path}"
    local arg
    for arg in "${args[@]}"; do
      printf " %q" "${arg}"
    done
    printf "\n"
    return 0
  fi

  printf "ov-install"
  local arg
  for arg in "${args[@]}"; do
    printf " %q" "${arg}"
  done
  printf "\n"
}

shell_single_quote() {
  printf "%s" "$1" | sed "s/'/'\"'\"'/g"
}

iso_now() {
  node -e "process.stdout.write(new Date().toISOString())"
}

decode_base64_with_node() {
  local value="${1:-}"
  node -e "process.stdout.write(Buffer.from(process.argv[1] || '', 'base64').toString('utf8'))" "${value}"
}

run_openclaw_cmd() {
  if [[ "$OPENCLAW_DIR" != "$DEFAULT_OPENCLAW_DIR" ]]; then
    OPENCLAW_STATE_DIR="$OPENCLAW_DIR" "$@"
  else
    "$@"
  fi
}

get_openclaw_config_path() {
  printf "%s\n" "${OPENCLAW_DIR}/openclaw.json"
}

get_install_state_path_for_plugin() {
  printf "%s\n" "${OPENCLAW_DIR}/extensions/$1/.ov-install-state.json"
}

get_upgrade_audit_dir() {
  printf "%s\n" "${OPENCLAW_DIR}/.openviking-upgrade-backup"
}

get_upgrade_audit_path() {
  printf "%s\n" "$(get_upgrade_audit_dir)/last-upgrade.json"
}

get_openclaw_config_backup_path() {
  printf "%s\n" "$(get_upgrade_audit_dir)/openclaw.json.bak"
}

variant_meta_by_id() {
  case "$1" in
    memory-openviking) printf "%s\n" "memory-openviking|openclaw-memory-plugin|memory|legacy|none" ;;
    openviking) printf "%s\n" "openviking|openclaw-plugin|contextEngine|current|legacy" ;;
    *) return 1 ;;
  esac
}

format_target_version_label() {
  local base="${RESOLVED_PLUGIN_ID:-openviking}@${PLUGIN_VERSION}"
  if [[ -n "${RESOLVED_PLUGIN_RELEASE_ID}" && "${RESOLVED_PLUGIN_RELEASE_ID}" != "${PLUGIN_VERSION}" ]]; then
    printf "%s\n" "${base} (${RESOLVED_PLUGIN_RELEASE_ID})"
    return 0
  fi
  printf "%s\n" "${base}"
}

validate_requested_plugin_version() {
  if [[ "${PLUGIN_VERSION}" == "v0.2.7" ]]; then
    err "Plugin version v0.2.7 does not exist. Please choose another release or branch."
    exit 1
  fi
}

ensure_plugin_only_operation_args() {
  if [[ ("${UPGRADE_PLUGIN_ONLY}" == "1" || "${ROLLBACK_LAST_UPGRADE}" == "1") && -n "${OPENVIKING_VERSION}" ]]; then
    err "--update/--upgrade-plugin and --rollback only operate on the plugin. Do not use --openviking-version with these modes."
    exit 1
  fi
}

backup_openclaw_config() {
  local config_path="$1"
  local backup_dir
  local backup_path
  backup_dir="$(get_upgrade_audit_dir)"
  backup_path="$(get_openclaw_config_backup_path)"
  mkdir -p "${backup_dir}"
  cp "${config_path}" "${backup_path}"
  printf "%s\n" "${backup_path}"
}

write_upgrade_audit_file() {
  local audit_path
  audit_path="$(get_upgrade_audit_path)"
  mkdir -p "$(get_upgrade_audit_dir)"
  node - "${audit_path}" \
    "${UPGRADE_AUDIT_OPERATION}" \
    "${UPGRADE_AUDIT_CREATED_AT}" \
    "${UPGRADE_AUDIT_FROM_VERSION}" \
    "${UPGRADE_AUDIT_TO_VERSION}" \
    "${UPGRADE_AUDIT_CONFIG_BACKUP_PATH}" \
    "${UPGRADE_AUDIT_RUNTIME_MODE}" \
    "${UPGRADE_AUDIT_COMPLETED_AT}" \
    "${UPGRADE_AUDIT_ROLLED_BACK_AT}" \
    "${UPGRADE_AUDIT_ROLLBACK_CONFIG_PATH}" \
    "${UPGRADE_AUDIT_PLUGIN_BACKUPS[@]}" <<'NODE'
const fs = require("fs");
const path = require("path");

const [
  auditPath,
  operation,
  createdAt,
  fromVersion,
  toVersion,
  configBackupPath,
  runtimeMode,
  completedAt,
  rolledBackAt,
  rollbackConfigPath,
  ...backupArgs
] = process.argv.slice(2);

const pluginBackups = backupArgs
  .filter(Boolean)
  .map((item) => {
    const [pluginId, ...rest] = item.split("|");
    return { pluginId, backupDir: rest.join("|") };
  })
  .filter((item) => item.pluginId && item.backupDir);

const data = {
  operation,
  createdAt,
  fromVersion,
  toVersion,
  configBackupPath,
  pluginBackups,
  runtimeMode,
};

if (completedAt) data.completedAt = completedAt;
if (rolledBackAt) data.rolledBackAt = rolledBackAt;
if (rollbackConfigPath) data.rollbackConfigPath = rollbackConfigPath;

fs.mkdirSync(path.dirname(auditPath), { recursive: true });
fs.writeFileSync(auditPath, `${JSON.stringify(data, null, 2)}\n`);
NODE
}

load_upgrade_audit_file() {
  local audit_path
  audit_path="$(get_upgrade_audit_path)"
  [[ -f "${audit_path}" ]] || return 1

  UPGRADE_AUDIT_OPERATION=""
  UPGRADE_AUDIT_CREATED_AT=""
  UPGRADE_AUDIT_FROM_VERSION=""
  UPGRADE_AUDIT_TO_VERSION=""
  UPGRADE_AUDIT_CONFIG_BACKUP_PATH=""
  UPGRADE_AUDIT_RUNTIME_MODE=""
  UPGRADE_AUDIT_COMPLETED_AT=""
  UPGRADE_AUDIT_ROLLED_BACK_AT=""
  UPGRADE_AUDIT_ROLLBACK_CONFIG_PATH=""
  UPGRADE_AUDIT_PLUGIN_BACKUPS=()

  local line key value
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "${key}" in
      operation) UPGRADE_AUDIT_OPERATION="$(decode_base64_with_node "${value}")" ;;
      createdAt) UPGRADE_AUDIT_CREATED_AT="$(decode_base64_with_node "${value}")" ;;
      fromVersion) UPGRADE_AUDIT_FROM_VERSION="$(decode_base64_with_node "${value}")" ;;
      toVersion) UPGRADE_AUDIT_TO_VERSION="$(decode_base64_with_node "${value}")" ;;
      configBackupPath) UPGRADE_AUDIT_CONFIG_BACKUP_PATH="$(decode_base64_with_node "${value}")" ;;
      runtimeMode) UPGRADE_AUDIT_RUNTIME_MODE="$(decode_base64_with_node "${value}")" ;;
      completedAt) UPGRADE_AUDIT_COMPLETED_AT="$(decode_base64_with_node "${value}")" ;;
      rolledBackAt) UPGRADE_AUDIT_ROLLED_BACK_AT="$(decode_base64_with_node "${value}")" ;;
      rollbackConfigPath) UPGRADE_AUDIT_ROLLBACK_CONFIG_PATH="$(decode_base64_with_node "${value}")" ;;
      pluginBackup) UPGRADE_AUDIT_PLUGIN_BACKUPS+=("$(decode_base64_with_node "${value}")") ;;
    esac
  done < <(node - "${audit_path}" <<'NODE'
const fs = require("fs");

const auditPath = process.argv[2];
const data = JSON.parse(fs.readFileSync(auditPath, "utf8"));
const enc = (value) => Buffer.from(String(value ?? ""), "utf8").toString("base64");

console.log(`operation=${enc(data.operation || "")}`);
console.log(`createdAt=${enc(data.createdAt || "")}`);
console.log(`fromVersion=${enc(data.fromVersion || "")}`);
console.log(`toVersion=${enc(data.toVersion || "")}`);
console.log(`configBackupPath=${enc(data.configBackupPath || "")}`);
console.log(`runtimeMode=${enc(data.runtimeMode || "")}`);
console.log(`completedAt=${enc(data.completedAt || "")}`);
console.log(`rolledBackAt=${enc(data.rolledBackAt || "")}`);
console.log(`rollbackConfigPath=${enc(data.rollbackConfigPath || "")}`);
for (const backup of Array.isArray(data.pluginBackups) ? data.pluginBackups : []) {
  const item = `${backup.pluginId || ""}|${backup.backupDir || ""}`;
  console.log(`pluginBackup=${enc(item)}`);
}
NODE
)
}

write_install_state_file() {
  local operation="$1"
  local from_version="$2"
  local install_state_path
  install_state_path="$(get_install_state_path_for_plugin "${RESOLVED_PLUGIN_ID:-openviking}")"
  mkdir -p "$(dirname "${install_state_path}")"
  node - "${install_state_path}" \
    "${RESOLVED_PLUGIN_ID:-openviking}" \
    "${PLUGIN_VERSION}" \
    "${RESOLVED_PLUGIN_RELEASE_ID}" \
    "${REPO}" \
    "${operation}" \
    "${from_version}" \
    "${UPGRADE_AUDIT_CONFIG_BACKUP_PATH}" \
    "${UPGRADE_AUDIT_PLUGIN_BACKUPS[@]}" <<'NODE'
const fs = require("fs");
const path = require("path");

const [installStatePath, pluginId, requestedRef, releaseId, repo, operation, fromVersion, configBackupPath, ...backupArgs] = process.argv.slice(2);
const generation = pluginId === "memory-openviking" ? "legacy" : pluginId === "openviking" ? "current" : "unknown";
const pluginBackups = backupArgs
  .filter(Boolean)
  .map((item) => {
    const [backupPluginId, ...rest] = item.split("|");
    return { pluginId: backupPluginId, backupDir: rest.join("|") };
  })
  .filter((item) => item.pluginId && item.backupDir);

const state = {
  pluginId,
  generation,
  requestedRef,
  releaseId,
  operation,
  fromVersion,
  configBackupPath,
  pluginBackups,
  installedAt: new Date().toISOString(),
  repo,
};

fs.mkdirSync(path.dirname(installStatePath), { recursive: true });
fs.writeFileSync(installStatePath, `${JSON.stringify(state, null, 2)}\n`);
NODE
}

stop_openclaw_gateway_for_upgrade() {
  if run_openclaw_cmd openclaw gateway stop >/dev/null 2>&1; then
    info "Stopped OpenClaw gateway before plugin upgrade"
  else
    warn "OpenClaw gateway may not be running; continuing"
  fi
}

prune_previous_upgrade_backups() {
  local plugin_id="$1"
  local keep_dir="$2"
  local disabled_dir="${OPENCLAW_DIR}/disabled-extensions"
  local keep_name=""
  [[ -n "${keep_dir}" ]] && keep_name="$(basename "${keep_dir}")"
  [[ -d "${disabled_dir}" ]] || return 0

  local entry
  shopt -s nullglob
  for entry in "${disabled_dir}/${plugin_id}-upgrade-backup-"*; do
    [[ -d "${entry}" ]] || continue
    [[ -n "${keep_name}" && "$(basename "${entry}")" == "${keep_name}" ]] && continue
    rm -rf "${entry}"
  done
  shopt -u nullglob
}

backup_plugin_directory() {
  local plugin_id="$1"
  local plugin_dir="${OPENCLAW_DIR}/extensions/${plugin_id}"
  [[ -d "${plugin_dir}" ]] || return 0

  local disabled_dir="${OPENCLAW_DIR}/disabled-extensions"
  local timestamp
  local backup_dir
  timestamp="$(node -e "process.stdout.write(String(Date.now()))")"
  backup_dir="${disabled_dir}/${plugin_id}-upgrade-backup-${timestamp}"
  mkdir -p "${disabled_dir}"
  if ! mv "${plugin_dir}" "${backup_dir}" 2>/dev/null; then
    cp -R "${plugin_dir}" "${backup_dir}"
    rm -rf "${plugin_dir}"
  fi
  info "$(tr "Backed up plugin directory: ${backup_dir}" "已备份插件目录: ${backup_dir}")" >&2
  prune_previous_upgrade_backups "${plugin_id}" "${backup_dir}"
  printf "%s\n" "${backup_dir}"
}

detect_installed_plugin_state() {
  local config_path
  config_path="$(get_openclaw_config_path)"

  UPGRADE_DETECTED_GENERATION="none"
  UPGRADE_DETECTED_FROM_VERSION=""
  UPGRADE_RUNTIME_MODE="local"
  UPGRADE_RUNTIME_CONFIG_PATH=""
  UPGRADE_RUNTIME_PORT=""
  UPGRADE_RUNTIME_BASE_URL=""
  UPGRADE_RUNTIME_API_KEY=""
  UPGRADE_RUNTIME_AGENT_ID=""
  UPGRADE_CLAIM_SLOT="1"
  UPGRADE_DETECTED_IDS=()

  local line key value
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "${key}" in
      generation) UPGRADE_DETECTED_GENERATION="${value}" ;;
      fromVersion) UPGRADE_DETECTED_FROM_VERSION="$(decode_base64_with_node "${value}")" ;;
      claimSlot) UPGRADE_CLAIM_SLOT="${value}" ;;
      mode) UPGRADE_RUNTIME_MODE="${value}" ;;
      configPath) UPGRADE_RUNTIME_CONFIG_PATH="$(decode_base64_with_node "${value}")" ;;
      port) UPGRADE_RUNTIME_PORT="${value}" ;;
      baseUrl) UPGRADE_RUNTIME_BASE_URL="$(decode_base64_with_node "${value}")" ;;
      apiKey) UPGRADE_RUNTIME_API_KEY="$(decode_base64_with_node "${value}")" ;;
      agentId) UPGRADE_RUNTIME_AGENT_ID="$(decode_base64_with_node "${value}")" ;;
      detectionId) UPGRADE_DETECTED_IDS+=("${value}") ;;
    esac
  done < <(node - "${config_path}" "${OPENCLAW_DIR}" "${RESOLVED_PLUGIN_ID}" "${RESOLVED_PLUGIN_SLOT}" "${OPENVIKING_DIR}" "${DEFAULT_SERVER_PORT}" <<'NODE'
const fs = require("fs");
const path = require("path");

const [configPath, openclawDir, resolvedPluginId, resolvedPluginSlot, openvikingDir, defaultServerPortRaw] = process.argv.slice(2);
const defaultServerPort = Number.parseInt(defaultServerPortRaw, 10) || 1933;
const enc = (value) => Buffer.from(String(value ?? ""), "utf8").toString("base64");
const variants = [
  { id: "memory-openviking", dir: "openclaw-memory-plugin", generation: "legacy", slot: "memory", slotFallback: "none" },
  { id: "openviking", dir: "openclaw-plugin", generation: "current", slot: "contextEngine", slotFallback: "legacy" },
];

function readJson(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return null;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function getInstallStatePath(pluginId) {
  return path.join(openclawDir, "extensions", pluginId, ".ov-install-state.json");
}

function detectPresence(config, variant) {
  const plugins = config?.plugins;
  const reasons = [];
  if (plugins) {
    if (plugins.entries && Object.prototype.hasOwnProperty.call(plugins.entries, variant.id)) reasons.push("entry");
    if (plugins.slots?.[variant.slot] === variant.id) reasons.push("slot");
    if (Array.isArray(plugins.allow) && plugins.allow.includes(variant.id)) reasons.push("allow");
    if (Array.isArray(plugins.load?.paths) && plugins.load.paths.some((item) => typeof item === "string" && (item.includes(variant.id) || item.includes(variant.dir)))) {
      reasons.push("loadPath");
    }
  }
  if (fs.existsSync(path.join(openclawDir, "extensions", variant.id))) reasons.push("dir");
  return { variant, present: reasons.length > 0, reasons };
}

function formatDetectionLabel(detection) {
  const requestedRef = detection.installState?.requestedRef;
  const releaseId = detection.installState?.releaseId;
  if (requestedRef) return `${detection.variant.id}@${requestedRef}`;
  if (releaseId) return `${detection.variant.id}#${releaseId}`;
  return `${detection.variant.id} (${detection.variant.generation}, exact version unknown)`;
}

function extractRuntimeConfig(entryConfig) {
  if (!entryConfig || typeof entryConfig !== "object") return null;
  const mode = entryConfig.mode === "remote" ? "remote" : "local";
  const runtime = { mode };
  if (mode === "remote") {
    if (typeof entryConfig.baseUrl === "string" && entryConfig.baseUrl.trim()) runtime.baseUrl = entryConfig.baseUrl.trim();
    if (typeof entryConfig.apiKey === "string" && entryConfig.apiKey.trim()) runtime.apiKey = entryConfig.apiKey;
    if (typeof entryConfig.agentId === "string" && entryConfig.agentId.trim()) runtime.agentId = entryConfig.agentId.trim();
    return runtime;
  }
  if (typeof entryConfig.configPath === "string" && entryConfig.configPath.trim()) runtime.configPath = entryConfig.configPath.trim();
  if (entryConfig.port !== undefined && entryConfig.port !== null && `${entryConfig.port}`.trim()) {
    const port = Number.parseInt(String(entryConfig.port), 10);
    if (Number.isFinite(port) && port > 0) runtime.port = port;
  }
  return runtime;
}

function readPortFromOvConf(filePath) {
  const targetPath = filePath || path.join(openvikingDir, "ov.conf");
  if (!fs.existsSync(targetPath)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(targetPath, "utf8"));
    const port = Number.parseInt(String(data?.server?.port ?? ""), 10);
    return Number.isFinite(port) && port > 0 ? port : null;
  } catch {
    return null;
  }
}

function shouldClaimTargetSlot(config, detections) {
  const currentOwner = config?.plugins?.slots?.[resolvedPluginSlot];
  if (!currentOwner || currentOwner === "none" || currentOwner === "legacy" || currentOwner === resolvedPluginId) return true;
  return detections.some((item) => item.variant.id === currentOwner);
}

const config = readJson(configPath);
const detections = variants
  .map((variant) => {
    const detection = detectPresence(config, variant);
    if (!detection.present) return null;
    detection.installState = readJson(getInstallStatePath(variant.id));
    return detection;
  })
  .filter(Boolean);

let generation = "none";
if (detections.length === 1) generation = detections[0].variant.generation;
if (detections.length > 1) generation = "mixed";

let runtime = null;
const candidateOrder = [...detections].sort((left, right) => (right.variant.generation === "current" ? 1 : 0) - (left.variant.generation === "current" ? 1 : 0));
for (const detection of candidateOrder) {
  const entryConfig = extractRuntimeConfig(config?.plugins?.entries?.[detection.variant.id]?.config);
  if (entryConfig) {
    runtime = entryConfig;
    break;
  }
}

if (!runtime) runtime = { mode: "local" };
if (runtime.mode === "remote") {
  runtime.baseUrl = runtime.baseUrl || "http://127.0.0.1:1933";
} else {
  runtime.configPath = runtime.configPath || path.join(openvikingDir, "ov.conf");
  runtime.port = runtime.port || readPortFromOvConf(runtime.configPath) || defaultServerPort;
}

const fromVersion = detections.length
  ? detections.map(formatDetectionLabel).join(" + ")
  : "not-installed";

console.log(`generation=${generation}`);
console.log(`fromVersion=${enc(fromVersion)}`);
console.log(`claimSlot=${shouldClaimTargetSlot(config, detections) ? "1" : "0"}`);
console.log(`mode=${runtime.mode === "remote" ? "remote" : "local"}`);
console.log(`configPath=${enc(runtime.configPath || "")}`);
console.log(`port=${runtime.port || ""}`);
console.log(`baseUrl=${enc(runtime.baseUrl || "")}`);
console.log(`apiKey=${enc(runtime.apiKey || "")}`);
console.log(`agentId=${enc(runtime.agentId || "")}`);
for (const detection of detections) {
  console.log(`detectionId=${detection.variant.id}`);
}
NODE
)
}

cleanup_installed_plugin_config() {
  local config_path
  config_path="$(get_openclaw_config_path)"
  [[ -f "${config_path}" ]] || return 0
  [[ ${#UPGRADE_DETECTED_IDS[@]} -gt 0 ]] || return 0

  local status
  status="$(node - "${config_path}" "${UPGRADE_DETECTED_IDS[@]}" <<'NODE'
const fs = require("fs");

const [configPath, ...pluginIds] = process.argv.slice(2);
const variantMap = {
  "memory-openviking": { id: "memory-openviking", dir: "openclaw-memory-plugin", slot: "memory", slotFallback: "none" },
  "openviking": { id: "openviking", dir: "openclaw-plugin", slot: "contextEngine", slotFallback: "legacy" },
};

const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
const plugins = config?.plugins;
if (!plugins) {
  console.log("no-plugins");
  process.exit(0);
}

let changed = false;
for (const pluginId of pluginIds) {
  const variant = variantMap[pluginId];
  if (!variant) continue;
  if (Array.isArray(plugins.allow)) {
    const nextAllow = plugins.allow.filter((item) => item !== variant.id);
    changed = changed || nextAllow.length !== plugins.allow.length;
    plugins.allow = nextAllow;
  }
  if (Array.isArray(plugins.load?.paths)) {
    const nextPaths = plugins.load.paths.filter((item) => typeof item !== "string" || (!item.includes(variant.id) && !item.includes(variant.dir)));
    changed = changed || nextPaths.length !== plugins.load.paths.length;
    plugins.load.paths = nextPaths;
  }
  if (plugins.entries && Object.prototype.hasOwnProperty.call(plugins.entries, variant.id)) {
    delete plugins.entries[variant.id];
    changed = true;
  }
  if (plugins.slots?.[variant.slot] === variant.id) {
    plugins.slots[variant.slot] = variant.slotFallback;
    changed = true;
  }
}

if (changed) {
  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`);
  console.log("changed");
} else {
  console.log("unchanged");
}
NODE
)"

  case "${status}" in
    changed)
      info "Cleaned existing OpenViking plugin config only"
      ;;
    no-plugins)
      warn "openclaw.json has no plugins section; skipped targeted plugin cleanup"
      ;;
    *)
      info "No OpenViking plugin config changes were required"
      ;;
  esac
}

prepare_strong_plugin_upgrade() {
  detect_installed_plugin_state
  if [[ "${UPGRADE_DETECTED_GENERATION}" == "none" ]]; then
    err "Plugin upgrade mode requires an existing OpenViking plugin entry in openclaw.json."
    exit 1
  fi

  SELECTED_MODE="${UPGRADE_RUNTIME_MODE:-local}"
  if [[ "${SELECTED_MODE}" == "remote" ]]; then
    remote_base_url="${UPGRADE_RUNTIME_BASE_URL:-http://127.0.0.1:1933}"
    remote_api_key="${UPGRADE_RUNTIME_API_KEY:-}"
    remote_agent_id="${UPGRADE_RUNTIME_AGENT_ID:-}"
  else
    SELECTED_CONFIG_PATH="${UPGRADE_RUNTIME_CONFIG_PATH:-${OPENVIKING_DIR}/ov.conf}"
    SELECTED_SERVER_PORT="${UPGRADE_RUNTIME_PORT:-${DEFAULT_SERVER_PORT}}"
  fi

  local to_version
  to_version="$(format_target_version_label)"
  info "$(tr "Detected installed OpenViking plugin state: ${UPGRADE_DETECTED_GENERATION}" "检测到已安装的 OpenViking 插件状态: ${UPGRADE_DETECTED_GENERATION}")"
  info "$(tr "Upgrade runtime mode: ${SELECTED_MODE}" "升级运行模式: ${SELECTED_MODE}")"
  info "$(tr "Upgrade path: ${UPGRADE_DETECTED_FROM_VERSION} -> ${to_version}" "升级路径: ${UPGRADE_DETECTED_FROM_VERSION} -> ${to_version}")"

  stop_openclaw_gateway_for_upgrade

  local config_path
  config_path="$(get_openclaw_config_path)"
  if [[ ! -f "${config_path}" ]]; then
    err "Plugin upgrade mode requires ${config_path} to exist."
    exit 1
  fi

  UPGRADE_AUDIT_PLUGIN_BACKUPS=()
  UPGRADE_AUDIT_OPERATION="upgrade"
  UPGRADE_AUDIT_CREATED_AT="$(iso_now)"
  UPGRADE_AUDIT_FROM_VERSION="${UPGRADE_DETECTED_FROM_VERSION}"
  UPGRADE_AUDIT_TO_VERSION="${to_version}"
  UPGRADE_AUDIT_CONFIG_BACKUP_PATH="$(backup_openclaw_config "${config_path}")"
  UPGRADE_AUDIT_RUNTIME_MODE="${SELECTED_MODE}"
  UPGRADE_AUDIT_COMPLETED_AT=""
  UPGRADE_AUDIT_ROLLED_BACK_AT=""
  UPGRADE_AUDIT_ROLLBACK_CONFIG_PATH=""

  info "$(tr "Backed up openclaw.json: ${UPGRADE_AUDIT_CONFIG_BACKUP_PATH}" "已备份 openclaw.json: ${UPGRADE_AUDIT_CONFIG_BACKUP_PATH}")"

  local detected_id backup_dir
  for detected_id in "${UPGRADE_DETECTED_IDS[@]}"; do
    backup_dir="$(backup_plugin_directory "${detected_id}")"
    if [[ -n "${backup_dir}" ]]; then
      UPGRADE_AUDIT_PLUGIN_BACKUPS+=("${detected_id}|${backup_dir}")
    fi
  done

  write_upgrade_audit_file
  cleanup_installed_plugin_config
  info "Upgrade will keep the existing OpenViking runtime file and re-apply only the minimum plugin runtime settings."
  info "Upgrade audit file: $(get_upgrade_audit_path)"
}

rollback_last_upgrade_operation() {
  local audit_path
  audit_path="$(get_upgrade_audit_path)"
  if ! load_upgrade_audit_file; then
    err "No rollback audit file found at ${audit_path}."
    exit 1
  fi

  if [[ -n "${UPGRADE_AUDIT_ROLLED_BACK_AT}" ]]; then
    warn "The last recorded upgrade was already rolled back at ${UPGRADE_AUDIT_ROLLED_BACK_AT}."
  fi

  local config_backup_path="${UPGRADE_AUDIT_CONFIG_BACKUP_PATH:-$(get_openclaw_config_backup_path)}"
  if [[ ! -f "${config_backup_path}" ]]; then
    err "Rollback config backup is missing: ${config_backup_path}"
    exit 1
  fi

  if [[ ${#UPGRADE_AUDIT_PLUGIN_BACKUPS[@]} -eq 0 ]]; then
    err "Rollback audit file contains no plugin backups."
    exit 1
  fi

  local backup_item plugin_id backup_dir
  for backup_item in "${UPGRADE_AUDIT_PLUGIN_BACKUPS[@]}"; do
    plugin_id="${backup_item%%|*}"
    backup_dir="${backup_item#*|}"
    if [[ -z "${plugin_id}" || -z "${backup_dir}" || ! -d "${backup_dir}" ]]; then
      err "Rollback plugin backup is missing: ${backup_dir:-<unknown>}"
      exit 1
    fi
  done

  info "Rolling back last upgrade: ${UPGRADE_AUDIT_FROM_VERSION:-unknown} <- ${UPGRADE_AUDIT_TO_VERSION:-unknown}"
  stop_openclaw_gateway_for_upgrade

  cp "${config_backup_path}" "$(get_openclaw_config_path)"
  info "Restored openclaw.json from backup: ${config_backup_path}"

  local extensions_dir="${OPENCLAW_DIR}/extensions"
  mkdir -p "${extensions_dir}"
  rm -rf "${extensions_dir}/memory-openviking" "${extensions_dir}/openviking"

  for backup_item in "${UPGRADE_AUDIT_PLUGIN_BACKUPS[@]}"; do
    plugin_id="${backup_item%%|*}"
    backup_dir="${backup_item#*|}"
    local dest_dir="${extensions_dir}/${plugin_id}"
    if ! mv "${backup_dir}" "${dest_dir}" 2>/dev/null; then
      cp -R "${backup_dir}" "${dest_dir}"
      rm -rf "${backup_dir}"
    fi
    info "Restored plugin directory: ${dest_dir}"
  done

  UPGRADE_AUDIT_ROLLED_BACK_AT="$(iso_now)"
  UPGRADE_AUDIT_ROLLBACK_CONFIG_PATH="${config_backup_path}"
  write_upgrade_audit_file

  echo ""
  bold "Rollback complete!"
  echo ""
  info "Rollback audit file: ${audit_path}"
  info "Run \`openclaw gateway\` and \`openclaw status\` to verify the restored plugin state."
}

# Prefer interactive mode. Even with curl | bash, try reading from /dev/tty.
# Fall back to defaults only when no interactive TTY is available.
if [[ ! -t 0 && "$INSTALL_YES" != "1" ]]; then
  if [[ ! -r /dev/tty ]]; then
    INSTALL_YES="1"
    echo "[WARN] No interactive TTY detected. Falling back to defaults (-y)."
  else
    echo "[INFO] Pipeline execution detected. Interactive prompts will use /dev/tty."
  fi
fi

# Colors and terminal attributes
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
    err "Windows is not supported by this installer yet. Please follow the docs for manual setup."
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

# ---- Workdir detection & mode selection ----

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

  # Only default instance or none 鈥?keep default
  if [[ ${#instances[@]} -le 1 ]]; then
    return 0
  fi

  # Multiple instances found 鈥?let user pick
  if [[ "$INSTALL_YES" != "1" ]]; then
    echo ""
    bold "Found multiple OpenClaw instances:"
    local i=1
    for inst in "${instances[@]}"; do
      echo "  ${i}) ${inst}"
      i=$((i + 1))
    done
    echo ""
    read -r -p "Select instance number [1]: " _choice < /dev/tty || true
    if [[ -n "$_choice" && "$_choice" =~ ^[0-9]+$ ]]; then
      local idx=$((_choice - 1))
      if [[ $idx -ge 0 && $idx -lt ${#instances[@]} ]]; then
        OPENCLAW_DIR="${instances[$idx]}"
      else
        warn "Invalid selection, using default"
        OPENCLAW_DIR="${instances[0]}"
      fi
    else
      OPENCLAW_DIR="${instances[0]}"
    fi
    PLUGIN_DEST="${OPENCLAW_DIR}/extensions/openviking"
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
    read -r -p "$(tr "OpenViking server URL [${remote_base_url}]: " "OpenViking 服务地址 [${remote_base_url}]: ")" _base_url < /dev/tty || true
    read -r -p "$(tr "API Key (optional): " "API Key（可选）: ")" _api_key < /dev/tty || true
    read -r -p "$(tr "Agent ID (optional): " "Agent ID（可选）: ")" _agent_id < /dev/tty || true
    remote_base_url="${_base_url:-$remote_base_url}"
    remote_api_key="${_api_key:-}"
    remote_agent_id="${_agent_id:-}"
  fi
}

# ---- Environment checks ----

check_python() {
  local py="${OPENVIKING_PYTHON:-python3}"
  local out
  if ! out=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null); then
    echo "fail|$py|$(tr "Python not found. Install Python >= 3.10." "未找到 Python，请安装 Python >= 3.10。")"
    return 1
  fi
  local major minor
  IFS=. read -r major minor <<< "$out"
  if [[ "$major" -lt 3 ]] || [[ "$major" -eq 3 && "$minor" -lt 10 ]]; then
    echo "fail|$out|$(tr "Python $out is too old. Need >= 3.10." "Python 版本 $out 过低，需要 >= 3.10。")"
    return 1
  fi
  echo "ok|$out|$py"
  return 0
}

check_node() {
  local out
  if ! out=$(node -v 2>/dev/null); then
    echo "fail||$(tr "Node.js not found. Install Node.js >= 22." "未找到 Node.js，请安装 Node.js >= 22。")"
    return 1
  fi
  local v="${out#v}"
  local major
  major="${v%%.*}"
  if [[ -z "$major" ]] || [[ "$major" -lt 22 ]]; then
    echo "fail|$out|$(tr "Node.js $out is too old. Need >= 22." "Node.js 版本 $out 过低，需要 >= 22。")"
    return 1
  fi
  echo "ok|$out|node"
  return 0
}

# Print guidance for missing dependencies
print_install_hints() {
  local missing=("$@")
  bold "\n============================================================"
  bold "  $(tr "Environment check failed. Install missing dependencies first:" "环境校验未通过，请先安装以下缺失项：")"
  bold "============================================================\n"

  for item in "${missing[@]}"; do
    local name="${item%%|*}"
    local rest="${item#*|}"
    err "$(tr "Missing: $name" "缺失: $name")"
    [[ -n "$rest" ]] && echo "  $rest"
    echo ""
  done

  detect_distro
  echo "Based on your system (${DISTRO}), you can run:"
  echo ""

  if printf '%s\n' "${missing[@]}" | grep -q "Python"; then
    echo "  # Install Python 3.10+ (pyenv recommended)"
    echo "  curl https://pyenv.run | bash"
    echo "  export PATH=\"\$HOME/.pyenv/bin:\$PATH\""
    echo "  eval \"\$(pyenv init -)\""
    echo "  pyenv install 3.11.12"
    echo "  pyenv global 3.11.12"
    echo "  python3 --version    # verify >= 3.10"
    echo ""
  fi

  if printf '%s\n' "${missing[@]}" | grep -q "Node"; then
    echo "  # Install Node.js 22+ (nvm)"
    echo "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
    echo "  source ~/.bashrc"
    echo "  nvm install 22"
    echo "  nvm use 22"
    echo "  node -v            # verify >= v22"
    echo ""
  fi

  bold "After installation, rerun this script."
  bold "See details: https://github.com/${REPO}/blob/${PLUGIN_VERSION}/examples/openclaw-plugin/INSTALL.md"
  echo ""
  exit 1
}

# Validate environment
validate_environment() {
  info "Checking OpenViking runtime environment..."
  echo ""

  local missing=()
  local r

  r=$(check_python) || missing+=("Python 3.10+ | $(echo "$r" | cut -d'|' -f3)")
  if [[ "${r%%|*}" == "ok" ]]; then
    info "  Python: $(echo "$r" | cut -d'|' -f2) OK"
  fi

  r=$(check_node) || missing+=("Node.js 22+ | $(echo "$r" | cut -d'|' -f3)")
  if [[ "${r%%|*}" == "ok" ]]; then
    info "  Node.js: $(echo "$r" | cut -d'|' -f2) OK"
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo ""
    print_install_hints "${missing[@]}"
  fi

  echo ""
  info "$(tr "Environment check passed." "环境校验通过。")"
  echo ""
}

# ---- Version detection & manifest ----

# Compare versions: version_gte "3.7.0" "3.7" returns 0 (true) if v1 >= v2
version_gte() {
  local v1="${1#v}"
  local v2="${2#v}"
  v1="${v1%%-*}"
  v2="${v2%%-*}"
  if [[ "$(printf '%s\n%s' "$v2" "$v1" | sort -V | head -1)" == "$v2" ]]; then
    return 0
  else
    return 1
  fi
}

is_semver_like() {
  [[ "$1" =~ ^v?[0-9]+(\.[0-9]+){1,2}$ ]]
}

pick_latest_plugin_tag() {
  local latest_semver=""
  local first_tag=""
  local tag=""
  while IFS= read -r tag; do
    [[ -z "$tag" ]] && continue
    [[ -z "$first_tag" ]] && first_tag="$tag"
    if is_semver_like "$tag"; then
      if [[ -z "$latest_semver" ]] || version_gte "$tag" "$latest_semver"; then
        latest_semver="$tag"
      fi
    fi
  done

  if [[ -n "$latest_semver" ]]; then
    printf '%s\n' "$latest_semver"
    return 0
  fi

  if [[ -n "$first_tag" ]]; then
    printf '%s\n' "$first_tag"
  fi
}

resolve_default_plugin_version() {
  [[ -n "$PLUGIN_VERSION" ]] && return 0

  info "$(tr "No plugin version specified; resolving latest tag from ${REPO}..." "未指定插件版本，正在解析 ${REPO} 的最新 tag...")"

  local latest_tag=""
  local api_tags=""
  api_tags="$(
    curl -fsSL --connect-timeout 10 \
      -H "Accept: application/vnd.github+json" \
      -H "User-Agent: openviking-installer" \
      "https://api.github.com/repos/${REPO}/tags?per_page=100" 2>/dev/null \
      | tr -d '\r' \
      | grep -oE '"name":"[^"]+"' \
      | sed -E 's/^"name":"(.*)"$/\1/' \
      || true
  )"
  if [[ -n "$api_tags" ]]; then
    latest_tag="$(printf '%s\n' "$api_tags" | pick_latest_plugin_tag)"
  fi

  if [[ -z "$latest_tag" ]] && command -v git >/dev/null 2>&1; then
    local git_tags=""
    git_tags="$(
      git ls-remote --tags --refs "https://github.com/${REPO}.git" 2>/dev/null \
      | sed -E 's#^.*refs/tags/##' \
      || true
    )"
    if [[ -n "$git_tags" ]]; then
      latest_tag="$(printf '%s\n' "$git_tags" | pick_latest_plugin_tag)"
    fi
  fi

  if [[ -z "$latest_tag" ]]; then
    err "$(tr "Could not resolve the latest tag for ${REPO}." "无法解析 ${REPO} 的最新 tag。")"
    echo "$(tr "Please rerun with --plugin-version <tag>, or use --plugin-version main to track the branch head explicitly." "请使用 --plugin-version <tag> 重新执行；如果需要显式跟踪分支头，请使用 --plugin-version main。")"
    exit 1
  fi

  PLUGIN_VERSION="$latest_tag"
  info "$(tr "Resolved default plugin version to latest tag: ${PLUGIN_VERSION}" "已将默认插件版本解析为最新 tag: ${PLUGIN_VERSION}")"
}

# Detect OpenClaw version
detect_openclaw_version() {
  local version_output
  version_output=$(openclaw --version 2>/dev/null || echo "0.0.0")
  local version
  version=$(echo "$version_output" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
  if [[ -z "$version" ]]; then
    version="0.0.0"
  fi
  echo "$version"
}

# Parse JSON using Python (available since we need Python for OpenViking)
parse_json() {
  local json_file="$1"
  local key="$2"
  local py_json="${OPENVIKING_PYTHON:-}"
  [[ -z "$py_json" ]] && py_json="$(command -v python3 || command -v python || true)"
  [[ -z "$py_json" ]] && py_json="python3"
  "$py_json" -c "
import json
with open('$json_file') as f:
    data = json.load(f)
keys = '$key'.split('.')
for k in keys:
    if k.startswith('[') and k.endswith(']'):
        data = data[int(k[1:-1])]
    elif isinstance(data, dict):
        data = data.get(k)
    else:
        data = None
        break
if isinstance(data, list):
    print('\\n'.join(str(x) for x in data))
elif data is not None:
    print(data)
" 2>/dev/null || echo ""
}

# Fallback configs for old versions without manifest
# Format: plugin_dir|plugin_id|plugin_kind|plugin_slot|required_files|optional_files
FALLBACK_LEGACY="openclaw-memory-plugin|memory-openviking|memory|memory|index.ts,config.ts,openclaw.plugin.json,package.json|package-lock.json,.gitignore"
# Must match examples/openclaw-plugin/install-manifest.json files.*
FALLBACK_CURRENT="openclaw-plugin|openviking|context-engine|contextEngine|index.ts,config.ts,package.json|context-engine.ts,client.ts,process-manager.ts,memory-ranking.ts,text-utils.ts,tool-call-id.ts,session-transcript-repair.ts,openclaw.plugin.json,tsconfig.json,package-lock.json,.gitignore"

# Resolve plugin configuration from manifest or fallback
resolve_plugin_config() {
  local tag="$PLUGIN_VERSION"
  local gh_raw="https://raw.githubusercontent.com/${REPO}/${tag}"
  local manifest_file
  manifest_file=$(mktemp)
  local plugin_dir=""

  info "$(tr "Resolving plugin configuration for version: ${tag}" "正在解析插件配置，版本: ${tag}")"

  # Try to detect plugin directory
  if curl -fsSL --connect-timeout 10 "${gh_raw}/examples/openclaw-plugin/install-manifest.json" -o "$manifest_file" 2>/dev/null && [[ -s "$manifest_file" ]]; then
    plugin_dir="openclaw-plugin"
    info "$(tr "Found manifest in openclaw-plugin" "在 openclaw-plugin 中找到 manifest")"
  elif curl -fsSL --connect-timeout 10 "${gh_raw}/examples/openclaw-memory-plugin/install-manifest.json" -o "$manifest_file" 2>/dev/null && [[ -s "$manifest_file" ]]; then
    plugin_dir="openclaw-memory-plugin"
    info "Found manifest in openclaw-memory-plugin"
  elif curl -fsSL --connect-timeout 10 --head "${gh_raw}/examples/openclaw-plugin/index.ts" 2>/dev/null | grep -q "200"; then
    plugin_dir="openclaw-plugin"
    info "No manifest found, using fallback for openclaw-plugin"
    rm -f "$manifest_file"
  elif curl -fsSL --connect-timeout 10 --head "${gh_raw}/examples/openclaw-memory-plugin/index.ts" 2>/dev/null | grep -q "200"; then
    plugin_dir="openclaw-memory-plugin"
    info "No manifest found, using fallback for openclaw-memory-plugin"
    rm -f "$manifest_file"
  else
    rm -f "$manifest_file"
    err "Cannot find plugin directory for version: ${tag}"
    exit 1
  fi

  RESOLVED_PLUGIN_DIR="$plugin_dir"

  # Parse manifest or use fallback
  if [[ -s "$manifest_file" ]]; then
    RESOLVED_PLUGIN_ID=$(parse_json "$manifest_file" "plugin.id")
    RESOLVED_PLUGIN_KIND=$(parse_json "$manifest_file" "plugin.kind")
    RESOLVED_PLUGIN_SLOT=$(parse_json "$manifest_file" "plugin.slot")
    RESOLVED_MIN_OPENCLAW_VERSION=$(parse_json "$manifest_file" "compatibility.minOpenclawVersion")
    RESOLVED_MIN_OPENVIKING_VERSION=$(parse_json "$manifest_file" "compatibility.minOpenvikingVersion")
    RESOLVED_PLUGIN_RELEASE_ID=$(parse_json "$manifest_file" "release.id")
    RESOLVED_NPM_OMIT_DEV=$(parse_json "$manifest_file" "npm.omitDev")
    [[ -z "$RESOLVED_NPM_OMIT_DEV" ]] && RESOLVED_NPM_OMIT_DEV="true"

    # Parse file lists
    local required_str optional_str
    required_str=$(parse_json "$manifest_file" "files.required")
    optional_str=$(parse_json "$manifest_file" "files.optional")

    IFS=$'\n' read -r -d '' -a RESOLVED_FILES_REQUIRED <<< "$required_str" || true
    IFS=$'\n' read -r -d '' -a RESOLVED_FILES_OPTIONAL <<< "$optional_str" || true

    rm -f "$manifest_file"
  else
    # No manifest: determine plugin identity by package.json name
    local fallback_key="current"
    [[ "$plugin_dir" == "openclaw-memory-plugin" ]] && fallback_key="legacy"
    local compat_ver=""

    local pkg_json_file
    pkg_json_file=$(mktemp)
    if curl -fsSL --connect-timeout 10 "${gh_raw}/examples/${plugin_dir}/package.json" -o "$pkg_json_file" 2>/dev/null && [[ -s "$pkg_json_file" ]]; then
      local pkg_name
      pkg_name=$(parse_json "$pkg_json_file" "name")
      if [[ -n "$pkg_name" && "$pkg_name" != "@openclaw/openviking" ]]; then
        fallback_key="legacy"
        info "$(tr "Detected legacy plugin by package name: ${pkg_name}" "通过 package.json 名称检测到旧版插件: ${pkg_name}")"
      elif [[ -n "$pkg_name" ]]; then
        fallback_key="current"
      fi

      local engines_ver
      engines_ver=$(parse_json "$pkg_json_file" "engines.openclaw")
      engines_ver="${engines_ver#>=}"
      engines_ver="${engines_ver#>}"
      engines_ver="${engines_ver// /}"
      if [[ -n "$engines_ver" ]]; then
        compat_ver="$engines_ver"
        info "$(tr "Read minOpenclawVersion from package.json engines.openclaw: >=${engines_ver}" "从 package.json engines.openclaw 读取最低版本: >=${engines_ver}")"
      fi
    fi
    rm -f "$pkg_json_file"

    local fallback=""
    if [[ "$fallback_key" == "legacy" ]]; then
      fallback="$FALLBACK_LEGACY"
    else
      fallback="$FALLBACK_CURRENT"
    fi

    local fb_dir fb_id fb_kind fb_slot required_csv optional_csv
    IFS='|' read -r fb_dir fb_id fb_kind fb_slot required_csv optional_csv <<< "$fallback"
    RESOLVED_PLUGIN_DIR="$plugin_dir"
    RESOLVED_PLUGIN_ID="$fb_id"
    RESOLVED_PLUGIN_KIND="$fb_kind"
    RESOLVED_PLUGIN_SLOT="$fb_slot"
    IFS=',' read -r -a RESOLVED_FILES_REQUIRED <<< "$required_csv"
    IFS=',' read -r -a RESOLVED_FILES_OPTIONAL <<< "$optional_csv"
    RESOLVED_NPM_OMIT_DEV="true"
    RESOLVED_MIN_OPENVIKING_VERSION=""
    RESOLVED_PLUGIN_RELEASE_ID=""

    # If no compatVer from package.json, try main branch manifest
    if [[ -z "$compat_ver" && "$tag" != "main" ]]; then
      local main_manifest
      main_manifest=$(mktemp)
      local main_raw="https://raw.githubusercontent.com/${REPO}/main"
      if curl -fsSL --connect-timeout 10 "${main_raw}/examples/openclaw-plugin/install-manifest.json" -o "$main_manifest" 2>/dev/null && [[ -s "$main_manifest" ]]; then
        compat_ver=$(parse_json "$main_manifest" "compatibility.minOpenclawVersion")
        if [[ -n "$compat_ver" ]]; then
          info "$(tr "Read minOpenclawVersion from main branch manifest: >=${compat_ver}" "从 main 分支 manifest 读取最低版本: >=${compat_ver}")"
        fi
      fi
      rm -f "$main_manifest"
    fi

    RESOLVED_MIN_OPENCLAW_VERSION="${compat_ver:-2026.3.7}"
  fi

  # Set plugin destination
  PLUGIN_DEST="${OPENCLAW_DIR}/extensions/${RESOLVED_PLUGIN_ID}"

  info "$(tr "Plugin: ${RESOLVED_PLUGIN_ID} (${RESOLVED_PLUGIN_KIND})" "插件: ${RESOLVED_PLUGIN_ID} (${RESOLVED_PLUGIN_KIND})")"
}

# Check OpenClaw version compatibility
check_openclaw_compatibility() {
  if [[ "$SKIP_OC" == "1" ]]; then
    return 0
  fi

  local openclaw_version
  openclaw_version=$(detect_openclaw_version)
  info "$(tr "Detected OpenClaw version: ${openclaw_version}" "检测到 OpenClaw 版本: ${openclaw_version}")"

  # If no minimum version required, pass
  if [[ -z "$RESOLVED_MIN_OPENCLAW_VERSION" ]]; then
    return 0
  fi

  # If user explicitly requested an old version, pass
  if is_semver_like "$PLUGIN_VERSION" && ! version_gte "$PLUGIN_VERSION" "v0.2.8"; then
    return 0
  fi

  # Check compatibility
  if ! version_gte "$openclaw_version" "$RESOLVED_MIN_OPENCLAW_VERSION"; then
    err "OpenClaw ${openclaw_version} does not support this plugin (requires >= ${RESOLVED_MIN_OPENCLAW_VERSION})"
    echo ""
    bold "Please choose one of the following options:"
    echo ""
    echo "  Option 1: Upgrade OpenClaw"
    echo "    npm update -g openclaw --registry ${NPM_REGISTRY}"
    echo ""
    echo "  Option 2: Install a legacy plugin release compatible with your current OpenClaw version"
    echo "    $(legacy_plugin_install_hint)"
    echo ""
    exit 1








  fi

  return 0
}

check_requested_openviking_compatibility() {
  if [[ "$SKIP_OV" == "1" || -z "$RESOLVED_MIN_OPENVIKING_VERSION" || -z "$OPENVIKING_VERSION" ]]; then
    return 0
  fi

  if ! version_gte "$OPENVIKING_VERSION" "$RESOLVED_MIN_OPENVIKING_VERSION"; then
    err "OpenViking ${OPENVIKING_VERSION} does not support this plugin (requires >= ${RESOLVED_MIN_OPENVIKING_VERSION})"
    echo ""
    echo "  Use a newer OpenViking version, or omit --openviking-version to install the latest release."
    exit 1
  fi
}

# ---- Install flow ----

install_openclaw() {
  if [[ "$SKIP_OC" == "1" ]]; then
    info "$(tr "Skipping OpenClaw check (SKIP_OPENCLAW=1)" "跳过 OpenClaw 检查 (SKIP_OPENCLAW=1)")"
    return 0
  fi
  info "$(tr "Checking OpenClaw..." "正在检查 OpenClaw...")"
  if command -v openclaw >/dev/null 2>&1; then
    info "$(tr "OpenClaw detected." "已检测到 OpenClaw。")"
    return 0
  fi

  err "OpenClaw not found. Install it manually, then rerun this script."
  echo ""
  echo "Recommended command:"
  echo "  npm install -g openclaw --registry ${NPM_REGISTRY}"
  echo ""
  echo "If npm global install fails, install Node via nvm and retry."
  echo "After installation, run:"
  echo "  openclaw --version"
  echo "  openclaw onboard"
  echo ""
  exit 1
}

run_pip_install_capture() {
  local log_file="$1"
  shift
  : > "${log_file}"
  if "$@" > >(tee "${log_file}") 2> >(tee -a "${log_file}" >&2); then
    return 0
  fi
  return 1
}

install_openviking() {
  if [[ "$SKIP_OV" == "1" ]]; then
    info "$(tr "Skipping OpenViking install (SKIP_OPENVIKING=1)" "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)")"
    return 0
  fi
  local py="${OPENVIKING_PYTHON:-python3}"

  # Determine package spec
  local pkg_spec="openviking"
  if [[ -n "$OPENVIKING_VERSION" ]]; then
    pkg_spec="openviking==${OPENVIKING_VERSION}"
    info "$(tr "Installing OpenViking ${OPENVIKING_VERSION} from PyPI..." "正在安装 OpenViking ${OPENVIKING_VERSION} (PyPI)...")"
  else
    info "$(tr "Installing OpenViking (latest) from PyPI..." "正在安装 OpenViking (最新版) (PyPI)...")"
  fi
  info "$(tr "Using pip index: ${PIP_INDEX_URL}" "使用 pip 镜像: ${PIP_INDEX_URL}")"

  info "Package: ${pkg_spec}"

  # Try system-wide pip first (works on many systems)
  local err_out=""
  local pip_log
  pip_log=$(mktemp)
  "$py" -m pip install --upgrade pip -q -i "${PIP_INDEX_URL}" >/dev/null 2>&1 || true
  if run_pip_install_capture "${pip_log}" "$py" -m pip install --progress-bar on "$pkg_spec" -i "${PIP_INDEX_URL}"; then
    rm -f "${pip_log}" 2>/dev/null || true
    OPENVIKING_PYTHON_PATH="$(command -v "$py" || true)"
    [[ -z "$OPENVIKING_PYTHON_PATH" ]] && OPENVIKING_PYTHON_PATH="$py"
    info "$(tr "OpenViking installed." "OpenViking 安装完成。")"
    return 0
  fi

  err_out="$(cat "${pip_log}" 2>/dev/null || true)"
  rm -f "${pip_log}" 2>/dev/null || true

  # When system has no pip, or PEP 668 (Debian/Ubuntu): use a venv
  if echo "$err_out" | grep -q "externally-managed-environment\|externally managed\|No module named pip"; then
    if echo "$err_out" | grep -q "No module named pip"; then
      info "System Python has no pip. Using a venv at ~/.openviking/venv"
    else
      # Opt-in: allow install with --break-system-packages when venv is not available (PEP 668 only, default off)
      if [[ "${OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES}" == "1" ]]; then
        info "Installing OpenViking with --break-system-packages (OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1)"
        if "$py" -m pip install --progress-bar on --break-system-packages "$pkg_spec" -i "${PIP_INDEX_URL}"; then
          OPENVIKING_PYTHON_PATH="$(command -v "$py" || true)"
          [[ -z "$OPENVIKING_PYTHON_PATH" ]] && OPENVIKING_PYTHON_PATH="$py"
          info "OpenViking installed (system)"
          return 0
        fi
      fi
      info "System Python is externally managed (PEP 668). Using a venv at ~/.openviking/venv"
    fi
    mkdir -p "${OPENVIKING_DIR}"
    local venv_dir="${OPENVIKING_DIR}/venv"
    local venv_py="${venv_dir}/bin/python"

    # Reuse existing venv if it has openviking (avoid repeated create on re-run)
    if [[ -x "${venv_py}" ]] && "${venv_py}" -c "import openviking" 2>/dev/null; then
      info "Using existing venv with openviking: ${venv_dir}"
      "${venv_py}" -m pip install --progress-bar on -U "$pkg_spec" -i "${PIP_INDEX_URL}" || true
      OPENVIKING_PYTHON_PATH="${venv_dir}/bin/python"
      info "OpenViking installed (venv)"
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
        elif echo "${PIP_INDEX_URL}" | grep -q "volces\|tuna.tsinghua\|pypi.tuna"; then
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
      err "Could not create venv. Install venv then re-run:"
      echo "  sudo apt install python${py_ver}-venv   # or python3-full"
      echo ""
      echo "Or (may conflict with system packages):"
      echo "  OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1 curl -fsSL ... | bash"
      exit 1
    fi

    "$venv_py" -m pip install --upgrade pip -q -i "${PIP_INDEX_URL}" >/dev/null 2>&1
    if ! "$venv_py" -m pip install --progress-bar on "$pkg_spec" -i "${PIP_INDEX_URL}"; then
      err "OpenViking install failed in venv."
      exit 1
    fi
    OPENVIKING_PYTHON_PATH="${venv_dir}/bin/python"
    info "OpenViking installed (venv)"
    return 0
  fi

  err "$(tr "OpenViking install failed. Check Python version (>=3.10) and pip." "OpenViking 安装失败，请检查 Python 版本（需 >= 3.10）与 pip。")"
  echo "$err_out" >&2
  exit 1
}

normalize_port() {
  local value="$1"
  local default_value="$2"
  local label="$3"
  if [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= 65535 )); then
    echo "$value"
    return 0
  fi
  if [[ -n "$value" ]]; then
    warn "Invalid ${label}: ${value}. Using ${default_value}."
  fi
  echo "$default_value"
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

  if [[ "$INSTALL_YES" != "1" ]]; then
    echo ""
    read -r -p "$(tr "OpenViking workspace path [${workspace}]: " "OpenViking 数据目录 [${workspace}]: ")" _workspace < /dev/tty || true
    read -r -p "OpenViking HTTP port [${server_port}]: " _server_port < /dev/tty || true
    read -r -p "AGFS port [${agfs_port}]: " _agfs_port < /dev/tty || true
    read -r -p "VLM model [${vlm_model}]: " _vlm_model < /dev/tty || true
    read -r -p "Embedding model [${embedding_model}]: " _embedding_model < /dev/tty || true
    echo "VLM and Embedding API keys can differ. You can leave either empty and edit ov.conf later."
    read -r -p "VLM API key (optional): " _vlm_api_key < /dev/tty || true
    read -r -p "Embedding API key (optional): " _embedding_api_key < /dev/tty || true

    workspace="${_workspace:-$workspace}"
    server_port="${_server_port:-$server_port}"
    agfs_port="${_agfs_port:-$agfs_port}"
    vlm_model="${_vlm_model:-$vlm_model}"
    embedding_model="${_embedding_model:-$embedding_model}"
    vlm_api_key="${_vlm_api_key:-$vlm_api_key}"
    embedding_api_key="${_embedding_api_key:-$embedding_api_key}"
  fi

  server_port="$(normalize_port "${server_port}" "${DEFAULT_SERVER_PORT}" "OpenViking HTTP port")"
  agfs_port="$(normalize_port "${agfs_port}" "${DEFAULT_AGFS_PORT}" "AGFS port")"
  mkdir -p "${workspace}"
  local py_json="${OPENVIKING_PYTHON_PATH:-${OPENVIKING_PYTHON:-}}"
  [[ -z "$py_json" ]] && py_json="$(command -v python3 || command -v python || true)"
  [[ -z "$py_json" ]] && py_json="python3"
  WORKSPACE="${workspace}" \
  SERVER_PORT="${server_port}" \
  AGFS_PORT="${agfs_port}" \
  VLM_MODEL="${vlm_model}" \
  EMBEDDING_MODEL="${embedding_model}" \
  VLM_API_KEY="${vlm_api_key}" \
  EMBEDDING_API_KEY="${embedding_api_key}" \
  "$py_json" - <<'PY' > "${conf_path}"
import json
import os

def maybe_value(name):
    value = os.environ.get(name, "")
    return value or None

config = {
    "server": {
        "host": "127.0.0.1",
        "port": int(os.environ["SERVER_PORT"]),
        "root_api_key": None,
        "cors_origins": ["*"],
    },
    "storage": {
        "workspace": os.environ["WORKSPACE"],
        "vectordb": {"name": "context", "backend": "local", "project": "default"},
        "agfs": {
            "port": int(os.environ["AGFS_PORT"]),
            "log_level": "warn",
            "backend": "local",
            "timeout": 10,
            "retry_times": 3,
        },
    },
    "embedding": {
        "dense": {
            "provider": "volcengine",
            "api_key": maybe_value("EMBEDDING_API_KEY"),
            "model": os.environ["EMBEDDING_MODEL"],
            "api_base": "https://ark.cn-beijing.volces.com/api/v3",
            "dimension": 1024,
            "input": "multimodal",
        }
    },
    "log": {
        "level": "WARNING",
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "output": "file",
        "rotation": True,
        "rotation_days": 3,
        "rotation_interval": "midnight",
    },
    "vlm": {
        "provider": "volcengine",
        "api_key": maybe_value("VLM_API_KEY"),
        "model": os.environ["VLM_MODEL"],
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "temperature": 0.1,
        "max_retries": 3,
    },
}

print(json.dumps(config, ensure_ascii=False, indent=2))
PY
  SELECTED_SERVER_PORT="${server_port}"
  SELECTED_CONFIG_PATH="${conf_path}"
  info "$(tr "Config generated: ${conf_path}" "已生成配置: ${conf_path}")"
}

download_plugin() {
  local gh_raw="https://raw.githubusercontent.com/${REPO}/${PLUGIN_VERSION}"
  local plugin_dir="$RESOLVED_PLUGIN_DIR"
  local total=$(( ${#RESOLVED_FILES_REQUIRED[@]} + ${#RESOLVED_FILES_OPTIONAL[@]} ))
  local i=0
  local max_retries=3

  mkdir -p "${PLUGIN_DEST}"
  info "$(tr "Downloading plugin from ${REPO}@${PLUGIN_VERSION} (${total} files)..." "正在从 ${REPO}@${PLUGIN_VERSION} 下载插件（共 ${total} 个文件）...")"

  # Download required files
  for name in "${RESOLVED_FILES_REQUIRED[@]}"; do
    [[ -z "$name" ]] && continue
    i=$((i + 1))
    local url="${gh_raw}/examples/${plugin_dir}/${name}"
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
      echo " OK"
    else
      echo ""
      err "$(tr "Download failed after ${max_retries} retries: ${url}" "下载失败（已重试 ${max_retries} 次）: ${url}")"
      exit 1
    fi
  done

  # Download optional files (retry like required; HTTP 404 skips silently — only non-404 failures are reported)
  for name in "${RESOLVED_FILES_OPTIONAL[@]}"; do
    [[ -z "$name" ]] && continue
    i=$((i + 1))
    local url="${gh_raw}/examples/${plugin_dir}/${name}"
    local dest="${PLUGIN_DEST}/${name}"
    echo -n "  [${i}/${total}] ${name} "
    local attempt=1
    local code=""
    local got404=0
    while [[ "$attempt" -le "${max_retries}" ]]; do
      rm -f "${dest}"
      code="$(curl -o "${dest}" -sS -w "%{http_code}" --connect-timeout 15 --max-time 120 "${url}" 2>/dev/null || echo "000")"
      if [[ "$code" == "200" ]] && [[ -s "${dest}" ]]; then
        echo " OK"
        got404=0
        break
      fi
      rm -f "${dest}"
      if [[ "$code" == "404" ]]; then
        got404=1
        break
      fi
      [[ "$attempt" -lt "${max_retries}" ]] && sleep 2
      attempt=$((attempt + 1))
    done
    if [[ "$code" == "200" ]] && [[ -s "${dest}" ]]; then
      continue
    fi
    if [[ "$got404" -eq 1 ]] || [[ "$code" == "404" ]]; then
      if [[ "$name" == ".gitignore" ]]; then
        echo "node_modules/" > "${dest}"
        echo " OK"
      else
        echo " $(tr "skip" "跳过")"
      fi
      continue
    fi
    echo ""
    err "$(tr "Optional file download failed after ${max_retries} retries (HTTP ${code:-unknown}). Check network or URL: ${url}" "可选文件已重试 ${max_retries} 次仍失败（HTTP ${code:-unknown}）。请检查网络或 URL: ${url}")"
    exit 1
  done

  # npm install
  info "$(tr "Installing plugin npm dependencies..." "正在安装插件 npm 依赖...")"
  local npm_args="--no-audit --no-fund"
  if [[ "$RESOLVED_NPM_OMIT_DEV" == "true" ]]; then
    npm_args="--omit=dev $npm_args"
  fi
  (cd "${PLUGIN_DEST}" && npm install --registry "${NPM_REGISTRY}" $npm_args) || {
    err "$(tr "Plugin dependency install failed: ${PLUGIN_DEST}" "插件依赖安装失败: ${PLUGIN_DEST}")"
    exit 1
  }
  info "$(tr "Plugin deployed: ${PLUGIN_DEST}" "插件部署完成: ${PLUGIN_DEST}")"
}

ensure_existing_plugin_for_upgrade() {
  if [[ ! -d "${PLUGIN_DEST}" ]]; then
    err "Plugin upgrade mode expects an existing plugin install. Run the full installer first."
    exit 1
  fi
}

create_plugin_staging_dir() {
  local plugin_id="${RESOLVED_PLUGIN_ID:-openviking}"
  local extensions_dir="${OPENCLAW_DIR}/extensions"
  local staging_dir="${extensions_dir}/.${plugin_id}.staging.$$.$RANDOM"
  mkdir -p "${extensions_dir}"
  rm -rf "${staging_dir}"
  mkdir -p "${staging_dir}"
  echo "${staging_dir}"
}

finalize_plugin_deployment() {
  local staging_dir="$1"
  rm -rf "${PLUGIN_DEST}"
  if ! mv "${staging_dir}" "${PLUGIN_DEST}" 2>/dev/null; then
    cp -R "${staging_dir}" "${PLUGIN_DEST}"
    rm -rf "${staging_dir}"
  fi
  info "Plugin deployed: ${PLUGIN_DEST}"
}

deploy_plugin_from_remote() {
  local final_dest="${PLUGIN_DEST}"
  local staging_dir
  staging_dir="$(create_plugin_staging_dir)"
  PLUGIN_DEST="${staging_dir}"
  if download_plugin; then
    PLUGIN_DEST="${final_dest}"
    finalize_plugin_deployment "${staging_dir}"
    return 0
  fi

  local status=$?
  PLUGIN_DEST="${final_dest}"
  rm -rf "${staging_dir}"
  return "${status}"
}

# Remove stale OpenClaw registration for this plugin id so a fresh deploy can be discovered.
# Matches INSTALL*.md manual cleanup (stale entries + slot + allow + load.paths).
resolved_plugin_slot_fallback() {
  case "${RESOLVED_PLUGIN_ID}" in
    memory-openviking) printf '%s\n' "none" ;;
    openviking) printf '%s\n' "legacy" ;;
    *) printf '%s\n' "none" ;;
  esac
}

scrub_stale_openclaw_plugin_registration() {
  local config_path
  config_path="$(get_openclaw_config_path)"
  [[ -f "${config_path}" ]] || return 0
  local plugin_id="$RESOLVED_PLUGIN_ID"
  local plugin_slot="$RESOLVED_PLUGIN_SLOT"
  local slot_fallback
  slot_fallback="$(resolved_plugin_slot_fallback)"

  node - "${config_path}" "${plugin_id}" "${plugin_slot}" "${slot_fallback}" <<'NODE' || true
const fs = require("fs");
const configPath = process.argv[2];
const pluginId = process.argv[3];
const slot = process.argv[4];
const slotFallback = process.argv[5];

let raw;
try {
  raw = fs.readFileSync(configPath, "utf8");
} catch {
  process.exit(0);
}
let cfg;
try {
  cfg = JSON.parse(raw);
} catch {
  process.exit(0);
}
if (!cfg.plugins) process.exit(0);

let changed = false;
const p = cfg.plugins;

if (p.entries && Object.prototype.hasOwnProperty.call(p.entries, pluginId)) {
  delete p.entries[pluginId];
  changed = true;
}

if (Array.isArray(p.allow)) {
  const next = p.allow.filter((id) => id !== pluginId);
  if (next.length !== p.allow.length) {
    p.allow = next;
    changed = true;
  }
}

if (p.load && Array.isArray(p.load.paths)) {
  const norm = (s) => String(s).replace(/\\/g, "/");
  const extNeedle = `/extensions/${pluginId}`;
  const next = p.load.paths.filter((path) => {
    if (typeof path !== "string") return true;
    const n = norm(path);
    return !n.includes(extNeedle);
  });
  if (next.length !== p.load.paths.length) {
    p.load.paths = next;
    changed = true;
  }
}

if (p.slots && p.slots[slot] === pluginId) {
  p.slots[slot] = slotFallback;
  changed = true;
}

if (!changed) process.exit(0);

const out = JSON.stringify(cfg, null, 2) + "\n";
const tmp = `${configPath}.ov-install-tmp.${process.pid}`;
fs.writeFileSync(tmp, out, "utf8");
fs.renameSync(tmp, configPath);
NODE
}

configure_openclaw_plugin() {
  local preserve_existing_config="${1:-0}"
  local skip_gateway_mode="${2:-0}"
  local claim_slot="${3:-1}"
  info "$(tr "Configuring OpenClaw plugin..." "正在配置 OpenClaw 插件...")"

  local plugin_id="$RESOLVED_PLUGIN_ID"
  local plugin_slot="$RESOLVED_PLUGIN_SLOT"

  local oc_env=()
  if [[ "$OPENCLAW_DIR" != "${HOME_DIR}/.openclaw" ]]; then
    oc_env=(env OPENCLAW_STATE_DIR="$OPENCLAW_DIR")
  fi

  # Stale plugins.entries / slots / allow from a previous partial install break `config set plugins.slots.*`.
  if [[ "${preserve_existing_config}" != "1" ]]; then
    scrub_stale_openclaw_plugin_registration
  fi

  # Enable plugin (files already deployed to extensions dir by deploy_plugin)
  "${oc_env[@]}" openclaw plugins enable "$plugin_id" || { err "openclaw plugins enable failed"; exit 1; }
  if [[ "${claim_slot}" == "1" ]]; then
    "${oc_env[@]}" openclaw config set "plugins.slots.${plugin_slot}" "$plugin_id"
  else
    warn "Skipped claiming plugins.slots.${plugin_slot}; it is currently owned by another plugin."
  fi
  if [[ "${preserve_existing_config}" == "1" ]]; then
    info "Preserved existing plugin runtime config"
    return 0
  fi

  # Set gateway mode
  if [[ "${skip_gateway_mode}" != "1" ]]; then
    "${oc_env[@]}" openclaw config set gateway.mode "${SELECTED_MODE}"
  fi

  # Set plugin config for the selected mode
  if [[ "$SELECTED_MODE" == "local" ]]; then
    local ov_conf_path="${SELECTED_CONFIG_PATH:-${OPENVIKING_DIR}/ov.conf}"
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.mode" local
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.configPath" "${ov_conf_path}"
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.port" "${SELECTED_SERVER_PORT}"
  else
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.mode" remote
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.baseUrl" "${remote_base_url}"
    if [[ -n "${remote_api_key}" ]]; then
      "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.apiKey" "${remote_api_key}"
    fi
    if [[ -n "${remote_agent_id}" ]]; then
      "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.agentId" "${remote_agent_id}"
    fi
  fi

  # Legacy (memory) plugins need explicit targetUri/autoRecall/autoCapture (new version has defaults in config.ts)
  if [[ "${RESOLVED_PLUGIN_KIND}" == "memory" ]]; then
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.targetUri" "viking://user/memories"
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.autoRecall" true --json
    "${oc_env[@]}" openclaw config set "plugins.entries.${plugin_id}.config.autoCapture" true --json
  fi

  info "$(tr "OpenClaw plugin configured" "OpenClaw 插件配置完成")"
}

_deprecated_write_openviking_env() {
  local py_path
  if [[ -n "${OPENVIKING_PYTHON_PATH}" ]]; then
    py_path="${OPENVIKING_PYTHON_PATH}"
  else
    py_path="$(command -v python3 || command -v python || true)"
  fi
  if [[ -z "$py_path" ]]; then
    py_path="${OPENVIKING_PYTHON:-python3}"
    warn "Could not resolve Python path; using OPENVIKING_PYTHON or python3 in openviking.env. Edit the file if startup fails."
  fi
  mkdir -p "${OPENCLAW_DIR}"
  cat > "${OPENCLAW_DIR}/openviking.env" <<EOF
export OPENVIKING_PYTHON='${py_path}'
EOF
  info "$(tr "Environment file generated: ${OPENCLAW_DIR}/openviking.env" "已生成环境文件: ${OPENCLAW_DIR}/openviking.env")"
}

# ---- Main flow ----

write_openviking_env() {
  local include_python="${1:-1}"
  local py_path=""
  local lines=()

  if [[ "$OPENCLAW_DIR" != "$DEFAULT_OPENCLAW_DIR" ]]; then
    lines+=("export OPENCLAW_STATE_DIR='$(shell_single_quote "${OPENCLAW_DIR}")'")
  fi

  if [[ "$include_python" == "1" ]]; then
    if [[ -n "${OPENVIKING_PYTHON_PATH}" ]]; then
      py_path="${OPENVIKING_PYTHON_PATH}"
    else
      py_path="$(command -v python3 || command -v python || true)"
    fi
    if [[ -z "$py_path" ]]; then
      py_path="${OPENVIKING_PYTHON:-python3}"
      warn "Could not resolve Python path; using OPENVIKING_PYTHON or python3 in openviking.env. Edit the file if startup fails."
    fi

    # Verify the resolved Python can actually import openviking
    if ! "$py_path" -c "import openviking" 2>/dev/null; then
      warn "Resolved Python (${py_path}) cannot import openviking. Searching for the correct Python..."
      local corrected=""
      for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
        local cpath
        cpath="$(command -v "$candidate" 2>/dev/null || true)"
        [[ -z "$cpath" || "$cpath" == "$py_path" ]] && continue
        if "$cpath" -c "import openviking" 2>/dev/null; then
          corrected="$cpath"
          break
        fi
      done
      if [[ -n "$corrected" ]]; then
        info "Auto-corrected OPENVIKING_PYTHON to ${corrected}"
        py_path="$corrected"
      else
        warn "Could not auto-detect the correct Python. Edit OPENVIKING_PYTHON in the env file manually."
      fi
    fi

    lines+=("export OPENVIKING_PYTHON='$(shell_single_quote "${py_path}")'")
  fi

  if [[ ${#lines[@]} -eq 0 ]]; then
    return 0
  fi

  mkdir -p "${OPENCLAW_DIR}"
  printf "%s\n" "${lines[@]}" > "${OPENCLAW_DIR}/openviking.env"
  info "$(tr "Environment file generated: ${OPENCLAW_DIR}/openviking.env" "已生成环境文件: ${OPENCLAW_DIR}/openviking.env")"
}

wrap_command() {
  local cmd="$1"
  local env_file="${OPENCLAW_DIR}/openviking.env"
  if [[ -f "${env_file}" ]]; then
    printf "source '%s' && %s" "$(shell_single_quote "${env_file}")" "${cmd}"
  else
    printf "%s" "${cmd}"
  fi
}

main() {
  echo ""
  bold "OpenClaw + OpenViking Installer"
  echo ""

  detect_os
  ensure_plugin_only_operation_args
  select_workdir
  if [[ "$ROLLBACK_LAST_UPGRADE" == "1" ]]; then
    info "Target: ${OPENCLAW_DIR}"
    info "Repository: ${REPO}"
    info "Mode: rollback last plugin upgrade"
    if [[ "$PLUGIN_VERSION_EXPLICIT" == "1" ]]; then
      warn "--plugin-version is ignored in --rollback mode."
    fi
    rollback_last_upgrade_operation
    return 0
  fi
  resolve_default_plugin_version
  validate_requested_plugin_version
  info "Target: ${OPENCLAW_DIR}"
  info "Repository: ${REPO}"
  info "Plugin version: ${PLUGIN_VERSION}"
  [[ -n "$OPENVIKING_VERSION" ]] && info "OpenViking version: ${OPENVIKING_VERSION}"

  if [[ "$UPGRADE_PLUGIN_ONLY" == "1" ]]; then
    SELECTED_MODE="local"
    info "Mode: plugin upgrade only (backup old plugin, clean only OpenViking plugin config, keep ov.conf)"
  else
    select_mode
  fi
  info "Mode: ${SELECTED_MODE}"

  if [[ "$UPGRADE_PLUGIN_ONLY" == "1" ]]; then
    install_openclaw
    resolve_plugin_config
    check_openclaw_compatibility
    prepare_strong_plugin_upgrade
  elif [[ "$SELECTED_MODE" == "local" ]]; then
    validate_environment
    install_openclaw
    # Resolve plugin config after OpenClaw is available (for version detection)
    resolve_plugin_config
    check_openclaw_compatibility
    check_requested_openviking_compatibility
    install_openviking
    configure_openviking_conf
  else
    install_openclaw
    resolve_plugin_config
    check_openclaw_compatibility
    collect_remote_config
  fi

  deploy_plugin_from_remote
  local install_state_operation="install"
  if [[ "$UPGRADE_PLUGIN_ONLY" == "1" ]]; then
    configure_openclaw_plugin "0" "1" "${UPGRADE_CLAIM_SLOT}"
    install_state_operation="upgrade"
  else
    configure_openclaw_plugin "0" "0" "1"
  fi

  write_install_state_file "${install_state_operation}" "${UPGRADE_AUDIT_FROM_VERSION}"
  if [[ "$UPGRADE_PLUGIN_ONLY" == "1" ]]; then
    UPGRADE_AUDIT_COMPLETED_AT="$(iso_now)"
    write_upgrade_audit_file
  fi

  if [[ "$UPGRADE_PLUGIN_ONLY" == "1" ]]; then
    if [[ "$OPENCLAW_DIR" != "$DEFAULT_OPENCLAW_DIR" && ! -f "${OPENCLAW_DIR}/openviking.env" ]]; then
      write_openviking_env 0
    fi
  elif [[ "$SELECTED_MODE" == "local" ]]; then
    write_openviking_env 1
  elif [[ "$OPENCLAW_DIR" != "$DEFAULT_OPENCLAW_DIR" ]]; then
    write_openviking_env 0
  fi

  echo ""
  bold "============================================================"
  bold "  Installation complete!"
  bold "============================================================"
  echo ""
  if [[ "$UPGRADE_PLUGIN_ONLY" == "1" ]]; then
    info "Upgrade path recorded: ${UPGRADE_AUDIT_FROM_VERSION} -> ${UPGRADE_AUDIT_TO_VERSION}"
    info "Rollback config backup: ${UPGRADE_AUDIT_CONFIG_BACKUP_PATH}"
    local backup_item
    for backup_item in "${UPGRADE_AUDIT_PLUGIN_BACKUPS[@]}"; do
      info "Rollback plugin backup: ${backup_item#*|}"
    done
    info "Rollback audit file: $(get_upgrade_audit_path)"
    echo ""
  fi
  if [[ "$SELECTED_MODE" == "local" ]]; then
    info "Run these commands to start OpenClaw + OpenViking:"
    echo "  1) $(wrap_command "openclaw --version")"
    echo "  2) $(wrap_command "openclaw onboard")"
    echo "  3) $(wrap_command "openclaw gateway")"
    echo "  4) $(wrap_command "openclaw status")"
    echo ""
    info "If source fails (e.g. file missing), run: export OPENVIKING_PYTHON=\"\$(command -v python3)\""
    info "You can edit the config freely: ${OPENVIKING_DIR}/ov.conf"
  else
    info "Run these commands to start OpenClaw:"
    echo "  1) $(wrap_command "openclaw --version")"
    echo "  2) $(wrap_command "openclaw onboard")"
    echo "  3) $(wrap_command "openclaw gateway")"
    echo "  4) $(wrap_command "openclaw status")"
    echo ""
    info "Remote server: ${remote_base_url}"
  fi
  echo ""
}

main "$@"
