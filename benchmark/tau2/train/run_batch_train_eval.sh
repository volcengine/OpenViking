#!/usr/bin/env bash
set -euo pipefail

# Run remote benchmark batch policy train/eval through the OpenViking session/train pipeline.
#
# The benchmark runtime is accessed only through an HTTP service that implements:
#   POST /v1/cases/query
#   POST /v1/rollouts/execute
#   GET  /v1/rollouts/executions/{execution_id}
#
# For tau2, start the runtime service first:
#   bash benchmark/tau2/service/run_service.sh --host 127.0.0.1 --port 1944

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

DATASET="tau2"
DOMAIN="airline"
EPOCHS="1"
CONCURRENCY="20"
COMMIT_CONCURRENCY="20"
BATCH_SIZE=""
CONFIG="${OPENVIKING_CONFIG_FILE:-}"
OUTPUT=""
SERVER_URL=""
API_KEY="${OPENVIKING_API_KEY:-}"
ACCOUNT_ID="${OPENVIKING_ACCOUNT:-}"
USER_ID="${OPENVIKING_USER:-}"
BENCHMARK_SERVICE_URL="${BENCHMARK_SERVICE_URL:-http://127.0.0.1:1944}"
MAX_ITERATIONS="30"
TRAIN_LIMIT=""
EVAL_LIMIT=""
BASELINE_EVAL="0"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset)
      DATASET="$2"; shift 2 ;;
    --domain)
      DOMAIN="$2"; shift 2 ;;
    --epochs)
      EPOCHS="$2"; shift 2 ;;
    --concurrency)
      CONCURRENCY="$2"; shift 2 ;;
    --commit-concurrency)
      COMMIT_CONCURRENCY="$2"; shift 2 ;;
    --batch-size)
      BATCH_SIZE="$2"; shift 2 ;;
    --config)
      CONFIG="$2"; shift 2 ;;
    --output)
      OUTPUT="$2"; shift 2 ;;
    --server-url)
      SERVER_URL="$2"; shift 2 ;;
    --benchmark-service-url)
      BENCHMARK_SERVICE_URL="$2"; shift 2 ;;
    --api-key)
      API_KEY="$2"; shift 2 ;;
    --account-id)
      ACCOUNT_ID="$2"; shift 2 ;;
    --user-id)
      USER_ID="$2"; shift 2 ;;
    --max-iterations)
      MAX_ITERATIONS="$2"; shift 2 ;;
    --train-limit)
      TRAIN_LIMIT="$2"; shift 2 ;;
    --eval-limit)
      EVAL_LIMIT="$2"; shift 2 ;;
    --baseline-eval)
      BASELINE_EVAL="1"; shift 1 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  bash benchmark/tau2/train/run_batch_train_eval.sh [--dataset DATASET] [--domain DOMAIN] [options]

Options:
  --dataset DATASET                 Remote benchmark dataset. Default: tau2
  --domain DOMAIN                   Benchmark domain. Default: airline
  --epochs N                        Training epochs. Default: 1
  --concurrency N                   Concurrent rollout executions. Default: 20
  --commit-concurrency N            Concurrent session.commit submissions. Default: 20
  --batch-size N                    Optional case load batch size. Default: service page size
  --config PATH                     Optional ov.conf. Default: OPENVIKING_CONFIG_FILE
  --output PATH                     Optional JSON report path
  --server-url URL                  Optional OpenViking server URL
  --benchmark-service-url URL       Benchmark runtime service URL. Default: http://127.0.0.1:1944
  --api-key KEY                     Optional OpenViking API key
  --account-id ID                   OpenViking trusted account id. Default: default
  --user-id ID                      OpenViking trusted user id. Default: default
  --max-iterations N                Runtime max tool iterations per rollout. Default: 30
  --train-limit N                   Limit train cases for smoke tests
  --eval-limit N                    Limit eval cases for smoke tests
  --baseline-eval                   Run pre-training baseline eval. Disabled by default

Environment:
  PYTHON_BIN=python3                Override Python executable
  BENCHMARK_SERVICE_URL=...         Default benchmark runtime service URL
  OPENVIKING_CONFIG_FILE=...        Used as --config when --config is not passed

Examples:
  bash benchmark/tau2/train/run_batch_train_eval.sh --domain airline --epochs 1 --concurrency 4
  bash benchmark/tau2/train/run_batch_train_eval.sh --dataset my_dataset --domain my_domain \
    --benchmark-service-url http://127.0.0.1:1944
EOF
      exit 0 ;;
    *)
      EXTRA_ARGS+=("$1"); shift ;;
  esac
done

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
export OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"

CONFIG="${CONFIG:-${OPENVIKING_CONFIG_FILE:-}}"

if [[ -z "${ACCOUNT_ID}" || -z "${USER_ID}" ]]; then
  RESOLVED_OV_IDENTITY="$(${PYTHON_BIN} - "${CONFIG}" <<'PY'
import json
import os
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 and sys.argv[1] else None
ov_data = {}
if config_path and config_path.exists():
    try:
        ov_data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except Exception:
        ov_data = {}
ov_server = (ov_data.get("bot") or {}).get("ov_server") or {}
account = str(ov_server.get("account_id") or "").strip()
user = str(ov_server.get("admin_user_id") or "").strip()

cli_path = Path(os.environ.get("OPENVIKING_CLI_CONFIG_FILE") or Path.home() / ".openviking" / "ovcli.conf").expanduser()
if (not account or not user) and cli_path.exists():
    try:
        cli_data = json.loads(cli_path.read_text(encoding="utf-8-sig"))
    except Exception:
        cli_data = {}
    account = account or str(cli_data.get("account") or "").strip()
    user = user or str(cli_data.get("user") or "").strip()

print(f"{account or 'default'}\t{user or 'default'}")
PY
)"
  IFS=$'\t' read -r RESOLVED_ACCOUNT_ID RESOLVED_USER_ID <<< "${RESOLVED_OV_IDENTITY}"
  ACCOUNT_ID="${ACCOUNT_ID:-${RESOLVED_ACCOUNT_ID:-default}}"
  USER_ID="${USER_ID:-${RESOLVED_USER_ID:-default}}"
fi

CMD=(
  "${PYTHON_BIN}" "${SCRIPT_DIR}/run_batch_train_eval.py"
  --dataset "${DATASET}"
  --domain "${DOMAIN}"
  --epochs "${EPOCHS}"
  --concurrency "${CONCURRENCY}"
  --commit-concurrency "${COMMIT_CONCURRENCY}"
  --max-iterations "${MAX_ITERATIONS}"
)

if [[ -n "${BATCH_SIZE}" ]]; then
  CMD+=(--batch-size "${BATCH_SIZE}")
fi
if [[ -n "${CONFIG}" ]]; then
  CMD+=(--config "${CONFIG}")
fi
if [[ -n "${OUTPUT}" ]]; then
  CMD+=(--output "${OUTPUT}")
fi
if [[ -n "${TRAIN_LIMIT}" ]]; then
  CMD+=(--train-limit "${TRAIN_LIMIT}")
fi
if [[ -n "${EVAL_LIMIT}" ]]; then
  CMD+=(--eval-limit "${EVAL_LIMIT}")
fi
if [[ "${BASELINE_EVAL}" == "1" ]]; then
  CMD+=(--baseline-eval)
fi
if [[ -n "${SERVER_URL}" ]]; then
  CMD+=(--server-url "${SERVER_URL}")
fi
if [[ -n "${BENCHMARK_SERVICE_URL}" ]]; then
  CMD+=(--benchmark-service-url "${BENCHMARK_SERVICE_URL}")
fi
if [[ -n "${API_KEY}" ]]; then
  CMD+=(--api-key "${API_KEY}")
fi
if [[ -n "${ACCOUNT_ID}" ]]; then
  CMD+=(--account-id "${ACCOUNT_ID}")
fi
if [[ -n "${USER_ID}" ]]; then
  CMD+=(--user-id "${USER_ID}")
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

cd "${REPO_ROOT}"
echo "[batch-train] repo: ${REPO_ROOT}"
echo "[batch-train] dataset=${DATASET} domain=${DOMAIN} epochs=${EPOCHS} concurrency=${CONCURRENCY} commit_concurrency=${COMMIT_CONCURRENCY} baseline_eval=${BASELINE_EVAL}"
echo "[batch-train] config=${CONFIG:-<default>}"
echo "[batch-train] ov_identity=${ACCOUNT_ID:-<unset>}/${USER_ID:-<unset>}"
echo "[batch-train] benchmark_service_url=${BENCHMARK_SERVICE_URL:-<unset>}"
echo "[batch-train] command: ${CMD[*]}"
exec "${CMD[@]}"
