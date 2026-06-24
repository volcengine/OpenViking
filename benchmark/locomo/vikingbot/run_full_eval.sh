#!/bin/bash
# LoCoMo 评测脚本
#
# Usage:
#   ./run_full_eval.sh                              # 评测全部 sample
#   ./run_full_eval.sh 0                            # 评测 sample 0 所有问题
#   ./run_full_eval.sh conv-26                      # 评测 sample_id conv-26 所有问题
#   ./run_full_eval.sh 0 2                          # 评测 sample 0 的第 2 题
#   ./run_full_eval.sh 0 --skip-import              # 跳过导入，批量评测
#   ./run_full_eval.sh 0 2 --skip-import                 # 跳过导入，单题非群聊模式（默认）
#   ./run_full_eval.sh 0 2 --group-chat                  # 单题群聊模式
#   ./run_full_eval.sh --skip-import --auto-commit  # 评测全部，跳过导入，自动提交
#   ./run_full_eval.sh --retry-wrong result/locomo_result_xxx.csv  # 只重跑错题

set -e

# --help 提前处理，避免触发 Python preflight
for arg in "$@"; do
    if [ "$arg" = "--help" ] || [ "$arg" = "-h" ]; then
        sed -n '2,10p' "$0" | sed 's/^# \?//'
        echo ""
        echo "位置参数:"
        echo "  sample_index      数字索引 (0,1,2...)"
        echo "  sample_id         样本ID (如 conv-26)"
        echo "  question_index    问题索引 (可选)，不传则测试该 sample 的所有问题"
        echo ""
        echo "开关参数:"
        echo "  --skip-import     跳过导入步骤，直接使用已导入的数据进行评测"
        echo "  --group-chat      群聊模式，使用 speaker 作为 Peer，并传 --memory-peer"
        echo "  --no-group-chat   非群聊模式（默认），使用 sample_id 作为 Peer"
        echo "  --auto-commit     自动提交未提交的代码变更，结果文件名带 commit id 和时间戳"
        echo "  --retry-wrong CSV 只重跑指定结果文件中的有效错题（导入相关对话+重新问答）"
        exit 0
    fi
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP_IMPORT=false
GROUP_CHAT=false
AUTO_COMMIT=false
RETRY_WRONG=""

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "未找到 python3/python，请先安装 Python。" >&2
    exit 1
fi

DEFAULT_OV_CONF_PATH="$($PYTHON_BIN - <<'PY'
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
print(str(path) if path is not None else str(Path.home() / ".openviking" / "ov.conf"))
PY
)"

if [ -t 0 ] && [ -t 1 ]; then
    echo "[preflight] OpenViking 配置默认路径: $DEFAULT_OV_CONF_PATH"
    printf "[preflight] 直接回车使用默认，或输入新路径 [%s]: " "$DEFAULT_OV_CONF_PATH"
    if ! read -r OV_CONF_PATH < /dev/tty; then
        OV_CONF_PATH="$DEFAULT_OV_CONF_PATH"
    fi
    if [ -z "$OV_CONF_PATH" ]; then
        OV_CONF_PATH="$DEFAULT_OV_CONF_PATH"
    fi
else
    OV_CONF_PATH="$DEFAULT_OV_CONF_PATH"
fi

if [ "$OV_CONF_PATH" = "~" ]; then
    OV_CONF_PATH="$HOME"
elif [[ "$OV_CONF_PATH" == ~/* ]]; then
    OV_CONF_PATH="$HOME/${OV_CONF_PATH#~/}"
fi

export OPENVIKING_CONFIG_FILE="$OV_CONF_PATH"
echo "[preflight] 本次使用 ov.conf: $OPENVIKING_CONFIG_FILE"

# 评测前预检配置
PRECHECK_STATUS=0
"$PYTHON_BIN" "$SCRIPT_DIR/preflight_eval_config.py" || PRECHECK_STATUS=$?
if [ "$PRECHECK_STATUS" -ne 0 ]; then
    if [ "$PRECHECK_STATUS" -eq 2 ]; then
        echo "[preflight] 已完成 OpenViking API key 初始化，请重新执行评测脚本。" >&2
    fi
    exit "$PRECHECK_STATUS"
fi

RUNTIME_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/ov_eval_runtime.XXXXXX")"
trap 'rm -f "$RUNTIME_ENV_FILE"' EXIT

if [ -t 0 ] && [ -t 1 ]; then
    INTERACTIVE=1
else
    INTERACTIVE=0
fi

INTERACTIVE="$INTERACTIVE" "$PYTHON_BIN" "$SCRIPT_DIR/preflight_eval_runtime.py" --output-env-file "$RUNTIME_ENV_FILE"
# shellcheck disable=SC1090
source "$RUNTIME_ENV_FILE"

# 解析参数
PREV_ARG=""
for arg in "$@"; do
    if [ "$PREV_ARG" = "--retry-wrong" ]; then
        RETRY_WRONG="$arg"
        PREV_ARG=""
        continue
    fi
    if [ "$arg" = "--skip-import" ]; then
        SKIP_IMPORT=true
    elif [ "$arg" = "--group-chat" ]; then
        GROUP_CHAT=true
    elif [ "$arg" = "--no-group-chat" ]; then
        GROUP_CHAT=false
    elif [ "$arg" = "--auto-commit" ]; then
        AUTO_COMMIT=true
    elif [ "$arg" = "--retry-wrong" ]; then
        PREV_ARG="$arg"
        continue
    fi
    PREV_ARG=""
done

# 过滤掉开关参数和 --retry-wrong 的值，获取位置参数
ARGS=()
SKIP_NEXT=false
for arg in "$@"; do
    if [ "$SKIP_NEXT" = "true" ]; then
        SKIP_NEXT=false
        continue
    fi
    if [ "$arg" = "--retry-wrong" ]; then
        SKIP_NEXT=true
        continue
    fi
    if [ "$arg" != "--skip-import" ] && [ "$arg" != "--group-chat" ] && [ "$arg" != "--no-group-chat" ] && [ "$arg" != "--auto-commit" ]; then
        ARGS+=("$arg")
    fi
done

# 构建通用选项
COMMON_OPTS=()
if [ "$GROUP_CHAT" = "true" ]; then
    COMMON_OPTS+=("--group-chat")
else
    COMMON_OPTS+=("--no-group-chat")
fi
IMPORT_OPTS=()
if [ -n "${OPENVIKING_API_KEY:-}" ]; then
    IMPORT_OPTS+=("--api-key" "$OPENVIKING_API_KEY" "--no-separate-user-by-sample")
fi

SAMPLE=${ARGS[0]}
QUESTION_INDEX=${ARGS[1]}
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"

# Export for inline Python usage
export SCRIPT_DIR INPUT_FILE RETRY_WRONG ACCOUNT OPENVIKING_URL OPENVIKING_API_KEY GROUP_CHAT

# auto-commit 逻辑
if [ "$AUTO_COMMIT" = "true" ]; then
    if [ -n "$(git status --porcelain)" ]; then
        echo "[auto-commit] 检测到未提交变更，正在提交..."
        git add -A
        git commit -m "auto-commit before eval $(date +%Y%m%d_%H%M%S)"
    else
        echo "[auto-commit] 工作区干净，无需提交"
    fi
fi
GIT_COMMIT_ID=$(git rev-parse --short HEAD)
TIMESTAMP=$(date +%Y%m%d%H%M%S)
IMPORT_SUCCESS_CSV="./result/import_success.csv"
IMPORT_ROW_START=0
IMPORT_PERFORMED=false

count_import_rows() {
    IMPORT_SUCCESS_CSV="$IMPORT_SUCCESS_CSV" "$PYTHON_BIN" - <<'PY'
import csv
import os
from pathlib import Path

path = Path(os.environ["IMPORT_SUCCESS_CSV"])
if not path.exists():
    print(0)
else:
    with open(path, "r", encoding="utf-8", newline="") as f:
        print(sum(1 for _ in csv.DictReader(f)))
PY
}

capture_import_row_start() {
    IMPORT_ROW_START=$(count_import_rows)
    IMPORT_PERFORMED=false
}

print_import_summary_table() {
    if [ "$SKIP_IMPORT" = "true" ] || [ "$IMPORT_PERFORMED" != "true" ]; then
        return
    fi

    echo ""
    IMPORT_SUCCESS_CSV="$IMPORT_SUCCESS_CSV" IMPORT_ROW_START="$IMPORT_ROW_START" "$PYTHON_BIN" - <<'PY'
import csv
import os
from pathlib import Path


def to_int(value: str) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def to_float(value: str) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def render_table(headers: list[str], rows: list[list[str]], align_right: set[int] | None = None) -> str:
    align_right = align_right or set()
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def format_row(row: list[str]) -> str:
        cells = []
        for i, cell in enumerate(row):
            cells.append(cell.rjust(widths[i]) if i in align_right else cell.ljust(widths[i]))
        return "| " + " | ".join(cells) + " |"

    sep = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [sep, format_row(headers), sep]
    for row in rows:
        lines.append(format_row(row))
    lines.append(sep)
    return "\n".join(lines)


path = Path(os.environ["IMPORT_SUCCESS_CSV"])
start = int(os.environ.get("IMPORT_ROW_START", "0"))
print("=== Import Summary ===")
if not path.exists():
    print("No import success CSV found.")
    raise SystemExit(0)

with open(path, "r", encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

rows = rows[start:]
if not rows:
    print("No new import records were written in this run.")
    raise SystemExit(0)

totals = {
    "sessions": len(rows),
    "embedding_tokens": 0,
    "vlm_tokens": 0,
    "cache_tokens": 0,
    "reasoning_tokens": 0,
    "llm_output_tokens": 0,
    "total_tokens": 0,
    "duration_seconds": 0.0,
}
for row in rows:
    totals["embedding_tokens"] += to_int(row.get("embedding_tokens"))
    totals["vlm_tokens"] += to_int(row.get("vlm_tokens"))
    totals["cache_tokens"] += to_int(row.get("cache_tokens"))
    totals["reasoning_tokens"] += to_int(row.get("reasoning_tokens"))
    totals["llm_output_tokens"] += to_int(row.get("llm_output_tokens"))
    totals["total_tokens"] += to_int(row.get("total_tokens"))
    totals["duration_seconds"] += to_float(row.get("duration_seconds"))

avg_duration = totals["duration_seconds"] / totals["sessions"] if totals["sessions"] else 0.0
summary_rows = [
    ["sessions", str(totals["sessions"])],
    ["embedding_tokens", str(totals["embedding_tokens"])],
    ["vlm_tokens", str(totals["vlm_tokens"])],
    ["cache_tokens", str(totals["cache_tokens"])],
    ["reasoning_tokens", str(totals["reasoning_tokens"])],
    ["llm_output_tokens", str(totals["llm_output_tokens"])],
    ["total_tokens", str(totals["total_tokens"])],
    ["total_duration_s", f"{totals['duration_seconds']:.3f}"],
    ["avg_duration_s", f"{avg_duration:.3f}"],
]
print(render_table(["metric", "value"], summary_rows, align_right={1}))
PY
}

prepare_bot_log_dir() {
    local output_file="$1"
    local base="${output_file%.csv}"
    export LOCOMO_VIKINGBOT_LOG_DIR="${base}_bot_logs"
    mkdir -p "$LOCOMO_VIKINGBOT_LOG_DIR"
    echo "[eval] vikingbot logs: $LOCOMO_VIKINGBOT_LOG_DIR"
}

# ========== 重跑错题模式（优先） ==========
if [ -n "$RETRY_WRONG" ]; then
    if [ ! -f "$RETRY_WRONG" ]; then
        echo "Error: --retry-wrong file not found: $RETRY_WRONG" >&2
        exit 1
    fi

    echo "=== 重跑错题模式 ==="
    echo "源文件: $RETRY_WRONG"

    if [ "$AUTO_COMMIT" = "true" ]; then
        RESULT_FILE="./result/locomo_retry_${TIMESTAMP}_${GIT_COMMIT_ID}.csv"
    else
        RESULT_FILE="./result/locomo_retry_${TIMESTAMP}.csv"
    fi

    # 从错题 CSV 中提取需要导入的对话（复用 import_to_ov.py 的并行逻辑）
    echo "[1/3] 导入错题相关对话..."
    capture_import_row_start
    "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
        --input "$INPUT_FILE" \
        --retry-wrong "$RETRY_WRONG" \
        --force-ingest \
        --account "$ACCOUNT" \
        --openviking-url "$OPENVIKING_URL" \
        "${IMPORT_OPTS[@]}" \
        "${COMMON_OPTS[@]}"
    IMPORT_PERFORMED=true

    echo "等待数据处理完成..."
    sleep 30

    # 评估错题
    echo "[2/3] 重新评估错题..."
    prepare_bot_log_dir "$RESULT_FILE"
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --output "$RESULT_FILE" \
        --retry-wrong "$RETRY_WRONG" \
        --threads 20 \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${COMMON_OPTS[@]}"

    # 裁判打分
    echo "[3/3] 裁判打分..."
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" --parallel 20

    # 统计结果
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_FILE"
    print_import_summary_table

    echo ""
    echo "=== 错题重跑完成 ==="
    echo "结果文件: $RESULT_FILE"
    exit 0
fi

# ========== 全量评测模式 ==========
if [ -z "$SAMPLE" ]; then
    echo "=== 全量评测模式 ==="

    if [ "$AUTO_COMMIT" = "true" ]; then
        RESULT_FILE="./result/locomo_result_${TIMESTAMP}_${GIT_COMMIT_ID}.csv"
    else
        RESULT_FILE="./result/locomo_result_${TIMESTAMP}.csv"
    fi

    # 导入数据
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/4] 跳过导入数据..."
    else
        echo "[1/4] 导入数据..."
        capture_import_row_start
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" --input "$INPUT_FILE" --force-ingest --account "$ACCOUNT" --openviking-url "$OPENVIKING_URL" "${IMPORT_OPTS[@]}" "${COMMON_OPTS[@]}"
        IMPORT_PERFORMED=true
        echo "等待 1 分钟..."
        sleep 60
    fi

    # 评估
    echo "[2/4] 评估..."
    prepare_bot_log_dir "$RESULT_FILE"
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" "$INPUT_FILE" --output "$RESULT_FILE" --config "$OPENVIKING_CONFIG_FILE" "${COMMON_OPTS[@]}"

    # 裁判打分
    echo "[3/4] 裁判打分..."
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" --parallel 40

    # 计算结果
    echo "[4/4] 计算结果..."
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_FILE"
    print_import_summary_table

    echo ""
    echo "=== 全量评测完成 ==="
    echo "结果文件: $RESULT_FILE"
    exit 0
fi

# ========== 单 sample 评测模式 ==========
# 判断是数字还是 sample_id
if [[ "$SAMPLE" =~ ^-?[0-9]+$ ]]; then
    SAMPLE_INDEX=$SAMPLE
    SAMPLE_ID_FOR_CMD=$SAMPLE_INDEX
    echo "Using sample index: $SAMPLE_INDEX"
else
    SAMPLE_INDEX=$(SAMPLE="$SAMPLE" INPUT_FILE="$INPUT_FILE" "$PYTHON_BIN" - <<'PY'
import json
import os

sample = os.environ["SAMPLE"]
input_file = os.environ["INPUT_FILE"]

with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

for i, s in enumerate(data):
    if s.get("sample_id") == sample:
        print(i)
        break
else:
    print("NOT_FOUND")
PY
)
    if [ "$SAMPLE_INDEX" = "NOT_FOUND" ]; then
        echo "Error: sample_id '$SAMPLE' not found"
        exit 1
    fi
    SAMPLE_ID_FOR_CMD=$SAMPLE
    echo "Using sample_id: $SAMPLE (index: $SAMPLE_INDEX)"
fi

# 判断是单题模式还是批量模式
if [ -n "$QUESTION_INDEX" ]; then
    # ========== 单题模式 ==========
    echo "=== 单题模式: sample $SAMPLE, question $QUESTION_INDEX ==="

    # 导入对话
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/3] Skipping import (--skip-import)"
    else
        echo "[1/3] Importing sample $SAMPLE_INDEX, question $QUESTION_INDEX..."
        capture_import_row_start
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --question-index "$QUESTION_INDEX" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL" \
            "${IMPORT_OPTS[@]}" \
            "${COMMON_OPTS[@]}"
        IMPORT_PERFORMED=true

        echo "Waiting for data processing..."
        sleep 3
    fi

    # 运行评测
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/2] Running evaluation (skip-import mode)..."
    else
        echo "[2/3] Running evaluation..."
    fi
    if [ "$AUTO_COMMIT" = "true" ]; then
        OUTPUT_FILE=./result/locomo_${SAMPLE}_${QUESTION_INDEX}_result_${TIMESTAMP}_${GIT_COMMIT_ID}.csv
    else
        OUTPUT_FILE=./result/locomo_${SAMPLE}_${QUESTION_INDEX}_result_${TIMESTAMP}.csv
    fi
    prepare_bot_log_dir "$OUTPUT_FILE"
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --sample "$SAMPLE_ID_FOR_CMD" \
        --question-index "$QUESTION_INDEX" \
        --count 1 \
        --output "$OUTPUT_FILE" \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${COMMON_OPTS[@]}"

    # 运行 Judge 评分
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[2/2] Running judge..."
    else
        echo "[3/3] Running judge..."
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$OUTPUT_FILE" --parallel 1

    # 输出结果
    echo ""
    echo "=== 评测结果 ==="
    print_import_summary_table
    OUTPUT_FILE="$OUTPUT_FILE" QUESTION_INDEX="$QUESTION_INDEX" "$PYTHON_BIN" - <<'PY'
import csv
import json
import os

question_index = int(os.environ["QUESTION_INDEX"])
output_file = os.environ["OUTPUT_FILE"]

with open(output_file, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

row = None
for r in rows:
    if int(r.get("question_index", -1)) == question_index:
        row = r
        break

if row is None:
    row = rows[-1]

evidence_text = json.loads(row.get("evidence_text", "[]"))
evidence_str = "\n".join(evidence_text) if evidence_text else ""

print(f"问题: {row['question']}")
print(f"期望答案: {row['answer']}")
print(f"模型回答: {row['response']}")
print(f"证据原文:\n{evidence_str}")
print(f"结果: {row.get('result', 'N/A')}")
print(f"原因: {row.get('reasoning', 'N/A')}")
PY

else
    # ========== 批量模式 ==========
    echo "=== 批量模式: sample $SAMPLE, 所有问题 ==="

    # 获取该 sample 的问题数量
    QUESTION_COUNT=$(SAMPLE_INDEX="$SAMPLE_INDEX" INPUT_FILE="$INPUT_FILE" "$PYTHON_BIN" - <<'PY'
import json
import os

sample_index = int(os.environ["SAMPLE_INDEX"])
input_file = os.environ["INPUT_FILE"]

with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

sample = data[sample_index]
print(len(sample.get("qa", [])))
PY
)
    echo "Found $QUESTION_COUNT questions for sample $SAMPLE"

    # 导入所有 sessions
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/4] Skipping import (--skip-import)"
    else
        echo "[1/4] Importing all sessions for sample $SAMPLE_INDEX..."
        capture_import_row_start
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL" \
            "${IMPORT_OPTS[@]}" \
            "${COMMON_OPTS[@]}"
        IMPORT_PERFORMED=true

        echo "Waiting for data processing..."
        sleep 10
    fi

    # 运行评测（所有问题）
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/3] Running evaluation for all questions (skip-import mode)..."
    else
        echo "[2/4] Running evaluation for all questions..."
    fi
    if [ "$AUTO_COMMIT" = "true" ]; then
        OUTPUT_FILE=./result/locomo_${SAMPLE}_result_${TIMESTAMP}_${GIT_COMMIT_ID}.csv
    else
        OUTPUT_FILE=./result/locomo_${SAMPLE}_result_${TIMESTAMP}.csv
    fi
    prepare_bot_log_dir "$OUTPUT_FILE"
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --sample "$SAMPLE_ID_FOR_CMD" \
        --output "$OUTPUT_FILE" \
        --threads 10 \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${COMMON_OPTS[@]}"

    # 运行 Judge 评分
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[2/3] Running judge..."
    else
        echo "[3/4] Running judge..."
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$OUTPUT_FILE" --parallel 40

    # 输出统计结果
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[3/3] Calculating statistics..."
    else
        echo "[4/4] Calculating statistics..."
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$OUTPUT_FILE"
    print_import_summary_table

    echo ""
    echo "=== 批量评测完成 ==="
    echo "结果文件: $OUTPUT_FILE"
fi
