#!/usr/bin/env bash
set -euo pipefail

# Tau2 convenience launcher for the generic OpenViking session/train batch pipeline.
# Start the tau2 runtime service first:
#   bash benchmark/tau2/train/run_service.sh --host 127.0.0.1 --port 1944
# Pass --rollout-backend native|vikingbot to override per run (default: vikingbot).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"

exec "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset tau2 \
  --domain airline \
  --eval-each-epoch \
  --concurrency 200 \
  --commit-concurrency 200 \
  --benchmark-service-url "${BENCHMARK_SERVICE_URL:-http://127.0.0.1:1944}" \
  "$@"
