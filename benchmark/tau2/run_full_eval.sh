#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG="$SCRIPT_DIR/config/baseline.yaml"
EXECUTE=false
RUN_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    --execute)
      EXECUTE=true
      shift
      ;;
    --help|-h)
      cat <<'EOF'
Usage:
  benchmark/tau2/run_full_eval.sh [--config PATH] [--run-id ID] [--execute]

Without --execute the script only writes preflight and run_plan artifacts.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

RUN_ARGS=()
if [[ -n "$RUN_ID" ]]; then
  RUN_ARGS+=(--run-id "$RUN_ID")
fi

cd "$REPO_ROOT"
"$PYTHON_BIN" "$SCRIPT_DIR/scripts/preflight.py" --config "$CONFIG" "${RUN_ARGS[@]}"

if [[ "$EXECUTE" == true ]]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/scripts/run_eval.py" --config "$CONFIG" "${RUN_ARGS[@]}" --execute
else
  "$PYTHON_BIN" "$SCRIPT_DIR/scripts/run_eval.py" --config "$CONFIG" "${RUN_ARGS[@]}" --plan-only
fi
