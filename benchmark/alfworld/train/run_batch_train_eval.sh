#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

declare -a DOMAIN_ARG=()
declare -a BENCHMARK_SERVICE_ARG=()
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
if [[ "${HAS_DOMAIN}" == "0" ]]; then
  DOMAIN_ARG=(--domain all)
fi
if [[ "${HAS_BENCHMARK_SERVICE_URL}" == "0" ]]; then
  BENCHMARK_SERVICE_ARG=(--benchmark-service-url "http://127.0.0.1:1954")
fi

exec bash "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset alfworld \
  "${DOMAIN_ARG[@]}" \
  "${BENCHMARK_SERVICE_ARG[@]}" \
  "$@"
