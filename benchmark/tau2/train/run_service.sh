#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"

load_user_env_file() {
  local env_file="${OPENVIKING_ENV_FILE:-${HOME}/.openviking_benchmark_env}"
  if [[ -z "${env_file}" || ! -f "${env_file}" ]]; then
    return
  fi

  local -a preserved_env=()
  local entry
  while IFS= read -r -d '' entry; do
    if [[ "${entry}" != *= ]]; then
      preserved_env+=("${entry}")
    fi
  done < <(env -0)

  echo "[tau2-service] loading env file ${env_file}"
  set +u
  set -a
  # shellcheck source=/dev/null
  source "${env_file}"
  set +a
  set -euo pipefail

  for entry in "${preserved_env[@]}"; do
    export "${entry}"
  done
}

load_user_env_file

PYTHON_BIN="${PYTHON_BIN:-python}"
HOST="127.0.0.1"
PORT="1944"
DATA_ROOT="${TAU2_DATA_ROOT:-}"
CONFIG="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"
KILL_EXISTING=1
ROLLOUT_LANGUAGE="default"
ROLLOUT_BACKEND="${TAU2_ROLLOUT_BACKEND:-vikingbot}"
NATIVE_THREAD_WORKERS="${TAU2_NATIVE_THREAD_WORKERS:-128}"
MAX_ROLLOUT_CONCURRENCY="${TAU2_MAX_ROLLOUT_CONCURRENCY:-200}"
ROLLOUT_THREAD_WORKERS="${TAU2_ROLLOUT_THREAD_WORKERS:-200}"
REPAIR_VIKINGBOT_GYM="${TAU2_REPAIR_VIKINGBOT_GYM:-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --data-root) DATA_ROOT="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --rollout-language) ROLLOUT_LANGUAGE="$2"; shift 2 ;;
    --rollout-backend) ROLLOUT_BACKEND="$2"; shift 2 ;;
    --native-thread-workers) NATIVE_THREAD_WORKERS="$2"; shift 2 ;;
    --max-rollout-concurrency) MAX_ROLLOUT_CONCURRENCY="$2"; shift 2 ;;
    --rollout-thread-workers) ROLLOUT_THREAD_WORKERS="$2"; shift 2 ;;
    --repair-vikingbot-gym) REPAIR_VIKINGBOT_GYM=1; shift 1 ;;
    --no-repair-vikingbot-gym) REPAIR_VIKINGBOT_GYM=0; shift 1 ;;
    --no-kill-existing) KILL_EXISTING=0; shift 1 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  bash benchmark/tau2/train/run_service.sh [--host 127.0.0.1] [--port 1944]

Options:
  --data-root PATH   tau2-bench data/tau2 root. Default auto-detect/TAU2_DATA_ROOT
  --config PATH      ov.conf for VikingBot/OpenViking access. Default ~/.openviking/ov.conf
  --rollout-language default|zh
                     Rollout response language. Use zh for Chinese user-facing replies.
  --rollout-backend native|vikingbot
                     Rollout implementation backend. Default: vikingbot.
  --native-thread-workers N
                     Default thread pool workers for native rollout. Default: 128.
  --max-rollout-concurrency N
                     Maximum concurrent rollout executions hosted by the service.
                     Default: 200.
  --rollout-thread-workers N
                     Worker threads used to host rollouts off the uvicorn event loop.
                     Default: 200. Use 0 to disable threaded hosting.
  --repair-vikingbot-gym
                     If --rollout-backend=vikingbot and tau2.gym/gymnasium is missing,
                     install tau2-bench[gym] into the current Python environment.
                     Default: enabled. Set TAU2_REPAIR_VIKINGBOT_GYM=0 or pass
                     --no-repair-vikingbot-gym to disable automatic repair.
  --no-kill-existing Do not stop existing process listening on --port
EOF
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "${ROLLOUT_LANGUAGE}" != "default" && "${ROLLOUT_LANGUAGE}" != "zh" ]]; then
  echo "[tau2-service] invalid --rollout-language: ${ROLLOUT_LANGUAGE}. Expected default or zh" >&2
  exit 1
fi

if [[ "${ROLLOUT_BACKEND}" != "native" && "${ROLLOUT_BACKEND}" != "vikingbot" ]]; then
  echo "[tau2-service] invalid --rollout-backend: ${ROLLOUT_BACKEND}. Expected native or vikingbot" >&2
  exit 1
fi

if ! [[ "${NATIVE_THREAD_WORKERS}" =~ ^[0-9]+$ ]] || [[ "${NATIVE_THREAD_WORKERS}" -le 0 ]]; then
  echo "[tau2-service] invalid --native-thread-workers: ${NATIVE_THREAD_WORKERS}. Expected positive integer" >&2
  exit 1
fi

if ! [[ "${MAX_ROLLOUT_CONCURRENCY}" =~ ^[0-9]+$ ]] || [[ "${MAX_ROLLOUT_CONCURRENCY}" -le 0 ]]; then
  echo "[tau2-service] invalid --max-rollout-concurrency: ${MAX_ROLLOUT_CONCURRENCY}. Expected positive integer" >&2
  exit 1
fi

if ! [[ "${ROLLOUT_THREAD_WORKERS}" =~ ^[0-9]+$ ]]; then
  echo "[tau2-service] invalid --rollout-thread-workers: ${ROLLOUT_THREAD_WORKERS}. Expected non-negative integer" >&2
  exit 1
fi

if [[ "${REPAIR_VIKINGBOT_GYM}" != "0" && "${REPAIR_VIKINGBOT_GYM}" != "1" ]]; then
  echo "[tau2-service] invalid TAU2_REPAIR_VIKINGBOT_GYM: ${REPAIR_VIKINGBOT_GYM}. Expected 0 or 1" >&2
  exit 1
fi

if [[ -z "${DATA_ROOT}" ]]; then
  for _candidate in \
    "${REPO_ROOT}/tau2-bench/data/tau2" \
    "${REPO_ROOT}/../tau2-bench/data/tau2" \
    "${HOME}/workspace/tau2-bench/data/tau2"; do
    if [[ -d "${_candidate}/domains" ]]; then
      DATA_ROOT="${_candidate}"
      break
    fi
  done
fi
if [[ -z "${DATA_ROOT}" || ! -d "${DATA_ROOT}/domains" ]]; then
  echo "[tau2-service] tau2 data root not found. Pass --data-root <tau2-bench>/data/tau2" >&2
  exit 1
fi

TAU2_BENCH_ROOT="${TAU2_BENCH_ROOT:-}"
if [[ -z "${TAU2_BENCH_ROOT}" ]]; then
  _maybe_root="$(cd "${DATA_ROOT}/../.." && pwd)"
  if [[ -d "${_maybe_root}/src/tau2" ]]; then
    TAU2_BENCH_ROOT="${_maybe_root}"
  fi
fi
VIKINGBOT_ROOT="${VIKINGBOT_ROOT:-${REPO_ROOT}/bot}"
export PYTHONPATH="${REPO_ROOT}:${VIKINGBOT_ROOT}:${TAU2_BENCH_ROOT:+${TAU2_BENCH_ROOT}/src:}${PYTHONPATH:-}"
export TAU2_DATA_ROOT="${DATA_ROOT}"
export OPENVIKING_CONFIG_FILE="${CONFIG}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-${ARK_API_KEY:-}}"
export OPENAI_API_BASE="${OPENAI_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-${OPENAI_API_BASE}}"
export AGENT_API_BASE="${AGENT_API_BASE:-${OPENAI_API_BASE}}"
export USER_API_BASE="${USER_API_BASE:-${OPENAI_API_BASE}}"

check_vikingbot_user_simulator() {
  "${PYTHON_BIN}" - <<'PY' >/dev/null 2>&1
from tau2.gym.gym_agent import AgentGymEnv
import gymnasium  # noqa: F401
assert AgentGymEnv is not None
PY
}

repair_vikingbot_user_simulator() {
  if [[ -z "${TAU2_BENCH_ROOT}" || ! -d "${TAU2_BENCH_ROOT}" ]]; then
    echo "[tau2-service] cannot repair vikingbot user simulator: tau2-bench root not found." >&2
    echo "[tau2-service] set TAU2_BENCH_ROOT or pass --data-root <tau2-bench>/data/tau2." >&2
    return 1
  fi
  echo "[tau2-service] repairing vikingbot user simulator dependency: ${PYTHON_BIN} -m pip install -e ${TAU2_BENCH_ROOT}[gym]"
  "${PYTHON_BIN}" -m pip install -e "${TAU2_BENCH_ROOT}[gym]"
}

if [[ "${ROLLOUT_BACKEND}" == "vikingbot" ]]; then
  if ! check_vikingbot_user_simulator && [[ "${REPAIR_VIKINGBOT_GYM}" == "1" ]]; then
    if ! repair_vikingbot_user_simulator; then
      echo "[tau2-service] vikingbot user simulator repair failed; validating again before exit." >&2
    fi
  fi
  if ! check_vikingbot_user_simulator; then
    cat >&2 <<EOF
[tau2-service] vikingbot backend requires tau2.gym.gym_agent.AgentGymEnv and gymnasium.
[tau2-service] Without them, communicate_with_user falls back to a fixed string and the user simulator is NOT active.
[tau2-service]
[tau2-service] Fix one of:
[tau2-service]   source benchmark/tau2/vikingbot/setup_env.sh
[tau2-service]   ${PYTHON_BIN} -m pip install -e "${TAU2_BENCH_ROOT:-<tau2-bench>}[gym]"
[tau2-service]   bash benchmark/tau2/train/run_service.sh --rollout-backend vikingbot --repair-vikingbot-gym
EOF
    exit 1
  fi
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "[tau2-service] WARNING: OPENAI_API_KEY/ARK_API_KEY is empty; tau2 user simulator LLM calls may fail." >&2
  fi
fi

cd "${REPO_ROOT}"
export TAU2_ROLLOUT_BACKEND="${ROLLOUT_BACKEND}"
export TAU2_NATIVE_THREAD_WORKERS="${NATIVE_THREAD_WORKERS}"
export TAU2_MAX_ROLLOUT_CONCURRENCY="${MAX_ROLLOUT_CONCURRENCY}"
export TAU2_ROLLOUT_THREAD_WORKERS="${ROLLOUT_THREAD_WORKERS}"
echo "[tau2-service] host=${HOST} port=${PORT} data_root=${DATA_ROOT} config=${CONFIG} rollout_language=${ROLLOUT_LANGUAGE} rollout_backend=${ROLLOUT_BACKEND} native_thread_workers=${NATIVE_THREAD_WORKERS} max_rollout_concurrency=${MAX_ROLLOUT_CONCURRENCY} rollout_thread_workers=${ROLLOUT_THREAD_WORKERS}"
if [[ "${KILL_EXISTING}" == "1" ]]; then
  EXISTING_PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${EXISTING_PIDS}" ]]; then
    echo "[tau2-service] stopping existing listener(s) on port ${PORT}: ${EXISTING_PIDS}"
    kill ${EXISTING_PIDS} 2>/dev/null || true
    for _ in {1..20}; do
      sleep 0.2
      if ! lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
        break
      fi
    done
    REMAINING_PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${REMAINING_PIDS}" ]]; then
      echo "[tau2-service] force stopping listener(s) on port ${PORT}: ${REMAINING_PIDS}"
      kill -9 ${REMAINING_PIDS} 2>/dev/null || true
    fi
  fi
fi
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/service_app.py" --host "${HOST}" --port "${PORT}" --data-root "${DATA_ROOT}" --config "${CONFIG}" --rollout-language "${ROLLOUT_LANGUAGE}" --rollout-backend "${ROLLOUT_BACKEND}" --native-thread-workers "${NATIVE_THREAD_WORKERS}" --max-rollout-concurrency "${MAX_ROLLOUT_CONCURRENCY}" --rollout-thread-workers "${ROLLOUT_THREAD_WORKERS}"
