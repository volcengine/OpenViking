#!/usr/bin/env bash
set -euo pipefail

# Run VikingBot tau2 runner for all tasks in {domain}_{train|test} split.
# Usage:
#   bash run_tau2_domain.sh --domain telecom --split train --epoch 0 --try-no 0 \
#     --keep-default-tools --result-dir result --use-continue --config .generated/telecom_v0.ov.conf --concurrency 5


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNNER="${SCRIPT_DIR}/vikingbot_tau2_runner.py"

DOMAIN=""
SPLIT=""
EPOCH=0
TRY_NO=0
KEEP_DEFAULT_TOOLS=0
RESULT_DIR="result"
USE_CONTINUE=0
CONFIG=""
CONCURRENCY=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="$2"; shift 2 ;;
    --split)
      SPLIT="$2"; shift 2 ;;
    --epoch)
      EPOCH="$2"; shift 2 ;;
    --try-no)
      TRY_NO="$2"; shift 2 ;;
    --keep-default-tools)
      KEEP_DEFAULT_TOOLS=1; shift 1 ;;
    --result-dir)
      RESULT_DIR="$2"; shift 2 ;;
    --use-continue)
      USE_CONTINUE=1; shift 1 ;;
    --config)
      CONFIG="$2"; shift 2 ;;
    --concurrency)
      CONCURRENCY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash run_tau2_domain.sh --domain DOMAIN --split train|test --epoch N --try-no N --keep-default-tools --result-dir DIR --use-continue --config PATH --concurrency N"; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${DOMAIN}" || ( "${SPLIT}" != "train" && "${SPLIT}" != "test" ) ]]; then
  echo "Usage: bash run_tau2_domain.sh --domain DOMAIN --split train|test --epoch N --try-no N --keep-default-tools --result-dir DIR --use-continue --config PATH --concurrency N" >&2
  exit 1
fi

DATA_ROOT="${TAU2_DATA_ROOT}"
SPLIT_TASKS_JSON="${DATA_ROOT}/domains/${DOMAIN}/split_tasks.json"

if [[ "${RESULT_DIR}" = /* ]]; then
  OUTPUT_ROOT="${RESULT_DIR}/${DOMAIN}_${SPLIT}"
else
  OUTPUT_ROOT="${REPO_ROOT}/${RESULT_DIR}/${DOMAIN}_${SPLIT}"
fi
mkdir -p "${OUTPUT_ROOT}"

if [[ ! -f "${SPLIT_TASKS_JSON}" ]]; then
  echo "Split file not found: ${SPLIT_TASKS_JSON}" >&2
  exit 1
fi

TASK_COUNT=$(SPLIT_TASKS_JSON="${SPLIT_TASKS_JSON}" SPLIT_NAME="${SPLIT}" python3 - <<'PY'
import json
import os

path = os.environ.get("SPLIT_TASKS_JSON")
split_name = os.environ.get("SPLIT_NAME")
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
print(len(data[split_name]))
PY
)

echo "${DOMAIN}_${SPLIT} task count: ${TASK_COUNT}, concurrency: ${CONCURRENCY}"

# build reusable flags
KEEP_DEFAULT_TOOLS_FLAG=""
CONTINUE_FLAG=""
CONFIG_FLAG=""
[[ "${KEEP_DEFAULT_TOOLS}" == "1" ]] && KEEP_DEFAULT_TOOLS_FLAG="--keep-default-tools"
[[ "${USE_CONTINUE}" == "1" ]]       && CONTINUE_FLAG="--continue"
[[ -n "${CONFIG}" ]]                 && CONFIG_FLAG="--config ${CONFIG}"

# Use a tmpdir as a semaphore: each running task holds a slot file.
SEM_DIR=$(mktemp -d)
trap 'rm -rf "${SEM_DIR}"' EXIT

active_count() { ls "${SEM_DIR}" 2>/dev/null | wc -l | tr -d ' '; }

for ((task_no=0; task_no < TASK_COUNT; task_no++)); do
  out_path="${OUTPUT_ROOT}/task_${task_no}_${EPOCH}_${TRY_NO}_trajectory.json"
  echo "[${DOMAIN}_${SPLIT}] task_no=${task_no} epoch=${EPOCH} try_no=${TRY_NO}"

  # wait for a free slot
  while [[ $(active_count) -ge ${CONCURRENCY} ]]; do sleep 0.5; done

  SLOT="${SEM_DIR}/${task_no}"
  touch "${SLOT}"
  (
    python "${RUNNER}" \
      --data-split "${DOMAIN}_${SPLIT}" \
      --task-no "${task_no}" \
      --output "${out_path}" ${CONFIG_FLAG} ${KEEP_DEFAULT_TOOLS_FLAG} ${CONTINUE_FLAG} \
      || echo "[WARN] task_no=${task_no} failed, skipping"
    rm -f "${SLOT}"
  ) &
done

wait || true
rm -rf "${SEM_DIR}"
