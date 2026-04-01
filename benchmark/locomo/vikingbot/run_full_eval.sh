#!/bin/bash

set -e

# Step 1: 导入数据
echo "[1/4] 导入数据..."
python bot/eval/locomo/import_to_ov.py --input ~/.test_data/locomo10.json --force-ingest

echo "等待 3 分钟..."
sleep 180

# Step 2: 评估
echo "[2/4] 评估..."
python bot/eval/locomo/run_eval.py ~/.test_data/locomo_qa_1528.csv --output ./result/locomo_result_multi_read_all.csv --threads 20

echo "等待 3 分钟..."
sleep 180

# Step 3: 裁判打分
echo "[3/4] 裁判打分..."
python bot/eval/locomo/judge.py --token 0a2b68f6-4df3-48f5-81b9-f85fe0af9cef --input ./result/locomo_result_multi_read_all.csv --parallel 10

echo "等待 3 分钟..."
sleep 180

# Step 4: 计算结果
echo "[4/4] 计算结果..."
python bot/eval/locomo/stat_judge_result.py --input ./result/locomo_result_multi_read_all.csv

echo "完成!"