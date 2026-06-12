#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
HOST="127.0.0.1"
PORT="1944"
DATA_ROOT="${TAU2_DATA_ROOT:-}"
CONFIG="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"
KILL_EXISTING=1
ROLLOUT_LANGUAGE="default"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --data-root) DATA_ROOT="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --rollout-language) ROLLOUT_LANGUAGE="$2"; shift 2 ;;
    --no-kill-existing) KILL_EXISTING=0; shift 1 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  bash benchmark/tau2/service/run_service.sh [--host 127.0.0.1] [--port 1944]

Options:
  --data-root PATH   tau2-bench data/tau2 root. Default auto-detect/TAU2_DATA_ROOT
  --config PATH      ov.conf for VikingBot/OpenViking access. Default ~/.openviking/ov.conf
  --rollout-language default|zh
                     Rollout response language. Use zh for Chinese user-facing replies.
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

cd "${REPO_ROOT}"
echo "[tau2-service] host=${HOST} port=${PORT} data_root=${DATA_ROOT} config=${CONFIG} rollout_language=${ROLLOUT_LANGUAGE}"
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
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/app.py" --host "${HOST}" --port "${PORT}" --data-root "${DATA_ROOT}" --config "${CONFIG}" --rollout-language "${ROLLOUT_LANGUAGE}"
