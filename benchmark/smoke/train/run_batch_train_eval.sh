#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SMOKE_DIR}/../.." && pwd)"

HAS_BENCHMARK_SERVICE_URL=0
for arg in "$@"; do
  case "${arg}" in
    --benchmark-service-url|--benchmark-service-url=*)
      HAS_BENCHMARK_SERVICE_URL=1
      ;;
  esac
done
if [[ "${HAS_BENCHMARK_SERVICE_URL}" == "0" ]]; then
  set -- --benchmark-service-url "http://127.0.0.1:1964" "$@"
fi

exec "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset smoke \
  --domain tickets \
  --eval-each-epoch \
  --trials 1 \
  --train-trials 1 \
  --concurrency 4 \
  --commit-concurrency 4 \
  "$@"
