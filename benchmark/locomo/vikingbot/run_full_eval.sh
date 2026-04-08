#!/bin/bash

set -e

# 基于脚本所在目录计算数据文件路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"

# Step 1: 导入数据（可跳过）
if [ "$1" != "--skip-import" ]; then
    echo "[1/4] 导入数据..."
    python benchmark/locomo/vikingbot/import_to_ov.py --input $INPUT_FILE --force-ingest
    echo "等待 1 分钟..."
    sleep 60
else
    echo "[1/4] 跳过导入数据..."
fi

# Step 2: 评估
echo "[2/4] 评估..."
python benchmark/locomo/vikingbot/run_eval.py $INPUT_FILE --output ./result/locomo_result_multi_read_all.csv


# Step 3: 裁判打分
echo "[3/4] 裁判打分..."
python benchmark/locomo/vikingbot/judge.py --input ./result/locomo_result_multi_read_all.csv --parallel 40

# Step 4: 计算结果
echo "[4/4] 计算结果..."
python benchmark/locomo/vikingbot/stat_judge_result.py --input ./result/locomo_result_multi_read_all.csv

echo "完成!"