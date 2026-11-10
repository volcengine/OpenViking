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
#   ./run_full_eval.sh --parallel-import-sessions 20 0 1  # 覆盖默认 session 导入并发数
#   ./run_full_eval.sh --parallel-import-sessions 50 --parallel-run-eval 20 --parallel-judge 40  # 分别设置导入、评测和裁判并发数
#   ./run_full_eval.sh --keep-runs 20               # 保留最近 20 次实验（默认 10）

set -e

UI_RESET=""
UI_BOLD=""
UI_DIM=""
UI_RED=""
UI_GREEN=""
UI_YELLOW=""
UI_CYAN=""
if [ -t 1 ] && [ "${TERM:-dumb}" != "dumb" ] && [ -z "${NO_COLOR+x}" ]; then
    UI_RESET=$'\033[0m'
    UI_BOLD=$'\033[1m'
    UI_DIM=$'\033[2m'
    UI_RED=$'\033[31m'
    UI_GREEN=$'\033[32m'
    UI_YELLOW=$'\033[33m'
    UI_CYAN=$'\033[36m'
fi

ui_banner() {
    printf "\n%b%s%b\n" "${UI_BOLD}${UI_CYAN}" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" "$UI_RESET"
    printf "%b  %s%b\n" "${UI_BOLD}" "$1" "$UI_RESET"
    printf "%b%s%b\n" "${UI_BOLD}${UI_CYAN}" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" "$UI_RESET"
}

ui_section() {
    printf "\n%b%s%b\n" "${UI_BOLD}${UI_CYAN}" "━━ $1" "$UI_RESET"
}

ui_info() {
    printf "  %b│ %s%b\n" "$UI_DIM" "$1" "$UI_RESET"
}

ui_success() {
    printf "  %b✓%b %s\n" "$UI_GREEN" "$UI_RESET" "$1"
}

ui_warn() {
    printf "  %b!%b %s\n" "$UI_YELLOW" "$UI_RESET" "$1"
}

ui_error() {
    printf "  %b✗%b %s\n" "$UI_RED" "$UI_RESET" "$1" >&2
}

ui_kv() {
    printf "  %b%s:%b %s\n" "$UI_DIM" "$1" "$UI_RESET" "$2"
}

ui_step() {
    local current="$1"
    local total="$2"
    local title="$3"
    printf "\n  %b▶ [%s/%s] %s%b\n" "${UI_BOLD}${UI_CYAN}" "$current" "$total" "$title" "$UI_RESET"
}

# --help 提前处理，避免触发 Python preflight
for arg in "$@"; do
    if [ "$arg" = "--help" ] || [ "$arg" = "-h" ]; then
        sed -n '2,17p' "$0" | sed 's/^# \?//'
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
        echo "  --parallel-import-sessions N  导入 session 并发数（默认 50）"
        echo "  --parallel-run-eval N         run_eval 并发线程数（默认 100）"
        echo "  --parallel-judge N            judge 并发请求数（默认 100）"
        echo "  --keep-runs N                 保留最近 N 次实验目录（默认 10）"
        exit 0
    fi
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP_IMPORT=false
GROUP_CHAT=false
AUTO_COMMIT=false
RETRY_WRONG=""
KEEP_RUNS=10
PARALLEL_IMPORT_SESSIONS="50"
PARALLEL_RUN_EVAL="100"
PARALLEL_JUDGE="100"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    ui_error "未找到 python3/python，请先安装 Python。"
    exit 1
fi

# 实验输出目录：result/locomo/runs/<timestamp>[_<commit>]
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RESULTS_ROOT="$REPO_ROOT/result/locomo"
mkdir -p "$RESULTS_ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

RUN_DIR_NAME="${TIMESTAMP}"
RUN_DIR="$RESULTS_ROOT/runs/$RUN_DIR_NAME"
mkdir -p "$RUN_DIR"
echo "$RUN_DIR" > "$RESULTS_ROOT/.latest_run"

ui_banner "LoCoMo · VikingBot Evaluation"
ui_kv "实验目录" "$RUN_DIR"

ui_section "1. 环境预检"

DEFAULT_OV_CONF_PATH="$($PYTHON_BIN - <<'PY'
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
print(str(path) if path is not None else str(Path.home() / ".openviking" / "ov.conf"))
PY
)"

if [ -t 0 ] && [ -t 1 ]; then
    ui_kv "默认配置" "$DEFAULT_OV_CONF_PATH"
    printf "\n  %b?%b 请选择 OpenViking 配置文件\n" "$UI_YELLOW" "$UI_RESET"
    printf "    %b直接回车使用默认路径%b\n" "$UI_DIM" "$UI_RESET"
    printf "    %b>%b " "$UI_GREEN" "$UI_RESET"
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
printf "\n"
ui_kv "本次配置" "$OPENVIKING_CONFIG_FILE"
ui_info "正在检查本地配置…"

# 评测前预检配置
PRECHECK_STATUS=0
"$PYTHON_BIN" "$SCRIPT_DIR/preflight_eval_config.py" || PRECHECK_STATUS=$?
if [ "$PRECHECK_STATUS" -ne 0 ]; then
    if [ "$PRECHECK_STATUS" -eq 2 ]; then
        ui_warn "已完成 OpenViking API key 初始化，请重新执行评测脚本。"
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
ui_success "环境预检完成"

# 解析参数
PREV_ARG=""
for arg in "$@"; do
    if [ "$PREV_ARG" = "--retry-wrong" ]; then
        RETRY_WRONG="$arg"
        PREV_ARG=""
        continue
    fi
    if [ "$PREV_ARG" = "--parallel-import-sessions" ]; then
        PARALLEL_IMPORT_SESSIONS="$arg"
        PREV_ARG=""
        continue
    fi
    if [ "$PREV_ARG" = "--parallel-run-eval" ]; then
        PARALLEL_RUN_EVAL="$arg"
        PREV_ARG=""
        continue
    fi
    if [ "$PREV_ARG" = "--parallel-judge" ]; then
        PARALLEL_JUDGE="$arg"
        PREV_ARG=""
        continue
    fi
    if [ "$PREV_ARG" = "--keep-runs" ]; then
        KEEP_RUNS="$arg"
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
    elif [ "$arg" = "--parallel-import-sessions" ] || [ "$arg" = "--parallel-run-eval" ] || [ "$arg" = "--parallel-judge" ] || [ "$arg" = "--keep-runs" ]; then
        PREV_ARG="$arg"
        continue
    fi
    PREV_ARG=""
done
if [ -n "$PREV_ARG" ]; then
    ui_error "$PREV_ARG requires a value"
    exit 1
fi

# auto-commit runs AFTER arg parsing so --auto-commit is actually honored, and
# GIT_COMMIT_ID is captured AFTER the commit so run metadata records the real HEAD.
if [ "$AUTO_COMMIT" = "true" ]; then
    if [ -n "$(cd "$SCRIPT_DIR/../../.." && git status --porcelain)" ]; then
        ui_info "检测到未提交变更，正在自动提交…"
        (cd "$SCRIPT_DIR/../../.." && git add -A && git commit -m "auto-commit before eval $(date +%Y%m%d_%H%M%S)")
    fi
fi
GIT_COMMIT_ID=$(cd "$SCRIPT_DIR/../../.." && git rev-parse --short HEAD 2>/dev/null || echo "nogit")

# 过滤掉开关参数和带值参数，获取位置参数
ARGS=()
SKIP_NEXT=false
for arg in "$@"; do
    if [ "$SKIP_NEXT" = "true" ]; then
        SKIP_NEXT=false
        continue
    fi
    if [ "$arg" = "--retry-wrong" ] || [ "$arg" = "--parallel-import-sessions" ] || [ "$arg" = "--parallel-run-eval" ] || [ "$arg" = "--parallel-judge" ] || [ "$arg" = "--keep-runs" ]; then
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
    IMPORT_OPTS+=("--api-key" "$OPENVIKING_API_KEY" "--auth-mode" "${OPENVIKING_AUTH_MODE:-api_key}")
    if [ "${OPENVIKING_AUTH_MODE:-api_key}" = "trusted" ]; then
        IMPORT_OPTS+=("--user" "${OPENVIKING_USER:-default}")
    fi
    IMPORT_OPTS+=("--no-separate-user-by-sample")
fi
if [ -n "${PARALLEL_IMPORT_SESSIONS:-}" ]; then
    if ! [[ "$PARALLEL_IMPORT_SESSIONS" =~ ^[1-9][0-9]*$ ]]; then
        ui_error "--parallel-import-sessions requires a positive integer"
        exit 1
    fi
    IMPORT_OPTS+=("--parallel-sessions" "$PARALLEL_IMPORT_SESSIONS")
fi
if ! [[ "$PARALLEL_RUN_EVAL" =~ ^[1-9][0-9]*$ ]]; then
    ui_error "--parallel-run-eval requires a positive integer"
    exit 1
fi
if ! [[ "$PARALLEL_JUDGE" =~ ^[1-9][0-9]*$ ]]; then
    ui_error "--parallel-judge requires a positive integer"
    exit 1
fi
if ! [[ "$KEEP_RUNS" =~ ^[1-9][0-9]*$ ]]; then
    ui_error "--keep-runs requires a positive integer"
    exit 1
fi
RUN_EVAL_OPTS=("--threads" "$PARALLEL_RUN_EVAL")
JUDGE_OPTS=("--parallel" "$PARALLEL_JUDGE")

SAMPLE=${ARGS[0]}
QUESTION_INDEX=${ARGS[1]}
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"

# 实验目录内的输出文件
RESULT_BASENAME="locomo_result"
RESULT_FILE="$RUN_DIR/${RESULT_BASENAME}.csv"
IMPORT_SUCCESS_CSV="$RUN_DIR/import_success.csv"
BOT_LOG_DIR="$RUN_DIR/${RESULT_BASENAME}_bot_logs"
MEMORY_SNAPSHOT_DIR="$RUN_DIR/memories"

ui_section "2. 运行配置"
ui_kv "配置文件" "$OPENVIKING_CONFIG_FILE"
ui_kv "OpenViking" "$OPENVIKING_URL"
ui_kv "运行身份" "account=$ACCOUNT · user=$OPENVIKING_USER · auth=$OPENVIKING_AUTH_MODE"
ui_kv "会话模式" "$([ "$GROUP_CHAT" = "true" ] && printf '群聊' || printf '非群聊')"
ui_kv "导入并发" "$PARALLEL_IMPORT_SESSIONS sessions"
ui_kv "评测并发" "$PARALLEL_RUN_EVAL threads"
ui_kv "裁判并发" "$PARALLEL_JUDGE requests"
ui_kv "导入策略" "$([ "$SKIP_IMPORT" = "true" ] && printf '跳过导入' || printf '强制导入')"
ui_kv "保留实验数" "$KEEP_RUNS"

# Export for inline Python usage
export SCRIPT_DIR INPUT_FILE RETRY_WRONG PARALLEL_IMPORT_SESSIONS ACCOUNT OPENVIKING_URL OPENVIKING_API_KEY OPENVIKING_USER OPENVIKING_AUTH_MODE GROUP_CHAT
export IMPORT_SUCCESS_CSV BOT_LOG_DIR MEMORY_SNAPSHOT_DIR RUN_DIR RESULTS_ROOT

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

    ui_section "导入摘要"
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
    mkdir -p "$BOT_LOG_DIR"
    export LOCOMO_VIKINGBOT_LOG_DIR="$BOT_LOG_DIR"
    ui_kv "VikingBot 日志" "$BOT_LOG_DIR"
}

# 保存运行元信息
write_run_metadata() {
    cat > "$RUN_DIR/run_metadata.txt" <<EOF
timestamp: $TIMESTAMP
git_commit: $GIT_COMMIT_ID
config_file: $OPENVIKING_CONFIG_FILE
openviking_url: $OPENVIKING_URL
skip_import: $SKIP_IMPORT
group_chat: $GROUP_CHAT
parallel_import_sessions: $PARALLEL_IMPORT_SESSIONS
parallel_run_eval: $PARALLEL_RUN_EVAL
parallel_judge: $PARALLEL_JUDGE
sample: ${SAMPLE:-all}
question_index: ${QUESTION_INDEX:-}
EOF
}

# 拷贝生成的记忆文件快照
copy_memory_snapshot() {
    if [ "$SKIP_IMPORT" = "true" ]; then
        return
    fi
    ui_step_copy="copy_memory_snapshot"
    ui_info "正在保存记忆文件快照…"

    # 从配置中提取 workspace 路径
    local workspace
    workspace=$("$PYTHON_BIN" - <<'PY'
import json, os
config_path = os.environ["OPENVIKING_CONFIG_FILE"]
try:
    with open(config_path, "r") as f:
        config = json.load(f)
    print(config.get("storage", {}).get("workspace", ""))
except Exception:
    print("")
PY
)
    if [ -z "$workspace" ]; then
        ui_warn "无法从配置中提取 workspace 路径，跳过记忆快照"
        return
    fi

    local peers_dir="$workspace/viking/default/user/default/peers"
    if [ ! -d "$peers_dir" ]; then
        # 尝试其他路径模式
        peers_dir=$(find "$workspace" -type d -name "peers" -path "*/user/*" 2>/dev/null | head -1)
    fi

    if [ -n "$peers_dir" ] && [ -d "$peers_dir" ]; then
        mkdir -p "$MEMORY_SNAPSHOT_DIR"
        cp -R "$peers_dir/." "$MEMORY_SNAPSHOT_DIR/" 2>/dev/null || true
        local mem_file_count
        mem_file_count=$(find "$MEMORY_SNAPSHOT_DIR" -name "*.md" ! -name ".overview.md" ! -name ".abstract.md" | wc -l | tr -d ' ')
        ui_success "记忆快照已保存：$mem_file_count 个 .md 文件 → $MEMORY_SNAPSHOT_DIR"
    else
        ui_warn "未找到 peers 记忆目录，跳过记忆快照"
    fi
}

# 清理旧实验目录，只保留最近 N 次
cleanup_old_runs() {
    local runs_root="$RESULTS_ROOT/runs"
    if [ ! -d "$runs_root" ]; then
        return
    fi
    local count
    count=$(find "$runs_root" -maxdepth 1 -type d | tail -n +2 | wc -l | tr -d ' ')
    if [ "$count" -le "$KEEP_RUNS" ]; then
        return
    fi
    ui_info "清理旧实验目录（保留最近 $KEEP_RUNS 次，当前 $count 次）…"
    # 按名称排序（时间戳），删除最旧的
    find "$runs_root" -maxdepth 1 -type d | tail -n +2 | sort | head -n "$((count - KEEP_RUNS))" | while read -r old_dir; do
        ui_info "  删除 $(basename "$old_dir")"
        rm -rf "$old_dir"
    done
    ui_success "旧实验目录已清理"
}

# ========== 重跑错题模式（优先） ==========
if [ -n "$RETRY_WRONG" ]; then
    if [ ! -f "$RETRY_WRONG" ]; then
        ui_error "--retry-wrong file not found: $RETRY_WRONG"
        exit 1
    fi

    write_run_metadata
    ui_section "3. 执行评测 · 错题重跑"
    ui_kv "错题文件" "$RETRY_WRONG"

    # 从错题 CSV 中提取需要导入的对话
    ui_step 1 3 "导入错题相关对话"
    capture_import_row_start
    "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
        --input "$INPUT_FILE" \
        --retry-wrong "$RETRY_WRONG" \
        --force-ingest \
        --account "$ACCOUNT" \
        --openviking-url "$OPENVIKING_URL" \
        --success-csv "$IMPORT_SUCCESS_CSV" \
        "${IMPORT_OPTS[@]}" \
        "${COMMON_OPTS[@]}"
    IMPORT_PERFORMED=true

    ui_info "等待数据处理完成（30 秒）…"
    sleep 30

    # 评估错题
    ui_step 2 3 "重新评估错题"
    prepare_bot_log_dir
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --output "$RESULT_FILE" \
        --retry-wrong "$RETRY_WRONG" \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${RUN_EVAL_OPTS[@]}" \
        "${COMMON_OPTS[@]}"

    # 裁判打分
    ui_step 3 3 "裁判打分"
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" "${JUDGE_OPTS[@]}"

    # 统计结果
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_FILE"
    print_import_summary_table

    copy_memory_snapshot
    cleanup_old_runs

    ui_section "完成"
    ui_success "错题重跑完成"
    ui_kv "实验目录" "$RUN_DIR"
    ui_kv "结果文件" "$RESULT_FILE"
    exit 0
fi

# ========== 全量评测模式 ==========
if [ -z "$SAMPLE" ]; then
    write_run_metadata
    ui_section "3. 执行评测 · 全量模式"

    # 导入数据
    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_warn "已通过 --skip-import 跳过导入数据"
    else
        ui_step 1 4 "导入数据"
        capture_import_row_start
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL" \
            --success-csv "$IMPORT_SUCCESS_CSV" \
            "${IMPORT_OPTS[@]}" \
            "${COMMON_OPTS[@]}"
        IMPORT_PERFORMED=true
        ui_info "等待数据处理完成（60 秒）…"
        sleep 60
    fi

    # 评估
    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 1 3 "运行评估"
    else
        ui_step 2 4 "运行评估"
    fi
    prepare_bot_log_dir
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --output "$RESULT_FILE" \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${RUN_EVAL_OPTS[@]}" \
        "${COMMON_OPTS[@]}"

    # 裁判打分
    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 2 3 "裁判打分"
    else
        ui_step 3 4 "裁判打分"
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" "${JUDGE_OPTS[@]}"

    # 计算结果
    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 3 3 "汇总结果"
    else
        ui_step 4 4 "汇总结果"
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_FILE"
    print_import_summary_table

    copy_memory_snapshot
    cleanup_old_runs

    ui_section "完成"
    ui_success "全量评测完成"
    ui_kv "实验目录" "$RUN_DIR"
    ui_kv "结果文件" "$RESULT_FILE"
    exit 0
fi

# ========== 单 sample 评测模式 ==========
# 判断是数字还是 sample_id
if [[ "$SAMPLE" =~ ^-?[0-9]+$ ]]; then
    SAMPLE_INDEX=$SAMPLE
    SAMPLE_ID_FOR_CMD=$SAMPLE_INDEX
    ui_kv "Sample" "index=$SAMPLE_INDEX"
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
        ui_error "sample_id '$SAMPLE' not found"
        exit 1
    fi
    SAMPLE_ID_FOR_CMD=$SAMPLE
    ui_kv "Sample" "id=$SAMPLE · index=$SAMPLE_INDEX"
fi

# 判断是单题模式还是批量模式
if [ -n "$QUESTION_INDEX" ]; then
    write_run_metadata
    # ========== 单题模式 ==========
    ui_section "3. 执行评测 · 单题模式"
    ui_kv "评测范围" "sample=$SAMPLE · question=$QUESTION_INDEX"

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_warn "已通过 --skip-import 跳过导入对话"
    else
        ui_step 1 3 "导入 sample $SAMPLE_INDEX · question $QUESTION_INDEX"
        capture_import_row_start
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --question-index "$QUESTION_INDEX" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL" \
            --success-csv "$IMPORT_SUCCESS_CSV" \
            "${IMPORT_OPTS[@]}" \
            "${COMMON_OPTS[@]}"
        IMPORT_PERFORMED=true

        ui_info "等待数据处理完成（3 秒）…"
        sleep 3
    fi

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 1 2 "运行评估"
    else
        ui_step 2 3 "运行评估"
    fi
    prepare_bot_log_dir
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --sample "$SAMPLE_ID_FOR_CMD" \
        --question-index "$QUESTION_INDEX" \
        --count 1 \
        --output "$RESULT_FILE" \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${RUN_EVAL_OPTS[@]}" \
        "${COMMON_OPTS[@]}"

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 2 2 "裁判打分"
    else
        ui_step 3 3 "裁判打分"
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" "${JUDGE_OPTS[@]}"

    ui_section "评测结果"
    print_import_summary_table
    OUTPUT_FILE="$RESULT_FILE" QUESTION_INDEX="$QUESTION_INDEX" "$PYTHON_BIN" - <<'PY'
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

    copy_memory_snapshot
    cleanup_old_runs

    ui_kv "实验目录" "$RUN_DIR"

else
    write_run_metadata
    # ========== 批量模式 ==========
    ui_section "3. 执行评测 · Sample 批量模式"
    ui_kv "评测范围" "sample=$SAMPLE · 所有问题"

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
    ui_kv "问题数量" "$QUESTION_COUNT"

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_warn "已通过 --skip-import 跳过导入所有 Sessions"
    else
        ui_step 1 4 "导入 sample $SAMPLE_INDEX 的所有 Sessions"
        capture_import_row_start
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL" \
            --success-csv "$IMPORT_SUCCESS_CSV" \
            "${IMPORT_OPTS[@]}" \
            "${COMMON_OPTS[@]}"
        IMPORT_PERFORMED=true

        ui_info "等待数据处理完成（10 秒）…"
        sleep 10
    fi

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 1 3 "评估所有问题"
    else
        ui_step 2 4 "评估所有问题"
    fi
    prepare_bot_log_dir
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
        "$INPUT_FILE" \
        --sample "$SAMPLE_ID_FOR_CMD" \
        --output "$RESULT_FILE" \
        --config "$OPENVIKING_CONFIG_FILE" \
        "${RUN_EVAL_OPTS[@]}" \
        "${COMMON_OPTS[@]}"

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 2 3 "裁判打分"
    else
        ui_step 3 4 "裁判打分"
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" "${JUDGE_OPTS[@]}"

    if [ "$SKIP_IMPORT" = "true" ]; then
        ui_step 3 3 "汇总结果"
    else
        ui_step 4 4 "汇总结果"
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_FILE"
    print_import_summary_table

    copy_memory_snapshot
    cleanup_old_runs

    ui_section "完成"
    ui_success "批量评测完成"
    ui_kv "实验目录" "$RUN_DIR"
    ui_kv "结果文件" "$RESULT_FILE"
fi
