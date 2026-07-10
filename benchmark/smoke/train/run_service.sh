#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SMOKE_DIR}/../.." && pwd)"

PYTHON_BIN="python"
HOST="127.0.0.1"
PORT="1964"
KILL_EXISTING=1
NATIVE_THREAD_WORKERS="8"
MAX_ROLLOUT_CONCURRENCY="32"
ROLLOUT_THREAD_WORKERS="8"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --native-thread-workers) NATIVE_THREAD_WORKERS="$2"; shift 2 ;;
    --max-rollout-concurrency) MAX_ROLLOUT_CONCURRENCY="$2"; shift 2 ;;
    --rollout-thread-workers) ROLLOUT_THREAD_WORKERS="$2"; shift 2 ;;
    --no-kill-existing) KILL_EXISTING=0; shift 1 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  bash benchmark/smoke/train/run_service.sh [--host 127.0.0.1] [--port 1964]

Starts a deterministic smoke rollout service. It has no external dataset or LLM dependency.
EOF
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

if [[ "${KILL_EXISTING}" == "1" ]]; then
  EXISTING_PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${EXISTING_PIDS}" ]]; then
    echo "[smoke-service] stopping existing listener(s) on port ${PORT}: ${EXISTING_PIDS}"
    kill ${EXISTING_PIDS} 2>/dev/null || true
    for _ in {1..20}; do
      sleep 0.2
      if ! lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
        break
      fi
    done
  fi
fi

cd "${REPO_ROOT}"
echo "[smoke-service] host=${HOST} port=${PORT} max_rollout_concurrency=${MAX_ROLLOUT_CONCURRENCY} rollout_thread_workers=${ROLLOUT_THREAD_WORKERS}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/service_app.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --native-thread-workers "${NATIVE_THREAD_WORKERS}" \
  --max-rollout-concurrency "${MAX_ROLLOUT_CONCURRENCY}" \
  --rollout-thread-workers "${ROLLOUT_THREAD_WORKERS}"
