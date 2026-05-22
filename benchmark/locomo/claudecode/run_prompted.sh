#!/bin/bash
#
# LoCoMo evaluation: Claude Code Prompted (vanilla auto-memory, no OpenViking).
#
# Floor reference: ingest each LoCoMo session via `claude -p`; CC writes notes
# into MEMORY.md inside per-sample project dirs. QA reads MEMORY.md back to
# answer questions. No hooks, no MCP, no OV.
#
# Required env (or pass via flags):
#   ANTHROPIC_AUTH_TOKEN  - or ANTHROPIC_API_KEY
#   ANTHROPIC_BASE_URL    - e.g. https://ark.cn-beijing.volces.com/api/compatible
#   ANTHROPIC_MODEL       - e.g. doubao-seed-2-0-code-preview-260215
#
# LoCoMo data must be at $SCRIPT_DIR/.tmp/locomo10.json.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

INPUT="${LOCOMO_INPUT:-$SCRIPT_DIR/.tmp/locomo10.json}"
PROJECT_ROOT="$SCRIPT_DIR/.tmp/locomo-eval-prompted"
HOME_DIR="$SCRIPT_DIR/.tmp/claude-eval-home-prompted"
RESULT_DIR="$SCRIPT_DIR/.tmp/result-prompted"
mkdir -p "$RESULT_DIR"

SAMPLE_ARG=()
[ $# -ge 1 ] && SAMPLE_ARG=(--sample "$1")

echo "[1/4] ingest into CC auto-memory..."
uv run python "$SCRIPT_DIR/ingest.py" \
  --input "$INPUT" \
  --project-root "$PROJECT_ROOT" \
  --home "$HOME_DIR" \
  --record "$RESULT_DIR/.ingest_record.json" \
  --success-csv "$RESULT_DIR/ingest_success.csv" \
  --error-log "$RESULT_DIR/ingest_errors.log" \
  "${SAMPLE_ARG[@]}"

echo "[2/4] running QA..."
uv run python "$SCRIPT_DIR/eval.py" \
  --input "$INPUT" \
  --output "$RESULT_DIR/qa_results.csv" \
  --project-root "$PROJECT_ROOT" \
  --home "$HOME_DIR" \
  --parallel "${QA_PARALLEL:-5}" \
  "${SAMPLE_ARG[@]}"

echo "[3/4] judging..."
ARK_API_KEY="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-}}" \
  uv run python "$SCRIPT_DIR/judge.py" --input "$RESULT_DIR/qa_results.csv" --parallel 40

echo "[4/4] stats..."
uv run python "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_DIR/qa_results.csv" \
  | tee "$RESULT_DIR/summary.txt"
