#!/usr/bin/env bash
# Live acceptance gate for the agent-driven context window:
# real pi + real OpenViking + real LLM relay. Manual, never run in CI.
#
# Required env (documented in e2e-window.mjs):
#   OPENVIKING_URL OPENVIKING_API_KEY E2E_LLM_API_KEY
# Optional:
#   PI_BIN E2E_LLM_BASE_URL E2E_LLM_MODEL E2E_LLM_API E2E_KEEP_TMP
#   E2E_WINDOW_FAILCLOSED=1|both
#
# Usage:
#   OPENVIKING_URL=... OPENVIKING_API_KEY=... E2E_LLM_API_KEY=... scripts/e2e-window.sh
#   E2E_WINDOW_FAILCLOSED=1 ... scripts/e2e-window.sh   # only the fail-closed variant
set -euo pipefail
cd "$(dirname "$0")/.."
exec node scripts/e2e-window.mjs "$@"
