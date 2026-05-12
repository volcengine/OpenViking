#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG="$SCRIPT_DIR/config/baseline.yaml"
EXECUTE=false
PREFLIGHT=false
STRICT_PREFLIGHT=false
RUN_ID=""
RUN_EVAL_EXTRA=()

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
    --preflight)
      PREFLIGHT=true
      shift
      ;;
    --strict-preflight)
      STRICT_PREFLIGHT=true
      shift
      ;;
    --domain|--repeat-count|--strategy-id|--task-id|--num-tasks|--train-num-tasks)
      RUN_EVAL_EXTRA+=("$1" "$2")
      shift 2
      ;;
    --help|-h)
      cat <<'EOF'
Usage:
  benchmark/tau2/run_full_eval.sh [--config PATH] [--run-id ID] [--execute] [--preflight]

Without --execute the script only writes run_plan artifacts.
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
if [[ "$STRICT_PREFLIGHT" == true ]]; then
  RUN_EVAL_EXTRA+=(--strict-preflight)
elif [[ "$PREFLIGHT" == true ]]; then
  RUN_EVAL_EXTRA+=(--preflight)
fi

if [[ "$EXECUTE" == true ]]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/scripts/run_eval.py" --config "$CONFIG" "${RUN_ARGS[@]}" "${RUN_EVAL_EXTRA[@]}" --execute
else
  "$PYTHON_BIN" "$SCRIPT_DIR/scripts/run_eval.py" --config "$CONFIG" "${RUN_ARGS[@]}" "${RUN_EVAL_EXTRA[@]}" --plan-only
fi
