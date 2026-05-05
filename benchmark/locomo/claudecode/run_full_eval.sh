#!/bin/bash

set -e

: '
Claude Code 完整 LoCoMo 评测流程

用法:
  ./run_full_eval.sh                          # 全流程（ingest + QA + judge + stat）
  ./run_full_eval.sh --skip-import            # 跳过 ingest，直接 QA
  ./run_full_eval.sh --sample 0              # 只处理第 0 个 sample
  ./run_full_eval.sh --api-url http://x --api-key sk-xxx   # 自定义 API
  ./run_full_eval.sh --model sonnet           # 指定模型
  ./run_full_eval.sh --force-ingest           # 强制重新导入
'

# =============================================================================
# 配置（按需修改）
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="$SCRIPT_DIR/../locomo10.json"
RESULT_DIR="$SCRIPT_DIR/result"

# 隔离环境目录（可用 --project-root / --home 覆盖）
PROJECT_ROOT="/tmp/locomo-eval"
EVAL_HOME="/tmp/claude-eval-home"

# 可选的 ingest prompt 前缀（可用 --prompt-prefix 覆盖）
PROMPT_PREFIX=""

# OV (OpenViking) 参数
HOOKS_SETTINGS=""
MCP_CONFIG=""
OV_INGEST_CONFIG=""
OV_QA_CONFIG=""
OV_CLI_CONFIG=""
OV_SHARED_ID_SET=false
OV_SHARED_ID=""
OV_PREAMBLE=""

# QA 并行度（注意 API rate limit）
QA_PARALLEL=5

# =============================================================================
# 参数解析
# =============================================================================
SKIP_IMPORT=false
FORCE_INGEST=false
SAMPLE_ARG=""
API_URL_ARG=""
API_KEY_ARG=""
AUTH_TOKEN_ARG=""
MODEL_ARG=""
SAMPLE_IDX=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-import)
            SKIP_IMPORT=true
            shift
            ;;
        --force-ingest)
            FORCE_INGEST=true
            shift
            ;;
        --sample)
            SAMPLE_IDX="$2"
            SAMPLE_ARG="--sample $2"
            shift 2
            ;;
        --api-url)
            API_URL_ARG="--api-url $2"
            shift 2
            ;;
        --api-key)
            API_KEY_ARG="--api-key $2"
            shift 2
            ;;
        --auth-token)
            AUTH_TOKEN_ARG="--auth-token $2"
            shift 2
            ;;
        --model)
            MODEL_ARG="--model $2"
            shift 2
            ;;
        --parallel)
            QA_PARALLEL="$2"
            shift 2
            ;;
        --project-root)
            PROJECT_ROOT="$2"
            shift 2
            ;;
        --home)
            EVAL_HOME="$2"
            shift 2
            ;;
        --result-dir)
            RESULT_DIR="$2"
            shift 2
            ;;
        --input)
            INPUT_FILE="$2"
            shift 2
            ;;
        --prompt-prefix)
            PROMPT_PREFIX="$2"
            shift 2
            ;;
        --hooks-settings)
            HOOKS_SETTINGS="$2"
            shift 2
            ;;
        --mcp-config)
            MCP_CONFIG="$2"
            shift 2
            ;;
        --ov-ingest-config)
            OV_INGEST_CONFIG="$2"
            shift 2
            ;;
        --ov-qa-config)
            OV_QA_CONFIG="$2"
            shift 2
            ;;
        --ov-cli-config)
            OV_CLI_CONFIG="$2"
            shift 2
            ;;
        --ov-shared-id)
            OV_SHARED_ID="$2"
            OV_SHARED_ID_SET=true
            shift 2
            ;;
        --ov-preamble)
            OV_PREAMBLE="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            shift
            ;;
    esac
done

# 输出文件名
if [ -n "$SAMPLE_IDX" ]; then
    OUTPUT_CSV="$RESULT_DIR/qa_results_sample${SAMPLE_IDX}.csv"
else
    OUTPUT_CSV="$RESULT_DIR/qa_results.csv"
fi

# force-ingest 参数
FORCE_INGEST_ARG=""
if [ "$FORCE_INGEST" = true ]; then
    FORCE_INGEST_ARG="--force-ingest"
fi

mkdir -p "$RESULT_DIR"

# =============================================================================
# Step 1: Ingest（逐 session 发给 Claude Code，让 auto-memory 记住）
# =============================================================================
if [ "$SKIP_IMPORT" = false ]; then
    echo "[1/4] Ingesting conversations into Claude Code auto-memory..."
    uv run python "$SCRIPT_DIR/ingest.py" \
        --input "$INPUT_FILE" \
        --project-root "$PROJECT_ROOT" \
        --home "$EVAL_HOME" \
        --record "$RESULT_DIR/.ingest_record.json" \
        --success-csv "$RESULT_DIR/ingest_success.csv" \
        --error-log "$RESULT_DIR/ingest_errors.log" \
        --prompt-prefix "$PROMPT_PREFIX" \
        $SAMPLE_ARG \
        $API_URL_ARG \
        $API_KEY_ARG \
        $AUTH_TOKEN_ARG \
        $MODEL_ARG \
        $FORCE_INGEST_ARG
else
    echo "[1/4] Skipping ingest..."
fi

# =============================================================================
# Step 2: QA 评估
# =============================================================================
OV_QA_ARGS=""
if [ -n "$HOOKS_SETTINGS" ]; then
    OV_QA_ARGS="$OV_QA_ARGS --hooks-settings $HOOKS_SETTINGS"
fi
if [ -n "$MCP_CONFIG" ]; then
    OV_QA_ARGS="$OV_QA_ARGS --mcp-config $MCP_CONFIG"
fi
if [ -n "$OV_QA_CONFIG" ]; then
    OV_QA_ARGS="$OV_QA_ARGS --ov-config $OV_QA_CONFIG"
fi
if [ -n "$OV_CLI_CONFIG" ]; then
    OV_QA_ARGS="$OV_QA_ARGS --ov-cli-config $OV_CLI_CONFIG"
fi
echo "[2/4] Running QA evaluation..."
OV_PREAMBLE_ARG=()
if [ -n "$OV_PREAMBLE" ]; then
    OV_PREAMBLE_ARG=(--ov-preamble "$OV_PREAMBLE")
fi
OV_SHARED_ARG=()
if [ "$OV_SHARED_ID_SET" = true ]; then
    OV_SHARED_ARG=(--ov-shared-id "$OV_SHARED_ID")
fi
uv run python "$SCRIPT_DIR/eval.py" \
    --input "$INPUT_FILE" \
    --output "$OUTPUT_CSV" \
    --project-root "$PROJECT_ROOT" \
    --home "$EVAL_HOME" \
    --parallel "$QA_PARALLEL" \
    $SAMPLE_ARG \
    $API_URL_ARG \
    $API_KEY_ARG \
    $AUTH_TOKEN_ARG \
    $MODEL_ARG \
    $OV_QA_ARGS \
    "${OV_SHARED_ARG[@]}" \
    "${OV_PREAMBLE_ARG[@]}"

# =============================================================================
# Step 3: Judge 打分
# =============================================================================
echo "[3/4] Judging answers..."
uv run python "$SCRIPT_DIR/judge.py" \
    --input "$OUTPUT_CSV" \
    --parallel 40

# =============================================================================
# Step 4: 统计结果
# =============================================================================
echo "[4/4] Computing statistics..."
uv run python "$SCRIPT_DIR/stat_judge_result.py" --input "$OUTPUT_CSV"

echo ""
echo "Done! Results: $OUTPUT_CSV"
