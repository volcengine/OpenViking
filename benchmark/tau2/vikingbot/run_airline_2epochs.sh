#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/run_airline_2epochs.log"

# --- env setup (inline, no source needed) ---
source "${SCRIPT_DIR}/setup_env.sh"

CONCURRENCY=5
DOMAIN=airline
RESULT_DIR=result
CONFIG="${TAU2_VIKINGBOT_CONFIG:-}"
CONFIG_FLAG=""
[[ -n "${CONFIG}" ]] && CONFIG_FLAG="--config ${CONFIG}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

log "===== Start airline 2-epoch run ====="

# ---------- Epoch 0 ----------
log ">>> Epoch 0 start (cold start, no memory)"
bash "${SCRIPT_DIR}/run_full_test.sh" \
  --domain "${DOMAIN}" \
  --epoch 0 \
  --concurrency "${CONCURRENCY}" \
  --result-dir "${RESULT_DIR}" \
  ${CONFIG_FLAG}
log ">>> Epoch 0 done"

# Wait for OpenViking server to finish async memory processing
WAIT_SECS=9000
log ">>> Waiting ${WAIT_SECS}s for server async memory commit to finish..."
sleep "${WAIT_SECS}"
log ">>> Wait done, starting Epoch 1"

# ---------- Epoch 1 ----------
log ">>> Epoch 1 start (with memory from epoch 0 train)"
bash "${SCRIPT_DIR}/run_full_test.sh" \
  --domain "${DOMAIN}" \
  --epoch 1 \
  --concurrency "${CONCURRENCY}" \
  --result-dir "${RESULT_DIR}" \
  ${CONFIG_FLAG}
log ">>> Epoch 1 done"

log "===== All done. Results in: ${SCRIPT_DIR}/${RESULT_DIR} ====="
log "===== Report: ${SCRIPT_DIR}/full_test_report_${DOMAIN}.txt ====="
