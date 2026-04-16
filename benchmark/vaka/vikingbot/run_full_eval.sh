#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="data/vaka_locomo.csv"
OUTPUT_FILE="$SCRIPT_DIR/result/vaka_qa_result.csv"
OPENVIKING_URL="http://localhost:1933"
OPENVIKING_ACCOUNT="default"
OPENVIKING_USER_ID="default"
OPENVIKING_AGENT_ID="default"
MEMORY_SESSIONS="1-70"
EVAL_SESSIONS="71-"
PARALLEL=10
PYTHON_BIN="${PYTHON_BIN:-python3}"
SKIP_IMPORT=false
SKIP_PREPARE=false
SKIP_JUDGE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)
            INPUT_FILE="$2"
            shift 2
            ;;
        --output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --openviking-url)
            OPENVIKING_URL="$2"
            shift 2
            ;;
        --account)
            OPENVIKING_ACCOUNT="$2"
            shift 2
            ;;
        --user-id)
            OPENVIKING_USER_ID="$2"
            shift 2
            ;;
        --agent-id)
            OPENVIKING_AGENT_ID="$2"
            shift 2
            ;;
        --memory-sessions)
            MEMORY_SESSIONS="$2"
            shift 2
            ;;
        --eval-sessions)
            EVAL_SESSIONS="$2"
            shift 2
            ;;
        --parallel)
            PARALLEL="$2"
            shift 2
            ;;
        --python)
            PYTHON_BIN="$2"
            shift 2
            ;;
        --skip-import)
            SKIP_IMPORT=true
            shift
            ;;
        --skip-prepare)
            SKIP_PREPARE=true
            shift
            ;;
        --skip-judge)
            SKIP_JUDGE=true
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--input CSV] [--output CSV] [--openviking-url URL] [--account ACCOUNT] [--user-id USER] [--agent-id AGENT] [--memory-sessions RANGE] [--eval-sessions RANGE] [--parallel N] [--python PYTHON_BIN] [--skip-import] [--skip-prepare] [--skip-judge]"
            exit 1
            ;;
    esac
done

read -r -a PYTHON_CMD <<< "$PYTHON_BIN"

mkdir -p "$(dirname "$OUTPUT_FILE")"

if [[ "$SKIP_IMPORT" != "true" ]]; then
    echo "[1/4] Importing Vaka memory sessions to OpenViking..."
    "${PYTHON_CMD[@]}" "$SCRIPT_DIR/import_to_ov.py" \
        --input "$INPUT_FILE" \
        --openviking-url "$OPENVIKING_URL" \
        --account "$OPENVIKING_ACCOUNT" \
        --user-id "$OPENVIKING_USER_ID" \
        --agent-id "$OPENVIKING_AGENT_ID" \
        --memory-sessions "$MEMORY_SESSIONS"
else
    echo "[1/4] Skipping import..."
fi

if [[ "$SKIP_PREPARE" != "true" ]]; then
    echo "[2/4] Preparing Vaka eval rows..."
    "${PYTHON_CMD[@]}" "$SCRIPT_DIR/run_eval.py" "$INPUT_FILE" \
        --output "$OUTPUT_FILE" \
        --memory-sessions "$MEMORY_SESSIONS" \
        --eval-sessions "$EVAL_SESSIONS"
else
    echo "[2/4] Skipping prepare..."
fi

if [[ "$SKIP_JUDGE" != "true" ]]; then
    echo "[3/4] Judging..."
    "${PYTHON_CMD[@]}" "$SCRIPT_DIR/judge.py" --input "$OUTPUT_FILE" --parallel "$PARALLEL"
else
    echo "[3/4] Skipping judge..."
fi

echo "[4/4] Calculating statistics..."
"${PYTHON_CMD[@]}" "$SCRIPT_DIR/stat_judge_result.py" --input "$OUTPUT_FILE"

echo "Done. Result file: $OUTPUT_FILE"
