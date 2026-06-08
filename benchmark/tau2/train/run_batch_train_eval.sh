#!/usr/bin/env bash
set -euo pipefail

# Run tau2 batch policy train/eval through the OpenViking session/train pipeline.
#
# Examples:
#   bash benchmark/tau2/train/run_batch_train_eval.sh
#   bash benchmark/tau2/train/run_batch_train_eval.sh --domain airline --epochs 2 --concurrency 20 \
#     --config benchmark/tau2/vikingbot/.generated/tau2_airline_v0.ov.conf
#
# Environment overrides:
#   OPENVIKING_CONFIG_FILE=...   Used as --config when --config is not passed
#   TAU2_DATA_ROOT=...           Tau2 data root, normally exported by setup_env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

DOMAIN="airline"
EPOCHS="1"
CONCURRENCY="20"
BATCH_SIZE=""
CONFIG="${OPENVIKING_CONFIG_FILE:-}"
OUTPUT=""
DATA_ROOT="${TAU2_DATA_ROOT:-}"
MAX_ITERATIONS="30"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="$2"; shift 2 ;;
    --epochs)
      EPOCHS="$2"; shift 2 ;;
    --concurrency)
      CONCURRENCY="$2"; shift 2 ;;
    --batch-size)
      BATCH_SIZE="$2"; shift 2 ;;
    --config)
      CONFIG="$2"; shift 2 ;;
    --output)
      OUTPUT="$2"; shift 2 ;;
    --data-root)
      DATA_ROOT="$2"; shift 2 ;;
    --max-iterations)
      MAX_ITERATIONS="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  bash benchmark/tau2/train/run_batch_train_eval.sh [--domain DOMAIN] [options]

Options:
  --domain DOMAIN             Tau2 domain. Default: airline
  --epochs N                  Training epochs. Default: 1
  --concurrency N             Concurrent rollouts for train/eval. Default: 20
  --batch-size N              Optional. Default: whole split as one batch
  --config PATH               Optional ov.conf. Default: OPENVIKING_CONFIG_FILE
  --output PATH               Optional JSON report path
  --data-root PATH            Optional tau2 data root. Default: TAU2_DATA_ROOT
  --max-iterations N          VikingBot max tool iterations. Default: 30

Environment:
  PYTHON_BIN=python3          Override Python executable

Examples:
  bash benchmark/tau2/train/run_batch_train_eval.sh
  bash benchmark/tau2/train/run_batch_train_eval.sh --domain airline --epochs 2 --concurrency 20 \
    --config benchmark/tau2/vikingbot/.generated/tau2_airline_v0.ov.conf
EOF
      exit 0 ;;
    *)
      EXTRA_ARGS+=("$1"); shift ;;
  esac
done


VIKINGBOT_ROOT="${VIKINGBOT_ROOT:-${REPO_ROOT}/bot}"
export PYTHONPATH="${REPO_ROOT}:${VIKINGBOT_ROOT}:${PYTHONPATH:-}"
export TAU2_DATA_ROOT="${TAU2_DATA_ROOT:-${TAU2_DIR}/vikingbot/tau2-bench/data/tau2}"
export OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-${ARK_API_KEY:-}}"
export OPENAI_API_BASE="${OPENAI_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"

CONFIG="${CONFIG:-${OPENVIKING_CONFIG_FILE:-}}"
DATA_ROOT="${DATA_ROOT:-${TAU2_DATA_ROOT:-}}"

CMD=(
  "${PYTHON_BIN}" "${SCRIPT_DIR}/run_batch_train_eval.py"
  --domain "${DOMAIN}"
  --epochs "${EPOCHS}"
  --concurrency "${CONCURRENCY}"
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
if [[ -n "${DATA_ROOT}" ]]; then
  SPLIT_FILE="${DATA_ROOT%/}/domains/${DOMAIN}/split_tasks.json"
  if [[ ! -f "${SPLIT_FILE}" ]]; then
    echo "[tau2-train] tau2 data split file not found: ${SPLIT_FILE}" >&2
    echo "[tau2-train] Please set --data-root or TAU2_DATA_ROOT to <tau2-bench>/data/tau2." >&2
    echo "[tau2-train] Example:" >&2
    echo "  TAU2_DATA_ROOT=/path/to/tau2-bench/data/tau2 benchmark/tau2/train/run_batch_train_eval.sh --domain ${DOMAIN}" >&2
    exit 1
  fi
  CMD+=(--data-root "${DATA_ROOT}")
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

cd "${REPO_ROOT}"
echo "[tau2-train] repo: ${REPO_ROOT}"
echo "[tau2-train] domain=${DOMAIN} epochs=${EPOCHS} concurrency=${CONCURRENCY}"
echo "[tau2-train] config=${CONFIG:-<default>}"
echo "[tau2-train] data_root=${DATA_ROOT:-${TAU2_DATA_ROOT:-<unset>}}"
echo "[tau2-train] command: ${CMD[*]}"
exec "${CMD[@]}"
