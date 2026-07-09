#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SMOKE_DIR}/../.." && pwd)"

exec "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset smoke \
  --domain tickets \
  --eval-each-epoch \
  --trials 1 \
  --train-trials 1 \
  --concurrency 4 \
  --commit-concurrency 4 \
  --benchmark-service-url "${BENCHMARK_SERVICE_URL:-http://127.0.0.1:1964}" \
  "$@"
