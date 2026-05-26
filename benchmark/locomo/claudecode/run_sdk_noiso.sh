#!/bin/bash
#
# LoCoMo evaluation: SDK pre-ingest + SHARED OpenViking namespace.
#
# All 10 LoCoMo samples are imported into the same OV user/agent (no
# per-sample isolation). QA at recall time relies on semantic retrieval to
# pick the right conv's memories out of the shared pool.
#
# Same env as run_sdk_iso.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

: "${OPENVIKING_PLUGIN_DIR:?set to claude-code-memory-plugin dir}"
export LOCOMO_BENCHMARK_DIR="$SCRIPT_DIR"

INPUT="${LOCOMO_INPUT:-$SCRIPT_DIR/.tmp/locomo10.json}"
PROJECT_ROOT="$SCRIPT_DIR/.tmp/locomo-eval-sdk-noiso"
HOME_DIR="$SCRIPT_DIR/.tmp/claude-eval-home-sdk-noiso"
RESULT_DIR="$SCRIPT_DIR/.tmp/result-sdk-noiso"
mkdir -p "$RESULT_DIR" "$PROJECT_ROOT"

SAMPLE_ARG=()
[ $# -ge 1 ] && SAMPLE_ARG=(--sample "$1")

if ! curl -sf http://127.0.0.1:1933/api/v1/observer/models >/dev/null; then
  echo "ERROR: openviking-server not responding on :1933" >&2; exit 1
fi

echo '{}' > "$PROJECT_ROOT/sample_mapping.json"

echo "[1/4] SDK pre-ingest (shared namespace, --no-user-agent-id)..."
uv run python "$SCRIPT_DIR/import_to_ov.py" \
  --input "$INPUT" \
  --success-csv "$RESULT_DIR/ingest_success.csv" \
  --error-log "$RESULT_DIR/ingest_errors.log" \
  --no-user-agent-id \
  "${SAMPLE_ARG[@]}"

echo "[2/4] running QA (shared OV namespace via --ov-shared-id \"\")..."
uv run python "$SCRIPT_DIR/eval.py" \
  --input "$INPUT" \
  --output "$RESULT_DIR/qa_results.csv" \
  --project-root "$PROJECT_ROOT" \
  --home "$HOME_DIR" \
  --parallel "${QA_PARALLEL:-5}" \
  --hooks-settings "$SCRIPT_DIR/config/ov-hooks.json" \
  --mcp-config "$SCRIPT_DIR/config/ov-mcp.json" \
  --ov-config "$SCRIPT_DIR/config/ov-qa.conf" \
  --ov-shared-id "" \
  ${OPENVIKING_CLI_CONFIG_FILE:+--ov-cli-config "$OPENVIKING_CLI_CONFIG_FILE"} \
  "${SAMPLE_ARG[@]}"

echo "[3/4] judging..."
ARK_API_KEY="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-}}" \
  uv run python "$SCRIPT_DIR/judge.py" --input "$RESULT_DIR/qa_results.csv" --parallel 40

echo "[4/4] stats..."
uv run python "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_DIR/qa_results.csv" \
  | tee "$RESULT_DIR/summary.txt"
