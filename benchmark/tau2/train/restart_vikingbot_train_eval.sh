#!/usr/bin/env bash
set -euo pipefail

# Restart the OpenViking bot server and tau2 rollout service, wait until both
# are healthy, then start tau2 vikingbot batch train/eval.
#
# Default training args match the common vikingbot run:
#   --commit-concurrency 100 --epochs 2 --trials 8 --skip-final-eval
# Pass any non-launcher arguments to override/extend the batch train/eval invocation.
#
# Launcher-only options:
#   --slot N   Run an isolated slot. Slot 0 is the default legacy setup. Slot N>0
#              uses separate ports, OpenViking config/data, logs, and result dir.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"

SLOT="${TAU2_TRAIN_SLOT:-0}"
declare -a TRAIN_CLI_ARGS=()

usage() {
  cat <<'USAGE'
Usage:
  bash benchmark/tau2/train/restart_vikingbot_train_eval.sh [--slot N] [train/eval args...]

Launcher options:
  --slot N  Isolated experiment slot. Slot 0 is default/legacy. Slot N>0 uses:
            OV port     = 1933 + N
            OV bot port = 18790 + N
            tau2 port   = 1944 + N
            OV config   = ~/.openviking_N/ov.conf
            OV data     = ~/.openviking_N/data
            result dir  = result/tau2/train_N

All remaining args are passed to benchmark/tau2/train/run_batch_train_eval.sh.
USAGE
}

parse_launcher_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --slot)
        if [[ $# -lt 2 ]]; then
          echo "[restart-vikingbot-train] ERROR: --slot requires a value" >&2
          exit 1
        fi
        SLOT="$2"
        shift 2
        ;;
      --slot=*)
        SLOT="${1#--slot=}"
        shift 1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        shift
        TRAIN_CLI_ARGS+=("$@")
        break
        ;;
      *)
        TRAIN_CLI_ARGS+=("$1")
        shift 1
        ;;
    esac
  done
}

validate_slot() {
  if ! [[ "${SLOT}" =~ ^[0-9]+$ ]]; then
    echo "[restart-vikingbot-train] ERROR: --slot must be a non-negative integer, got: ${SLOT}" >&2
    exit 1
  fi
}

parse_launcher_args "$@"
validate_slot

if [[ "${SLOT}" == "0" ]]; then
  DEFAULT_OPENVIKING_PORT="1933"
  DEFAULT_OPENVIKING_BOT_PORT="18790"
  DEFAULT_TAU2_SERVICE_PORT="1944"
  DEFAULT_RESULT_DIR_NAME="train"
  DEFAULT_LOG_DIR="${REPO_ROOT}/result/tau2/train/service_logs"
  DEFAULT_OPENVIKING_CONFIG_FILE="${HOME}/.openviking/ov.conf"
  DEFAULT_OPENVIKING_DATA_DIR="${HOME}/.openviking/data"
  DEFAULT_SLOT_ROOT="${HOME}/.openviking"
else
  DEFAULT_OPENVIKING_PORT="$((1933 + SLOT))"
  DEFAULT_OPENVIKING_BOT_PORT="$((18790 + SLOT))"
  DEFAULT_TAU2_SERVICE_PORT="$((1944 + SLOT))"
  DEFAULT_RESULT_DIR_NAME="train_${SLOT}"
  DEFAULT_LOG_DIR="${REPO_ROOT}/result/tau2/${DEFAULT_RESULT_DIR_NAME}/service_logs"
  DEFAULT_SLOT_ROOT="${HOME}/.openviking_${SLOT}"
  DEFAULT_OPENVIKING_CONFIG_FILE="${DEFAULT_SLOT_ROOT}/ov.conf"
  DEFAULT_OPENVIKING_DATA_DIR="${DEFAULT_SLOT_ROOT}/data"
fi

OPENVIKING_PORT="${OPENVIKING_PORT:-${DEFAULT_OPENVIKING_PORT}}"
OPENVIKING_BOT_PORT="${OPENVIKING_BOT_PORT:-${DEFAULT_OPENVIKING_BOT_PORT}}"
TAU2_SERVICE_HOST="${TAU2_SERVICE_HOST:-127.0.0.1}"
TAU2_SERVICE_PORT="${TAU2_SERVICE_PORT:-${DEFAULT_TAU2_SERVICE_PORT}}"
TAU2_ROLLOUT_BACKEND="${TAU2_ROLLOUT_BACKEND:-vikingbot}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-180}"
RESULT_DIR_NAME="${RESULT_DIR_NAME:-${DEFAULT_RESULT_DIR_NAME}}"
LOG_DIR="${LOG_DIR:-${DEFAULT_LOG_DIR}}"
OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${DEFAULT_OPENVIKING_CONFIG_FILE}}"
OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-${DEFAULT_OPENVIKING_DATA_DIR}}"
SLOT_ROOT="${SLOT_ROOT:-${DEFAULT_SLOT_ROOT}}"

OPENVIKING_LOG="${LOG_DIR}/openviking-server.log"
TAU2_SERVICE_LOG="${LOG_DIR}/tau2-service.log"

mkdir -p "${LOG_DIR}"

log() {
  printf '[restart-vikingbot-train] %s\n' "$*"
}

fail() {
  printf '[restart-vikingbot-train] ERROR: %s\n' "$*" >&2
  exit 1
}

json_string_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "${value}"
}

prepare_slot_config() {
  if [[ "${SLOT}" == "0" && "${OPENVIKING_CONFIG_FILE}" == "${HOME}/.openviking/ov.conf" ]]; then
    return 0
  fi

  local escaped_workspace
  local config_dir
  escaped_workspace="$(json_string_escape "${OPENVIKING_DATA_DIR}")"
  config_dir="$(dirname "${OPENVIKING_CONFIG_FILE}")"
  mkdir -p "${config_dir}" "${OPENVIKING_DATA_DIR}"

  if [[ -d "${HOME}/.openviking" && "${config_dir}" != "${HOME}/.openviking" ]]; then
    local config_name
    for config_name in ov.conf ovcli.conf ovcli.settings.conf; do
      if [[ -f "${HOME}/.openviking/${config_name}" ]]; then
        cp -f "${HOME}/.openviking/${config_name}" "${config_dir}/${config_name}"
      fi
    done
  fi

  if [[ ! -f "${OPENVIKING_CONFIG_FILE}" ]]; then
    cat > "${OPENVIKING_CONFIG_FILE}" <<EOF_CONFIG
{
  "storage": {
    "workspace": "${escaped_workspace}"
  }
}
EOF_CONFIG
  fi

  python - \
    "${OPENVIKING_CONFIG_FILE}" \
    "${OPENVIKING_DATA_DIR}" \
    "${OPENVIKING_PORT}" \
    "${OPENVIKING_BOT_PORT}" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
workspace = str(Path(sys.argv[2]).expanduser())
openviking_port = int(sys.argv[3])
openviking_bot_port = int(sys.argv[4])
openviking_url = f"http://127.0.0.1:{openviking_port}"
openviking_bot_url = f"http://127.0.0.1:{openviking_bot_port}"
try:
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
except FileNotFoundError:
    data = {}
if not isinstance(data, dict):
    raise SystemExit(f"config root must be a JSON object: {config_path}")
storage = data.get("storage")
if not isinstance(storage, dict):
    storage = {}
data["storage"] = storage
storage["workspace"] = workspace
server = data.get("server")
if not isinstance(server, dict):
    server = {}
data["server"] = server
server["host"] = "127.0.0.1"
server["port"] = openviking_port
server["bot_api_url"] = openviking_bot_url
bot = data.get("bot")
if not isinstance(bot, dict):
    bot = {}
data["bot"] = bot
ov_server = bot.get("ov_server")
if not isinstance(ov_server, dict):
    ov_server = {}
bot["ov_server"] = ov_server
ov_server["server_url"] = openviking_url
ov_server.setdefault("case_recall_limit", 1)
ov_server.setdefault("trajectory_recall_limit", 2)
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

wait_for_http_json_ok() {
  local name="$1"
  local url="$2"
  local required_pattern="$3"
  local log_file="$4"
  local deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
  local response=""

  log "waiting for ${name}: ${url}"
  while (( SECONDS < deadline )); do
    response="$(curl -fsS "${url}" 2>/dev/null || true)"
    if [[ -n "${response}" && "${response//[[:space:]]/}" == *"${required_pattern}"* ]]; then
      log "✓ ${name} is ready"
      return 0
    fi
    sleep 2
  done

  log "last ${name} response: ${response:-<empty>}"
  if [[ -f "${log_file}" ]]; then
    log "recent ${name} logs:"
    tail -80 "${log_file}" >&2 || true
  fi
  fail "${name} did not become ready within ${WAIT_TIMEOUT_SECONDS}s"
}

start_openviking_server() {
  prepare_slot_config
  log "slot=${SLOT} result_dir=result/tau2/${RESULT_DIR_NAME}"
  log "slot root: ${SLOT_ROOT}"
  log "OpenViking config: ${OPENVIKING_CONFIG_FILE}"
  log "OpenViking data: ${OPENVIKING_DATA_DIR}"
  log "restarting OpenViking server on port ${OPENVIKING_PORT}, bot port ${OPENVIKING_BOT_PORT}"
  log "OpenViking log: ${OPENVIKING_LOG}"
  : > "${OPENVIKING_LOG}"

  (
    cd "${REPO_ROOT}"
    export OPENVIKING_CONFIG_FILE
    exec bot/scripts/restart_openviking_server.sh \
      --port "${OPENVIKING_PORT}" \
      --bot-port "${OPENVIKING_BOT_PORT}" \
      --config "${OPENVIKING_CONFIG_FILE}" \
      --data-dir "${OPENVIKING_DATA_DIR}" \
      --no-kill-all-vikingbot
  ) >"${OPENVIKING_LOG}" 2>&1 &

  echo "$!" > "${LOG_DIR}/openviking-server.pid"
  log "OpenViking restart wrapper pid: $(cat "${LOG_DIR}/openviking-server.pid")"

  wait_for_http_json_ok \
    "OpenViking bot API" \
    "http://127.0.0.1:${OPENVIKING_PORT}/bot/v1/health" \
    '"status":"healthy"' \
    "${OPENVIKING_LOG}"
}

start_tau2_service() {
  log "restarting tau2 service on ${TAU2_SERVICE_HOST}:${TAU2_SERVICE_PORT} backend=${TAU2_ROLLOUT_BACKEND}"
  log "tau2 service log: ${TAU2_SERVICE_LOG}"
  : > "${TAU2_SERVICE_LOG}"

  (
    cd "${REPO_ROOT}"
    export OPENVIKING_CONFIG_FILE
    exec benchmark/tau2/train/run_service.sh \
      --host "${TAU2_SERVICE_HOST}" \
      --port "${TAU2_SERVICE_PORT}" \
      --config "${OPENVIKING_CONFIG_FILE}" \
      --rollout-backend "${TAU2_ROLLOUT_BACKEND}"
  ) >"${TAU2_SERVICE_LOG}" 2>&1 &

  echo "$!" > "${LOG_DIR}/tau2-service.pid"
  log "tau2 service pid: $(cat "${LOG_DIR}/tau2-service.pid")"

  wait_for_http_json_ok \
    "tau2 rollout service" \
    "http://${TAU2_SERVICE_HOST}:${TAU2_SERVICE_PORT}/health" \
    '"status":"ok"' \
    "${TAU2_SERVICE_LOG}"
}

run_train_eval() {
  local -a train_args=("$@")
  if [[ ${#train_args[@]} -eq 0 ]]; then
    train_args=(
      --commit-concurrency 100
      --epochs 2
      --trials 8
      --skip-final-eval
    )
  fi

  export OPENVIKING_CONFIG_FILE
  export BENCHMARK_SERVICE_URL="http://${TAU2_SERVICE_HOST}:${TAU2_SERVICE_PORT}"
  log "starting batch train/eval with BENCHMARK_SERVICE_URL=${BENCHMARK_SERVICE_URL}"
  log "command: benchmark/tau2/train/run_batch_train_eval.sh --config ${OPENVIKING_CONFIG_FILE} --server-url http://127.0.0.1:${OPENVIKING_PORT} --result-dir-name ${RESULT_DIR_NAME} ${train_args[*]}"
  cd "${REPO_ROOT}"
  exec benchmark/tau2/train/run_batch_train_eval.sh \
    --config "${OPENVIKING_CONFIG_FILE}" \
    --server-url "http://127.0.0.1:${OPENVIKING_PORT}" \
    --result-dir-name "${RESULT_DIR_NAME}" \
    "${train_args[@]}"
}

main() {
  start_openviking_server
  start_tau2_service
  run_train_eval "${TRAIN_CLI_ARGS[@]}"
}

main
