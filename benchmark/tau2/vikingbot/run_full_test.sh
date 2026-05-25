#!/usr/bin/env bash
set -euo pipefail

# Run tau2 domain train/test in parallel, then evaluate rewards and commit
# train trajectories into OpenViking memory.
# Usage:
#   bash run_full_test.sh --domain airline [--epoch 0] [--try-no 0] [--result-dir result] [--concurrency N]

CUR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="${CUR_DIR}/scripts"

PIDS=()
kill_tree() {
  local pid="$1"
  local sig="$2"
  local children=""
  children=$(pgrep -P "${pid}" 2>/dev/null || true)
  for child in ${children}; do
    kill_tree "${child}" "${sig}"
  done
  kill "-${sig}" "${pid}" 2>/dev/null || true
}
cleanup() {
  local exit_code=$?
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    echo "[run_full_test] Caught interrupt, stopping child processes..."
    for pid in "${PIDS[@]}"; do
      kill_tree "${pid}" "TERM"
    done
    sleep 1
    for pid in "${PIDS[@]}"; do
      kill_tree "${pid}" "KILL"
    done
  fi
  exit "${exit_code}"
}
trap cleanup INT TERM

DOMAIN=""
EPOCH=0
TRY_NO=0
RESULT_DIR="result"
CONCURRENCY=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="$2"; shift 2 ;;
    --epoch)
      EPOCH="$2"; shift 2 ;;
    --try-no)
      TRY_NO="$2"; shift 2 ;;
    --result-dir)
      RESULT_DIR="$2"; shift 2 ;;
    --concurrency)
      CONCURRENCY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash run_full_test.sh --domain DOMAIN [--epoch N] [--try-no N] [--result-dir DIR] [--concurrency N]"
      exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${DOMAIN}" ]]; then
  echo "Missing --domain" >&2
  exit 1
fi

KEEP_DEFAULT_TOOLS_FLAG=""
ONLY_WRONG_FLAG=""
if [[ "${EPOCH}" -gt 0 ]]; then
  KEEP_DEFAULT_TOOLS_FLAG="--keep-default-tools"
  ONLY_WRONG_FLAG="--only-wrong"
fi

if [[ "${RESULT_DIR}" = /* ]]; then
  OUTPUT_ROOT="${RESULT_DIR}"
else
  OUTPUT_ROOT="${CUR_DIR}/${RESULT_DIR}"
fi
TRAIN_DIR="${OUTPUT_ROOT}/${DOMAIN}_train"
TEST_DIR="${OUTPUT_ROOT}/${DOMAIN}_test"
AGENT_ID=${DOMAIN}_v2
AGENT_ID_FLAG="--agent-id ${AGENT_ID}"

echo "[run_full_test] Start ${DOMAIN} train/test in parallel..."
bash "${SCRIPTS_DIR}/run_tau2_domain.sh" \
  --domain "${DOMAIN}" \
  --split train \
  --epoch "${EPOCH}" \
  --try-no "${TRY_NO}" \
  --result-dir "${RESULT_DIR}" \
  --concurrency "${CONCURRENCY}" \
  ${KEEP_DEFAULT_TOOLS_FLAG} \
  --use-continue ${AGENT_ID_FLAG} &
PID_TRAIN=$!
PIDS+=("${PID_TRAIN}")

bash "${SCRIPTS_DIR}/run_tau2_domain.sh" \
  --domain "${DOMAIN}" \
  --split test \
  --epoch "${EPOCH}" \
  --try-no "${TRY_NO}" \
  --result-dir "${RESULT_DIR}" \
  --concurrency "${CONCURRENCY}" \
  ${KEEP_DEFAULT_TOOLS_FLAG} \
  --use-continue ${AGENT_ID_FLAG} &
PID_TEST=$!
PIDS+=("${PID_TEST}")

wait "${PID_TRAIN}" || true
wait "${PID_TEST}" || true
PIDS=()

REPORT_PATH="${CUR_DIR}/full_test_report_${DOMAIN}.txt"
{
  echo "==== Tau2 Full Test Report ===="
  echo "Domain: ${DOMAIN}"
  echo "Epoch: ${EPOCH}  Try: ${TRY_NO}"
  echo "Result dir: ${RESULT_DIR}"
  echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "-- Train Reward --"
  bash "${SCRIPTS_DIR}/run_eval_reward.sh" "${TRAIN_DIR}" "${EPOCH}" "${TRY_NO}"
  echo
  echo "-- Test Reward --"
  bash "${SCRIPTS_DIR}/run_eval_reward.sh" "${TEST_DIR}" "${EPOCH}" "${TRY_NO}"
} | tee -a "${REPORT_PATH}"

echo "[run_full_test] Report saved to: ${REPORT_PATH}"

# train: commit sessions to extract memory
python "${SCRIPTS_DIR}/commit_trajectory_to_memory.py" \
  --input "${TRAIN_DIR}" \
  --domain "${AGENT_ID}" \
  --pattern "*_${EPOCH}_${TRY_NO}_trajectory.json" \
  --include-eval-result \
  ${ONLY_WRONG_FLAG}
