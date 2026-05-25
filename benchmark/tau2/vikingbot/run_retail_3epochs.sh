#!/usr/bin/env bash
set -euo pipefail

# Example: 3-epoch retail run (cold start -> 2 memory-augmented epochs).
# Each epoch runs train+test, evaluates, and commits train trajectories to memory.
# Between epochs we wait for the OpenViking server to finish async memory commit.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/run_retail_3epochs.log"

# --- env setup ---
source "${SCRIPT_DIR}/setup_env.sh"

CONCURRENCY=2
DOMAIN=retail
RESULT_DIR=result
# Seconds to wait for the server's async memory commit to finish between epochs.
WAIT_SECS=300

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

log "===== Start retail 3-epoch run ====="

for epoch in 0 1 2; do
  if [[ "${epoch}" -eq 0 ]]; then
    log ">>> Epoch ${epoch} start (cold start, no memory)"
  else
    log ">>> Epoch ${epoch} start (with memory from previous epochs)"
  fi

  bash "${SCRIPT_DIR}/run_full_test.sh" \
    --domain "${DOMAIN}" \
    --epoch "${epoch}" \
    --concurrency "${CONCURRENCY}" \
    --result-dir "${RESULT_DIR}"
  log ">>> Epoch ${epoch} done"

  if [[ "${epoch}" -lt 2 ]]; then
    log ">>> Waiting ${WAIT_SECS}s for server async memory commit to finish..."
    sleep "${WAIT_SECS}"
  fi
done

log "===== All done. Results in: ${SCRIPT_DIR}/${RESULT_DIR} ====="
log "===== Report: ${SCRIPT_DIR}/full_test_report_${DOMAIN}.txt ====="
