#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

exec bash "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset alfworld \
  --domain "${ALFWORLD_DOMAIN:-all}" \
  --benchmark-service-url "${ALFWORLD_SERVICE_URL:-http://127.0.0.1:1954}" \
  "$@"
