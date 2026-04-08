#!/bin/bash

set -e

: '
OpenClaw 完整评估流程脚本

用法:
  ./run_full_eval.sh                      # 只导入 OpenViking
  ./run_full_eval.sh --with-claw-import   # 同时导入 OpenViking 和 OpenClaw
  ./run_full_eval.sh --skip-import        # 跳过导入步骤
'

# 基于脚本所在目录计算数据文件路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"
RESULT_DIR="$SCRIPT_DIR/result"
OUTPUT_CSV="$RESULT_DIR/qa_results.csv"
GATEWAY_TOKEN="your_gateway_token"


# 解析参数
SKIP_IMPORT=false
WITH_CLAW_IMPORT=false
for arg in "$@"; do
    if [ "$arg" = "--skip-import" ]; then
        SKIP_IMPORT=true
    elif [ "$arg" = "--with-claw-import" ]; then
        WITH_CLAW_IMPORT=true
    fi
done

# 确保结果目录存在
mkdir -p "$RESULT_DIR"

# Step 1: 导入数据
if [ "$SKIP_IMPORT" = false ]; then
    if [ "$WITH_CLAW_IMPORT" = true ]; then
        echo "[1/5] 导入数据到 OpenViking 和 OpenClaw..."

        # 后台运行 OpenViking 导入
        python "$SCRIPT_DIR/../vikingbot/import_to_ov.py" --input "$INPUT_FILE" --force-ingest > "$RESULT_DIR/import_ov.log" 2>&1 &
        PID_OV=$!

        # 后台运行 OpenClaw 导入
        python "$SCRIPT_DIR/eval.py" ingest "$INPUT_FILE" --force-ingest --token "$GATEWAY_TOKEN" > "$RESULT_DIR/import_claw.log" 2>&1 &
        PID_CLAW=$!

        # 等待两个导入任务完成
        wait $PID_OV $PID_CLAW
    else
        echo "[1/5] 导入数据到 OpenViking..."
        python "$SCRIPT_DIR/../vikingbot/import_to_ov.py" --input "$INPUT_FILE" --force-ingest
    fi

    echo "导入完成，等待 1 分钟..."
    sleep 60
else
    echo "[1/5] 跳过导入数据..."
fi

# Step 2: 运行 QA 模型（默认输出到 result/qa_results.csv）
echo "[2/5] 运行 QA 评估..."
python "$SCRIPT_DIR/eval.py" qa "$INPUT_FILE" --token "$GATEWAY_TOKEN"

# Step 3: 裁判打分
echo "[3/5] 裁判打分..."
python "$SCRIPT_DIR/judge.py" --input "$OUTPUT_CSV" --parallel 40

# Step 4: 计算结果
echo "[4/5] 计算结果..."
python "$SCRIPT_DIR/../vikingbot/stat_judge_result.py" --input "$OUTPUT_CSV"

echo "[5/5] 完成!"
echo "结果文件: $OUTPUT_CSV"
