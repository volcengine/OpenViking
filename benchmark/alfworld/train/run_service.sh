#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

ALFWORLD_REPO="${ALFWORLD_REPO:-${HOME}/workspace/alfworld}"
if [[ -z "${ALFWORLD_DATA:-}" && -d "${ALFWORLD_REPO}/data" ]]; then
  export ALFWORLD_DATA="${ALFWORLD_REPO}/data"
fi
export PYTHONPATH="${REPO_ROOT}:${ALFWORLD_REPO}:${PYTHONPATH:-}"
cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" -m benchmark.alfworld.train.service_app "$@"
