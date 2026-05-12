#!/bin/bash
#
# LoCoMo evaluation: end-to-end via Claude Code auto-capture (stream-json).
#
# Mirrors a real CC user behaviour:
#   - Each LoCoMo session is fed turn-by-turn into one `claude -p`
#     subprocess (stream-json input). Plugin's auto-capture (Stop hook) and
#     session-end (SessionEnd hook) push memories into OpenViking. The
#     benchmark's auto-capture wrapper adds per-message `created_at` so
#     event archive dates line up with conv timestamps.
#   - After ingest, snapshot HOME + OV data.
#   - QA loops over conv-* in series; before each conv we restore the OV
#     data + HOME from the post-ingest snapshot, so accumulated capture
#     during QA does not leak across convs.
#
# Required env:
#   ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY
#   ANTHROPIC_BASE_URL
#   ANTHROPIC_MODEL
#   OPENVIKING_PLUGIN_DIR           - path to claude-code-memory-plugin
#   OPENVIKING_DATA_DIR             - default: ~/.openviking/data
#   OPENVIKING_SERVER_TMUX          - tmux session name running openviking-server
#                                     (default "ovserver"); used to Ctrl-C/restart
#   OPENVIKING_CLI_CONFIG_FILE      - optional ovcli.conf path

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

: "${OPENVIKING_PLUGIN_DIR:?set to claude-code-memory-plugin dir}"
export LOCOMO_BENCHMARK_DIR="$SCRIPT_DIR"

OV_DATA_DIR="${OPENVIKING_DATA_DIR:-$HOME/.openviking/data}"
OV_TMUX="${OPENVIKING_SERVER_TMUX:-ovserver}"

INPUT="${LOCOMO_INPUT:-$SCRIPT_DIR/.tmp/locomo10.json}"
PROJECT_DIR="$SCRIPT_DIR/.tmp/locomo-eval-e2e"
HOME_DIR="$SCRIPT_DIR/.tmp/claude-eval-home-e2e"
RESULT_DIR="$SCRIPT_DIR/.tmp/result-e2e"
SNAP_DIR="$SCRIPT_DIR/.tmp/snapshots-e2e"
mkdir -p "$RESULT_DIR" "$SNAP_DIR" "$PROJECT_DIR"

server_health() {
  curl -sf http://127.0.0.1:1933/api/v1/observer/models >/dev/null
}

restart_server() {
  if ! tmux has-session -t "$OV_TMUX" 2>/dev/null; then
    echo "ERROR: tmux session '$OV_TMUX' missing; start openviking-server in it first" >&2
    exit 1
  fi
  tmux send-keys -t "$OV_TMUX" C-c
  sleep 3
  tmux send-keys -t "$OV_TMUX" "openviking-server" Enter
  local i=0
  until server_health; do
    sleep 2; i=$((i+2))
    if [ $i -gt 30 ]; then echo "ERROR: server didn't come up in 30s" >&2; exit 1; fi
  done
}

server_health || { echo "ERROR: openviking-server not responding on :1933" >&2; exit 1; }

# ---- Phase 1: ingest ------------------------------------------------------
echo "[1/5] e2e ingest via stream-json..."
SAMPLE_ARG=()
[ $# -ge 1 ] && SAMPLE_ARG=(--sample "$1")

uv run python "$SCRIPT_DIR/ingest_e2e.py" \
  --input "$INPUT" \
  --project-dir "$PROJECT_DIR" --home "$HOME_DIR" \
  --record "$RESULT_DIR/.ingest_record.json" \
  --success-csv "$RESULT_DIR/ingest_success.csv" \
  --error-log "$RESULT_DIR/ingest_errors.log" \
  --hooks-settings "$SCRIPT_DIR/config/ov-hooks.json" \
  --mcp-config "$SCRIPT_DIR/config/ov-mcp.json" \
  --ov-config "$SCRIPT_DIR/config/ov-ingest.conf" \
  ${OPENVIKING_CLI_CONFIG_FILE:+--ov-cli-config "$OPENVIKING_CLI_CONFIG_FILE"} \
  --ov-shared-id "" \
  --parallel "${INGEST_PARALLEL:-3}" \
  --timeout 1200 \
  "${SAMPLE_ARG[@]}" 2>&1 | tee "$RESULT_DIR/ingest.log"

# ---- Phase 2: snapshot ----------------------------------------------------
echo "[2/5] snapshot HOME + OV data..."
tmux send-keys -t "$OV_TMUX" C-c; sleep 3
rm -rf "$SNAP_DIR/data.postingest" "$SNAP_DIR/HOME.postingest"
cp -r "$OV_DATA_DIR" "$SNAP_DIR/data.postingest"
cp -r "$HOME_DIR" "$SNAP_DIR/HOME.postingest"
tmux send-keys -t "$OV_TMUX" "openviking-server" Enter
sleep 8
server_health && echo "  server back up"

# ---- Phase 3: QA loop with per-conv restore ------------------------------
SAMPLES=(conv-26 conv-30 conv-41 conv-42 conv-43 conv-44 conv-47 conv-48 conv-49 conv-50)
if [ $# -ge 1 ]; then SAMPLES=("$1"); fi

for SID in "${SAMPLES[@]}"; do
  OUT="$RESULT_DIR/qa_results_${SID}.csv"
  if [ -f "$OUT" ]; then
    echo "[$SID] CSV exists ($(tail -n +2 "$OUT" | wc -l | tr -d ' ') rows); skipping"
    continue
  fi

  echo "[$SID] restoring HOME + OV data from snapshot..."
  rm -rf "$HOME_DIR"; cp -r "$SNAP_DIR/HOME.postingest" "$HOME_DIR"
  tmux send-keys -t "$OV_TMUX" C-c; sleep 3
  rm -rf "$OV_DATA_DIR"; cp -r "$SNAP_DIR/data.postingest" "$OV_DATA_DIR"
  tmux send-keys -t "$OV_TMUX" "openviking-server" Enter
  i=0; until server_health; do sleep 2; i=$((i+2)); [ $i -gt 30 ] && exit 1; done

  echo "[$SID] running eval.py..."
  uv run python "$SCRIPT_DIR/eval.py" \
    --input "$INPUT" --output "$OUT" \
    --project-root "$PROJECT_DIR" --home "$HOME_DIR" --shared-cwd \
    --sample "$SID" --parallel "${QA_PARALLEL:-3}" \
    --hooks-settings "$SCRIPT_DIR/config/ov-hooks.json" \
    --mcp-config "$SCRIPT_DIR/config/ov-mcp.json" \
    --ov-config "$SCRIPT_DIR/config/ov-ingest.conf" \
    --ov-shared-id "" \
    ${OPENVIKING_CLI_CONFIG_FILE:+--ov-cli-config "$OPENVIKING_CLI_CONFIG_FILE"} \
    2>&1 | tee "$RESULT_DIR/qa_${SID}.log"
done

# ---- Phase 4: merge + judge ----------------------------------------------
echo "[4/5] merging per-sample CSVs + judging..."
MERGED="$RESULT_DIR/qa_results.csv"
shopt -s nullglob
PER_SAMPLE=( "$RESULT_DIR"/qa_results_conv-*.csv )
head -1 "${PER_SAMPLE[0]}" > "$MERGED"
for f in "${PER_SAMPLE[@]}"; do tail -n +2 "$f" >> "$MERGED"; done

ARK_API_KEY="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-}}" \
  uv run python "$SCRIPT_DIR/judge.py" --input "$MERGED" --parallel 40

echo "[5/5] stats..."
uv run python "$SCRIPT_DIR/stat_judge_result.py" --input "$MERGED" \
  | tee "$RESULT_DIR/summary.txt"
