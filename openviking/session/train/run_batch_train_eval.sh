#!/usr/bin/env bash
set -euo pipefail

# Generic launcher for the OpenViking session/train remote benchmark batch pipeline.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

load_user_env_file() {
  local env_file="${OPENVIKING_ENV_FILE:-${HOME}/.openviking_benchmark_env}"
  if [[ -z "${env_file}" || ! -f "${env_file}" ]]; then
    return
  fi

  local -a preserved_env=()
  local entry
  while IFS= read -r -d '' entry; do
    if [[ "${entry}" != *= ]]; then
      preserved_env+=("${entry}")
    fi
  done < <(env -0)

  echo "[batch-train-eval] loading env file ${env_file}"
  set +u
  set -a
  # shellcheck source=/dev/null
  source "${env_file}"
  set +a
  set -euo pipefail

  for entry in "${preserved_env[@]}"; do
    export "${entry}"
  done
}

load_user_env_file

PYTHON_BIN="${PYTHON_BIN:-python}"

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
export OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"

cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" -m openviking.session.train.run_batch_train_eval "$@"
