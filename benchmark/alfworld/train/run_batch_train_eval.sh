#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

HAS_DOMAIN=0
HAS_BENCHMARK_SERVICE_URL=0
for arg in "$@"; do
  case "${arg}" in
    --domain|--domain=*)
      HAS_DOMAIN=1
      ;;
    --benchmark-service-url|--benchmark-service-url=*)
      HAS_BENCHMARK_SERVICE_URL=1
      ;;
  esac
done
if [[ "${HAS_BENCHMARK_SERVICE_URL}" == "0" ]]; then
  set -- --benchmark-service-url "http://127.0.0.1:1954" "$@"
fi
if [[ "${HAS_DOMAIN}" == "0" ]]; then
  set -- --domain all "$@"
fi

exec bash "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset alfworld \
  "$@"
