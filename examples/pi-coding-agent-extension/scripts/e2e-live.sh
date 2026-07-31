#!/usr/bin/env bash
# Live e2e acceptance gate: real pi + real OpenViking + real LLM.
# Required env is documented in e2e-live.mjs.
set -euo pipefail
cd "$(dirname "$0")/.."
exec node scripts/e2e-live.mjs "$@"
