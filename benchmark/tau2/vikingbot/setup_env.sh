#!/usr/bin/env bash
# Source this script to activate the tau2 vikingbot environment.
# Usage: source setup_env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# OpenViking repo root (this folder lives at benchmark/tau2/vikingbot/).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# --- venv (optional) ---
# If a project venv exists, activate it. Otherwise we rely on the current env.
VENV="${REPO_ROOT}/.venv"
if [[ -f "${VENV}/bin/activate" ]]; then
  source "${VENV}/bin/activate"
  echo "[setup_env] venv activated: ${VENV}"
else
  echo "[setup_env] no venv at ${VENV}; using current Python environment"
fi

# --- local packages via PYTHONPATH (openviking + vikingbot, no pip install needed) ---
# vikingbot lives under the OpenViking repo's bot/ directory.
OPENVIKING_TAU2_ROOT="${REPO_ROOT}"
VIKINGBOT_ROOT="${VIKINGBOT_ROOT:-${REPO_ROOT}/bot}"
export PYTHONPATH="${OPENVIKING_TAU2_ROOT}:${VIKINGBOT_ROOT}:${PYTHONPATH:-}"

# --- tau2-bench checkout (external dependency, see README) ---
# Clone tau2-bench into this folder (./tau2-bench, gitignored) or set TAU2_BENCH_ROOT.
TAU2_BENCH_ROOT="${TAU2_BENCH_ROOT:-${SCRIPT_DIR}/tau2-bench}"
export TAU2_DATA_ROOT="${TAU2_DATA_ROOT:-${TAU2_BENCH_ROOT}/data/tau2}"

# --- OpenViking server config ---
export OPENVIKING_CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"

# --- LLM for tau2 user simulator (e.g. Doubao via volcengine ARK, OpenAI-compatible) ---
# Provide your own key via ARK_API_KEY (do NOT commit real keys).
export OPENAI_API_KEY="${OPENAI_API_KEY:-${ARK_API_KEY:-}}"
export OPENAI_API_BASE="${OPENAI_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
if [[ -z "${OPENAI_API_KEY}" ]]; then
  echo "[setup_env] WARNING: OPENAI_API_KEY/ARK_API_KEY is empty; the tau2 user simulator will fail."
fi

echo "[setup_env] PYTHONPATH includes openviking (${OPENVIKING_TAU2_ROOT}) and vikingbot (${VIKINGBOT_ROOT})"
echo "[setup_env] TAU2_DATA_ROOT=${TAU2_DATA_ROOT}"
echo "[setup_env] OPENAI_API_BASE=${OPENAI_API_BASE}"
