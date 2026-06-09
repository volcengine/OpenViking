#!/usr/bin/env bash
set -euo pipefail

# Run one epoch of a tau2 domain:
#   - train: a single run (used to extract experience into memory; mirrors real usage)
#   - test : TEST_REPEATS independent runs in parallel, averaged (agent execution is stochastic,
#            so averaging repeats gives a more confident accuracy estimate)
# Then evaluate rewards and commit the train trajectories to memory.
# Usage:
#   bash run_full_test.sh --domain airline [--epoch 0] [--test-repeats 8] [--result-dir result] [--concurrency N] [--config PATH] [--commit|--no-commit]

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
TEST_REPEATS=8
RESULT_DIR="result"
CONCURRENCY=1
DO_COMMIT=1
CONFIG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="$2"; shift 2 ;;
    --epoch)
      EPOCH="$2"; shift 2 ;;
    --test-repeats)
      TEST_REPEATS="$2"; shift 2 ;;
    --result-dir)
      RESULT_DIR="$2"; shift 2 ;;
    --concurrency)
      CONCURRENCY="$2"; shift 2 ;;
    --config)
      CONFIG="$2"; shift 2 ;;
    --no-commit)
      DO_COMMIT=0; shift ;;
    --commit)
      DO_COMMIT=1; shift ;;
    -h|--help)
      echo "Usage: bash run_full_test.sh --domain DOMAIN [--epoch N] [--test-repeats N] [--result-dir DIR] [--concurrency N] [--config PATH] [--commit|--no-commit]"
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
CONFIG_FLAG=""
[[ -n "${CONFIG}" ]] && CONFIG_FLAG="--config ${CONFIG}"
# train runs once per epoch; test repeats are averaged.
TRAIN_TRY_NO=0

echo "[run_full_test] Start ${DOMAIN}: 1 train run + ${TEST_REPEATS} test run(s), in parallel..."

# train: a single run per epoch
bash "${SCRIPTS_DIR}/run_tau2_domain.sh" \
  --domain "${DOMAIN}" \
  --split train \
  --epoch "${EPOCH}" \
  --try-no "${TRAIN_TRY_NO}" \
  --result-dir "${RESULT_DIR}" \
  --concurrency "${CONCURRENCY}" \
  ${KEEP_DEFAULT_TOOLS_FLAG} \
  --use-continue ${CONFIG_FLAG} &
PIDS+=("$!")

# test: TEST_REPEATS independent runs (try-no 0 .. TEST_REPEATS-1), averaged at eval time
for ((t=0; t<TEST_REPEATS; t++)); do
  bash "${SCRIPTS_DIR}/run_tau2_domain.sh" \
    --domain "${DOMAIN}" \
    --split test \
    --epoch "${EPOCH}" \
    --try-no "${t}" \
    --result-dir "${RESULT_DIR}" \
    --concurrency "${CONCURRENCY}" \
    ${KEEP_DEFAULT_TOOLS_FLAG} \
    --use-continue ${CONFIG_FLAG} &
  PIDS+=("$!")
done

for pid in "${PIDS[@]}"; do
  wait "${pid}" || true
done
PIDS=()

REPORT_PATH="${CUR_DIR}/full_test_report_${DOMAIN}.txt"
{
  echo "==== Tau2 Full Test Report ===="
  echo "Domain: ${DOMAIN}"
  echo "Epoch: ${EPOCH}  Test repeats: ${TEST_REPEATS}"
  echo "Result dir: ${RESULT_DIR}"
  echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "-- Train Reward (try ${TRAIN_TRY_NO}) --"
  bash "${SCRIPTS_DIR}/run_eval_reward.sh" "${TRAIN_DIR}" "${EPOCH}" "${TRAIN_TRY_NO}" || true
  echo
  echo "-- Test Reward (averaged over ${TEST_REPEATS} repeat(s)) --"
  acc_file="$(mktemp)"
  for ((t=0; t<TEST_REPEATS; t++)); do
    echo "[test try=${t}]"
    test_out="$(bash "${SCRIPTS_DIR}/run_eval_reward.sh" "${TEST_DIR}" "${EPOCH}" "${t}" || true)"
    echo "${test_out}"
    echo "${test_out}" | awk -F': ' '/Average reward:/ {print $2}' | tail -n 1 >> "${acc_file}"
    echo
  done
  echo -n "Test average accuracy over ${TEST_REPEATS} repeat(s): "
  awk '{sum += $1; n++} END {if (n > 0) printf "%.6f\n", sum / n; else print "n/a"}' "${acc_file}"
  rm -f "${acc_file}"
} | tee -a "${REPORT_PATH}"

echo "[run_full_test] Report saved to: ${REPORT_PATH}"

# train: commit trajectories to extract memory (consumed by the next epoch)
if [[ "${DO_COMMIT}" -eq 1 ]]; then
  echo "[run_full_test] Commit train trajectories to memory..."
  python "${SCRIPTS_DIR}/commit_trajectory_to_memory.py" \
    --input "${TRAIN_DIR}" \
    --pattern "*_${EPOCH}_${TRAIN_TRY_NO}_trajectory.json" \
    --include-eval-result \
    ${CONFIG_FLAG} \
    ${ONLY_WRONG_FLAG}
else
  echo "[run_full_test] Skip commit (--no-commit)"
fi
