#!/bin/bash
# Run each LoCoMo conversation as its own VikingBot eval.
#
# This script does not edit ov.conf and does not restart OpenViking.
# Baseline vs feature is whichever server is already running.
# --arm only labels the manifest so the two sweeps stay distinguishable.
#
# Prerequisite: benchmark/locomo/data/locomo10.json
# Server file must already contain vlm, query_planner, embedding, and
# memory.topic_continuity. Those sections are read at OpenViking startup.
#
# Usage:
#   export OPENVIKING_CONFIG_FILE=~/.openviking/ov-locomo-baseline.conf
#   ./run_each_conv.sh --arm baseline
#   ./run_each_conv.sh --arm baseline conv-26
#   ./run_each_conv.sh --arm feature --from conv-41
#   ./run_each_conv.sh --arm baseline -- --parallel-run-eval 20
#
# Flags:
#   --arm NAME          Label written into the suite manifest. Does not
#                       change the server. Default: unlabeled
#   --from SAMPLE       Skip conversations before this sample_id or index
#   --keep-runs N       Passed to run_full_eval.sh. Default 40, so a
#                       10-conversation sweep is not deleted by the
#                       per-run cleanup (that script defaults to 10)
#   --dry-run           Print the conversations that would run
#   --                  Remaining args are forwarded to run_full_eval.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RESULTS_ROOT="$REPO_ROOT/result/locomo"
ARM="unlabeled"
FROM_SAMPLE=""
KEEP_RUNS="40"
DRY_RUN=false
FORWARD=()

usage() {
    sed -n '2,27p' "$0" | sed 's/^# \?//'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --arm)
            ARM="${2:?--arm requires a name}"
            shift 2
            ;;
        --from)
            FROM_SAMPLE="${2:?--from requires a sample_id or index}"
            shift 2
            ;;
        --keep-runs)
            KEEP_RUNS="${2:?--keep-runs requires a positive integer}"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --)
            shift
            FORWARD=("$@")
            break
            ;;
        --*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ -n "${ONLY_SAMPLE:-}" ]; then
                echo "Only one sample may be given: $1" >&2
                exit 1
            fi
            ONLY_SAMPLE="$1"
            shift
            ;;
    esac
done

if ! [[ "$KEEP_RUNS" =~ ^[1-9][0-9]*$ ]]; then
    echo "--keep-runs requires a positive integer" >&2
    exit 1
fi

if [ ! -f "$INPUT_FILE" ]; then
    echo "Missing $INPUT_FILE" >&2
    echo "Download https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json into that path." >&2
    exit 1
fi

if [ -z "${OPENVIKING_CONFIG_FILE:-}" ]; then
    OPENVIKING_CONFIG_FILE="${HOME}/.openviking/ov.conf"
fi
case "$OPENVIKING_CONFIG_FILE" in
    "~") OPENVIKING_CONFIG_FILE="$HOME" ;;
    "~/"*) OPENVIKING_CONFIG_FILE="$HOME/${OPENVIKING_CONFIG_FILE#~/}" ;;
esac
export OPENVIKING_CONFIG_FILE

if [ ! -f "$OPENVIKING_CONFIG_FILE" ]; then
    echo "Config file not found: $OPENVIKING_CONFIG_FILE" >&2
    echo "Set OPENVIKING_CONFIG_FILE to the same file the server was started with." >&2
    exit 1
fi

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "python3 or python is required" >&2
    exit 1
fi

CONFIG_SUMMARY="$(
    OPENVIKING_CONFIG_FILE="$OPENVIKING_CONFIG_FILE" "$PYTHON_BIN" - <<'PY'
import json
import os

path = os.environ["OPENVIKING_CONFIG_FILE"]
with open(path, "r", encoding="utf-8") as f:
    config = json.load(f)

memory = config.get("memory") or {}
topic = memory.get("topic_continuity") or {}
bot_ov = ((config.get("bot") or {}).get("ov_server") or {})
vlm = config.get("vlm") or {}
planner = config.get("query_planner") or {}
embedding = ((config.get("embedding") or {}).get("dense") or {})
workspace = (config.get("storage") or {}).get("workspace", "")
enabled = topic.get("enabled", False)
print(f"topic_continuity.enabled={str(bool(enabled)).lower()}")
print(f"topic_continuity.max_turns={topic.get('max_turns', 10)}")
print(f"memory_recall_midterm_limit={bot_ov.get('memory_recall_midterm_limit', 10)}")
print(f"vlm.model={vlm.get('model', '')}")
print(f"vlm.thinking={str(bool(vlm.get('thinking', False))).lower()}")
print(f"query_planner.model={planner.get('model', '')}")
print(f"query_planner.thinking={str(bool(planner.get('thinking', False))).lower()}")
print(f"embedding.model={embedding.get('model', '')}")
print(f"embedding.dimension={embedding.get('dimension', '')}")
print(f"storage.workspace={workspace}")
PY
)"

mapfile -t SAMPLE_IDS < <(
    INPUT_FILE="$INPUT_FILE" "$PYTHON_BIN" - <<'PY'
import json
import os

with open(os.environ["INPUT_FILE"], "r", encoding="utf-8") as f:
    data = json.load(f)
for item in data:
    sample_id = item.get("sample_id")
    if not sample_id:
        raise SystemExit("locomo10.json entry is missing sample_id")
    print(sample_id)
PY
)

if [ "${#SAMPLE_IDS[@]}" -eq 0 ]; then
    echo "No conversations found in $INPUT_FILE" >&2
    exit 1
fi

resolve_sample() {
    local token="$1"
    local i
    if [[ "$token" =~ ^[0-9]+$ ]]; then
        if [ "$token" -ge "${#SAMPLE_IDS[@]}" ]; then
            echo "Sample index $token is out of range (0-$((${#SAMPLE_IDS[@]} - 1)))" >&2
            exit 1
        fi
        echo "${SAMPLE_IDS[$token]}"
        return
    fi
    for i in "${!SAMPLE_IDS[@]}"; do
        if [ "${SAMPLE_IDS[$i]}" = "$token" ]; then
            echo "$token"
            return
        fi
    done
    echo "Unknown sample: $token" >&2
    exit 1
}

SELECTED=()
if [ -n "${ONLY_SAMPLE:-}" ]; then
    SELECTED+=("$(resolve_sample "$ONLY_SAMPLE")")
else
    started=false
    if [ -z "$FROM_SAMPLE" ]; then
        started=true
    else
        FROM_SAMPLE="$(resolve_sample "$FROM_SAMPLE")"
    fi
    for sample_id in "${SAMPLE_IDS[@]}"; do
        if [ "$started" = false ]; then
            if [ "$sample_id" = "$FROM_SAMPLE" ]; then
                started=true
            else
                continue
            fi
        fi
        SELECTED+=("$sample_id")
    done
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SUITE_DIR="$RESULTS_ROOT/suites/${ARM}_${TIMESTAMP}"
mkdir -p "$SUITE_DIR"

{
    echo "arm: $ARM"
    echo "config_file: $OPENVIKING_CONFIG_FILE"
    echo "input: $INPUT_FILE"
    echo "started: $TIMESTAMP"
    echo
    echo "$CONFIG_SUMMARY"
    echo
    echo "sample_id	run_dir	status"
} > "$SUITE_DIR/manifest.txt"

echo "Suite:  $SUITE_DIR"
echo "Config: $OPENVIKING_CONFIG_FILE"
echo "$CONFIG_SUMMARY" | sed 's/^/  /'
echo "Arm label: $ARM (this does not change the running server)"
echo "Conversations: ${SELECTED[*]}"

if [ "$DRY_RUN" = true ]; then
    exit 0
fi

index=0
total="${#SELECTED[@]}"
for sample_id in "${SELECTED[@]}"; do
    index=$((index + 1))
    echo
    echo "========== [$index/$total] $sample_id =========="
    set +e
    "$SCRIPT_DIR/run_full_eval.sh" \
        "$sample_id" \
        --keep-runs "$KEEP_RUNS" \
        "${FORWARD[@]}" \
        < /dev/null
    status=$?
    set -e
    run_dir=""
    if [ -f "$RESULTS_ROOT/.latest_run" ]; then
        run_dir="$(cat "$RESULTS_ROOT/.latest_run")"
    fi
    if [ "$status" -eq 0 ]; then
        printf '%s\t%s\tok\n' "$sample_id" "$run_dir" >> "$SUITE_DIR/manifest.txt"
        echo "Finished $sample_id -> $run_dir"
    else
        printf '%s\t%s\tfailed:%s\n' "$sample_id" "$run_dir" "$status" >> "$SUITE_DIR/manifest.txt"
        echo "Stopped on $sample_id (exit $status)." >&2
        echo "Resume with: OPENVIKING_CONFIG_FILE=$OPENVIKING_CONFIG_FILE $0 --arm $ARM --from $sample_id" >&2
        exit "$status"
    fi
done

echo
echo "Suite complete: $SUITE_DIR/manifest.txt"
