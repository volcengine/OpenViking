#!/usr/bin/env bash
set -euo pipefail

# Restart OpenViking and the ALFWorld rollout service, wait until both are
# healthy, then start ALFWorld batch train/eval through the generic remote
# benchmark pipeline.
#
# Default train/eval args are intentionally conservative for ALFWorld:
#   --commit-concurrency 32 --epochs 1 --trials 1 --train-trials 1 --skip-final-eval
# Pass any non-launcher arguments to override/extend the batch invocation.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALFWORLD_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ALFWORLD_DIR}/../.." && pwd)"

SLOT="0"
declare -a TRAIN_CLI_ARGS=()

usage() {
  cat <<'USAGE'
Usage:
  bash benchmark/alfworld/train/restart_alfworld_train_eval.sh [--slot N] [train/eval args...]

Launcher options:
  --slot N  Isolated experiment slot. Slot 0 is default. Slot N>0 uses:
            OV port        = 1933 + N
            OV bot port    = 18790 + N
            ALFWorld port  = 1954 + N
            OV config      = ~/.openviking_N/ov.conf
            OV data        = ~/.openviking_N/data
            result dir     = result/alfworld/train_N

All remaining args are passed to benchmark/alfworld/train/run_batch_train_eval.sh.

Common examples:
  bash benchmark/alfworld/train/restart_alfworld_train_eval.sh --epochs 1 --eval-split test
  bash benchmark/alfworld/train/restart_alfworld_train_eval.sh --slot 1 --train-index 0 --eval-index 0
USAGE
}

parse_launcher_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --slot)
        if [[ $# -lt 2 ]]; then
          echo "[restart-alfworld-train] ERROR: --slot requires a value" >&2
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
    echo "[restart-alfworld-train] ERROR: --slot must be a non-negative integer, got: ${SLOT}" >&2
    exit 1
  fi
}

load_user_env_file() {
  local env_file="${OPENVIKING_ENV_FILE:-${HOME}/.openviking_benchmark_env}"
  if [[ -z "${env_file}" || ! -f "${env_file}" ]]; then
    return 0
  fi

  printf '[restart-alfworld-train] loading env file %s\n' "${env_file}"
  set +u
  set -a
  # shellcheck source=/dev/null
  source "${env_file}"
  set +a
  set -euo pipefail
}

parse_launcher_args "$@"
validate_slot
load_user_env_file

if [[ "${SLOT}" == "0" ]]; then
  DEFAULT_OPENVIKING_PORT="1933"
  DEFAULT_OPENVIKING_BOT_PORT="18790"
  DEFAULT_ALFWORLD_SERVICE_PORT="1954"
  DEFAULT_RESULT_DIR_NAME="train"
  DEFAULT_LOG_DIR="${REPO_ROOT}/result/alfworld/train/service_logs"
  DEFAULT_OPENVIKING_CONFIG_FILE="${HOME}/.openviking/ov.conf"
  DEFAULT_OPENVIKING_DATA_DIR="${HOME}/.openviking/data"
  DEFAULT_SLOT_ROOT="${HOME}/.openviking"
else
  DEFAULT_OPENVIKING_PORT="$((1933 + SLOT))"
  DEFAULT_OPENVIKING_BOT_PORT="$((18790 + SLOT))"
  DEFAULT_ALFWORLD_SERVICE_PORT="$((1954 + SLOT))"
  DEFAULT_RESULT_DIR_NAME="train_${SLOT}"
  DEFAULT_LOG_DIR="${REPO_ROOT}/result/alfworld/${DEFAULT_RESULT_DIR_NAME}/service_logs"
  DEFAULT_SLOT_ROOT="${HOME}/.openviking_${SLOT}"
  DEFAULT_OPENVIKING_CONFIG_FILE="${DEFAULT_SLOT_ROOT}/ov.conf"
  DEFAULT_OPENVIKING_DATA_DIR="${DEFAULT_SLOT_ROOT}/data"
fi

# Keep train slots hermetic: inherited environment variables must not reroute
# --slot 1 to the default 1933/~/.openviking service.
OPENVIKING_PORT="${DEFAULT_OPENVIKING_PORT}"
OPENVIKING_BOT_PORT="${DEFAULT_OPENVIKING_BOT_PORT}"
ALFWORLD_SERVICE_HOST="127.0.0.1"
ALFWORLD_SERVICE_PORT="${DEFAULT_ALFWORLD_SERVICE_PORT}"
ALFWORLD_MAX_ROLLOUT_CONCURRENCY="32"
ALFWORLD_ROLLOUT_THREAD_WORKERS="${ALFWORLD_MAX_ROLLOUT_CONCURRENCY}"
ALFWORLD_NATIVE_THREAD_WORKERS="32"
ALFWORLD_ROLLOUT_BACKEND="${ALFWORLD_ROLLOUT_BACKEND:-vikingbot}"
ALFWORLD_EXPERIENCE_LOADER_MODE="${ALFWORLD_EXPERIENCE_LOADER_MODE:-skill}"
WAIT_TIMEOUT_SECONDS="180"
RESULT_DIR_NAME="${DEFAULT_RESULT_DIR_NAME}"
LOG_DIR="${DEFAULT_LOG_DIR}"
OPENVIKING_CONFIG_FILE="${DEFAULT_OPENVIKING_CONFIG_FILE}"
OPENVIKING_DATA_DIR="${DEFAULT_OPENVIKING_DATA_DIR}"
SLOT_ROOT="${DEFAULT_SLOT_ROOT}"
ALFWORLD_REPO="${ALFWORLD_REPO:-${HOME}/workspace/alfworld}"
if [[ -z "${ALFWORLD_DATA:-}" && -d "${ALFWORLD_REPO}/data" ]]; then
  export ALFWORLD_DATA="${ALFWORLD_REPO}/data"
fi

OPENVIKING_LOG="${LOG_DIR}/openviking-server.log"
ALFWORLD_SERVICE_LOG="${LOG_DIR}/alfworld-service.log"

mkdir -p "${LOG_DIR}"

log() {
  printf '[restart-alfworld-train] %s\n' "$*"
}

fail() {
  printf '[restart-alfworld-train] ERROR: %s\n' "$*" >&2
  exit 1
}

stop_existing_listener() {
  local name="$1"
  local port="$2"
  local pids
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    log "no existing ${name} listener on port ${port}"
    return 0
  fi

  log "stopping existing ${name} listener(s) on port ${port}: ${pids}"
  kill ${pids} 2>/dev/null || true
  for _ in {1..20}; do
    sleep 0.2
    if ! lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      log "✓ stopped existing ${name} listener(s) on port ${port}"
      return 0
    fi
  done

  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    log "force stopping existing ${name} listener(s) on port ${port}: ${pids}"
    kill -9 ${pids} 2>/dev/null || true
  fi
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


validate_alfworld_data() {
  if [[ "${ALFWORLD_SKIP_DATA_CHECK:-0}" == "1" || "${ALFWORLD_ALLOW_PSEUDO_CASES:-0}" == "1" ]]; then
    log "skipping ALFWorld data preflight because ALFWORLD_SKIP_DATA_CHECK/ALFWORLD_ALLOW_PSEUDO_CASES is set"
    return 0
  fi

  if [[ -z "${ALFWORLD_DATA:-}" ]]; then
    fail "ALFWORLD_DATA is unset. Download data first, e.g. python ~/workspace/alfworld/scripts/alfworld-download --data-dir ~/workspace/alfworld/data"
  fi
  if [[ ! -d "${ALFWORLD_DATA}" ]]; then
    fail "ALFWORLD_DATA does not exist: ${ALFWORLD_DATA}"
  fi
  local sample_game
  sample_game="$(find "${ALFWORLD_DATA}" -name 'game.tw-pddl' -print -quit 2>/dev/null || true)"
  if [[ -z "${sample_game}" ]]; then
    log "ALFWORLD_DATA contains no game.tw-pddl files: ${ALFWORLD_DATA}"
    log "current disk space:"
    df -h "${ALFWORLD_DATA}" 2>/dev/null || df -h "${REPO_ROOT}" || true
    fail "ALFWorld data is not downloaded/extracted. Free disk space or choose a larger ALFWORLD_DATA, then run: python ~/workspace/alfworld/scripts/alfworld-download --data-dir ${ALFWORLD_DATA}"
  fi
  log "✓ ALFWorld data preflight found game: ${sample_game}"
}

start_openviking_server() {
  prepare_slot_config
  log "slot=${SLOT} result_dir=result/alfworld/${RESULT_DIR_NAME}"
  log "slot root: ${SLOT_ROOT}"
  log "OpenViking config: ${OPENVIKING_CONFIG_FILE}"
  log "OpenViking data: ${OPENVIKING_DATA_DIR}"
  log "restarting OpenViking server on port ${OPENVIKING_PORT}, bot port ${OPENVIKING_BOT_PORT}"
  log "OpenViking log: ${OPENVIKING_LOG}"
  : > "${OPENVIKING_LOG}"
  stop_existing_listener "OpenViking server" "${OPENVIKING_PORT}"
  stop_existing_listener "OpenViking bot" "${OPENVIKING_BOT_PORT}"

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

start_alfworld_service() {
  log "restarting ALFWorld service on ${ALFWORLD_SERVICE_HOST}:${ALFWORLD_SERVICE_PORT}"
  log "ALFWorld service backend=${ALFWORLD_ROLLOUT_BACKEND} loader_mode=${ALFWORLD_EXPERIENCE_LOADER_MODE} concurrency=${ALFWORLD_MAX_ROLLOUT_CONCURRENCY} rollout_thread_workers=${ALFWORLD_ROLLOUT_THREAD_WORKERS}"
  log "ALFWorld data root: ${ALFWORLD_DATA:-<unset>}"
  log "ALFWorld service log: ${ALFWORLD_SERVICE_LOG}"
  : > "${ALFWORLD_SERVICE_LOG}"
  stop_existing_listener "ALFWorld rollout service" "${ALFWORLD_SERVICE_PORT}"

  if [[ -z "${ALFWORLD_DATA:-}" ]]; then
    log "WARNING: ALFWORLD_DATA is unset. The case loader may expose pseudo env-slot cases, but actual rollouts usually need ALFWorld data."
  fi

  (
    cd "${REPO_ROOT}"
    export OPENVIKING_CONFIG_FILE
    exec benchmark/alfworld/train/run_service.sh \
      --host "${ALFWORLD_SERVICE_HOST}" \
      --port "${ALFWORLD_SERVICE_PORT}" \
      --data-root "${ALFWORLD_DATA:-}" \
      --config "${OPENVIKING_CONFIG_FILE}" \
      --rollout-backend "${ALFWORLD_ROLLOUT_BACKEND}" \
      --loader-mode "${ALFWORLD_EXPERIENCE_LOADER_MODE}" \
      --native-thread-workers "${ALFWORLD_NATIVE_THREAD_WORKERS}" \
      --max-rollout-concurrency "${ALFWORLD_MAX_ROLLOUT_CONCURRENCY}" \
      --rollout-thread-workers "${ALFWORLD_ROLLOUT_THREAD_WORKERS}"
  ) >"${ALFWORLD_SERVICE_LOG}" 2>&1 &

  echo "$!" > "${LOG_DIR}/alfworld-service.pid"
  log "ALFWorld service pid: $(cat "${LOG_DIR}/alfworld-service.pid")"

  wait_for_http_json_ok \
    "ALFWorld rollout service" \
    "http://${ALFWORLD_SERVICE_HOST}:${ALFWORLD_SERVICE_PORT}/health" \
    '"status":"ok"' \
    "${ALFWORLD_SERVICE_LOG}"
}

run_train_eval() {
  local -a train_args=("$@")
  if [[ ${#train_args[@]} -eq 0 ]]; then
    train_args=(
      --commit-concurrency 32
      --epochs 1
      --trials 1
      --train-trials 1
      --skip-final-eval
    )
  fi

  export OPENVIKING_CONFIG_FILE
  local benchmark_service_url="http://${ALFWORLD_SERVICE_HOST}:${ALFWORLD_SERVICE_PORT}"
  log "starting batch train/eval with benchmark service ${benchmark_service_url}"
  log "command: benchmark/alfworld/train/run_batch_train_eval.sh --benchmark-service-url ${benchmark_service_url} --config ${OPENVIKING_CONFIG_FILE} --server-url http://127.0.0.1:${OPENVIKING_PORT} --result-dir-name ${RESULT_DIR_NAME} ${train_args[*]}"
  cd "${REPO_ROOT}"
  exec benchmark/alfworld/train/run_batch_train_eval.sh \
    --benchmark-service-url "${benchmark_service_url}" \
    --config "${OPENVIKING_CONFIG_FILE}" \
    --server-url "http://127.0.0.1:${OPENVIKING_PORT}" \
    --result-dir-name "${RESULT_DIR_NAME}" \
    "${train_args[@]}"
}

main() {
  validate_alfworld_data
  start_openviking_server
  start_alfworld_service
  run_train_eval "${TRAIN_CLI_ARGS[@]}"
}

main
