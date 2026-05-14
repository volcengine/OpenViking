#!/bin/bash
#
# LoCoMo evaluation: SDK pre-ingest + per-sample namespace isolation.
#
# Each LoCoMo sample (conv-XX) gets its own OpenViking user/agent_id.
# Ingest goes straight to the OV server via the openviking Python SDK
# (import_to_ov.py); no Claude Code is involved at ingest time.
# QA uses Claude Code with the plugin's auto-recall hook + MCP, talking to
# the per-sample OV namespace.
#
# Required env:
#   ANTHROPIC_AUTH_TOKEN   - or ANTHROPIC_API_KEY
#   ANTHROPIC_BASE_URL     - e.g. https://ark.cn-beijing.volces.com/api/compatible
#   ANTHROPIC_MODEL        - e.g. doubao-seed-2-0-code-preview-260215
#   OPENVIKING_PLUGIN_DIR  - path to claude-code-memory-plugin (for hooks)
#   OPENVIKING_CLI_CONFIG_FILE (optional) - ovcli.conf override, e.g. ovcli-local.conf
#
# OpenViking server must be running at 127.0.0.1:1933.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

: "${OPENVIKING_PLUGIN_DIR:?set to claude-code-memory-plugin dir}"
export LOCOMO_BENCHMARK_DIR="$SCRIPT_DIR"

INPUT="${LOCOMO_INPUT:-$SCRIPT_DIR/.tmp/locomo10.json}"
PROJECT_ROOT="$SCRIPT_DIR/.tmp/locomo-eval-sdk-iso"
HOME_DIR="$SCRIPT_DIR/.tmp/claude-eval-home-sdk-iso"
RESULT_DIR="$SCRIPT_DIR/.tmp/result-sdk-iso"
mkdir -p "$RESULT_DIR" "$PROJECT_ROOT"

SAMPLE_ARG=()
[ $# -ge 1 ] && SAMPLE_ARG=(--sample "$1")

if ! curl -sf http://127.0.0.1:1933/api/v1/observer/models >/dev/null; then
  echo "ERROR: openviking-server not responding on :1933" >&2; exit 1
fi

# eval.py expects sample_mapping.json under --project-root in non-shared-cwd mode
echo '{}' > "$PROJECT_ROOT/sample_mapping.json"

echo "[1/4] SDK pre-ingest (per-sample namespace)..."
uv run python "$SCRIPT_DIR/import_to_ov.py" \
  --input "$INPUT" \
  --success-csv "$RESULT_DIR/ingest_success.csv" \
  --error-log "$RESULT_DIR/ingest_errors.log" \
  "${SAMPLE_ARG[@]}"

echo "[2/4] running QA (per-sample agent_id from sample_id)..."
uv run python "$SCRIPT_DIR/eval.py" \
  --input "$INPUT" \
  --output "$RESULT_DIR/qa_results.csv" \
  --project-root "$PROJECT_ROOT" \
  --home "$HOME_DIR" \
  --parallel "${QA_PARALLEL:-5}" \
  --hooks-settings "$SCRIPT_DIR/config/ov-hooks.json" \
  --mcp-config "$SCRIPT_DIR/config/ov-mcp.json" \
  --ov-config "$SCRIPT_DIR/config/ov-qa.conf" \
  ${OPENVIKING_CLI_CONFIG_FILE:+--ov-cli-config "$OPENVIKING_CLI_CONFIG_FILE"} \
  "${SAMPLE_ARG[@]}"

echo "[3/4] judging..."
ARK_API_KEY="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-}}" \
  uv run python "$SCRIPT_DIR/judge.py" --input "$RESULT_DIR/qa_results.csv" --parallel 40

echo "[4/4] stats..."
uv run python "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_DIR/qa_results.csv" \
  | tee "$RESULT_DIR/summary.txt"
