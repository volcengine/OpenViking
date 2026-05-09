#!/bin/bash

set -e

: '
OpenClaw 完整评估流程脚本

用法:
  ./run_full_eval.sh                      # 只导入 OpenViking (所有 samples)
  ./run_full_eval.sh --with-claw-import   # 同时导入 OpenViking 和 OpenClaw (所有 samples)
  ./run_full_eval.sh --skip-import        # 跳过导入步骤 (所有 samples)
  ./run_full_eval.sh --sample 0           # 只处理第 0 个 sample
  ./run_full_eval.sh --sample 1 --with-claw-import  # 只处理第 1 个 sample，同时导入 OpenClaw
  ./run_full_eval.sh --force-ingest       # 强制重新导入所有数据
'

# 基于脚本所在目录计算数据文件路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"
RESULT_DIR="$SCRIPT_DIR/result"
OUTPUT_CSV="$RESULT_DIR/qa_results.csv"
GATEWAY_TOKEN="90f2d2dc2f7b4d50cb943d3d3345e667bb3e9bcb7ec3a1fb"


# 解析参数
SKIP_IMPORT=false
WITH_CLAW_IMPORT=false
FORCE_INGEST=false
SAMPLE_IDX=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-import)
            SKIP_IMPORT=true
            shift
            ;;
        --with-claw-import)
            WITH_CLAW_IMPORT=true
            shift
            ;;
        --force-ingest)
            FORCE_INGEST=true
            shift
            ;;
        --sample)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "错误: --sample 需要一个参数 (sample index, 0-based)"
                exit 1
            fi
            SAMPLE_IDX="$2"
            shift 2
            ;;
        *)
            echo "警告: 未知参数 $1"
            shift
            ;;
    esac
done

# 构建 sample 参数
SAMPLE_ARG=""
if [ -n "$SAMPLE_IDX" ]; then
    SAMPLE_ARG="--sample $SAMPLE_IDX"
    # 如果指定了 sample，修改输出文件名以避免覆盖
    OUTPUT_CSV="$RESULT_DIR/qa_results_sample${SAMPLE_IDX}.csv"
fi

# 构建 force-ingest 参数
FORCE_INGEST_ARG=""
if [ "$FORCE_INGEST" = true ]; then
    FORCE_INGEST_ARG="--force-ingest"
fi

# 确保结果目录存在
mkdir -p "$RESULT_DIR"

# Step 1: 导入数据
if [ "$SKIP_IMPORT" = false ]; then
    if [ "$WITH_CLAW_IMPORT" = true ]; then
        echo "[1/5] 导入数据到 OpenViking 和 OpenClaw..."

        # 后台运行 OpenViking 导入
        python "$SCRIPT_DIR/import_to_ov.py" --no-user-agent-id --input "$INPUT_FILE" $FORCE_INGEST_ARG $SAMPLE_ARG > "$RESULT_DIR/import_ov.log" 2>&1 &
        PID_OV=$!

        # 后台运行 OpenClaw 导入
        python "$SCRIPT_DIR/eval.py" ingest "$INPUT_FILE" $FORCE_INGEST_ARG --token "$GATEWAY_TOKEN" $SAMPLE_ARG > "$RESULT_DIR/import_claw.log" 2>&1 &
        PID_CLAW=$!

        # 等待两个导入任务完成
        wait $PID_OV $PID_CLAW
    else
        echo "[1/5] 导入数据到 OpenViking..."
        python "$SCRIPT_DIR/import_to_ov.py" --no-user-agent-id --input "$INPUT_FILE" $FORCE_INGEST_ARG $SAMPLE_ARG
    fi

else
    echo "[1/5] 跳过导入数据..."
fi

# Step 2: 运行 QA 模型（默认输出到 result/qa_results.csv）
echo "[2/5] 运行 QA 评估..."
python "$SCRIPT_DIR/eval.py" qa "$INPUT_FILE" --token "$GATEWAY_TOKEN" $SAMPLE_ARG --parallel 15 --output "${OUTPUT_CSV%.csv}"

# Step 3: 裁判打分
echo "[3/5] 裁判打分..."
python "$SCRIPT_DIR/judge.py" --input "$OUTPUT_CSV" --parallel 40

# Step 4: 计算结果
echo "[4/5] 计算结果..."
python "$SCRIPT_DIR/stat_judge_result.py" --input "$OUTPUT_CSV"

echo "[5/5] 完成!"
echo "结果文件: $OUTPUT_CSV"
