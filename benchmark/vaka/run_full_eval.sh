#!/bin/bash
# VAKA 评测完整流程脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================"
echo "VAKA 记忆评测 - 完整流程"
echo "============================================"

# Step 1: Ingest + Query
echo ""
echo "[Step 1] 执行对话写入和查询召回..."
cd "$SCRIPT_DIR"
python3 run_eval.py --phase all

# Step 2: LLM 评分
echo ""
echo "[Step 2] 执行 LLM 评分..."
python3 judge.py

echo ""
echo "============================================"
echo "评测完成！结果文件:"
echo "  - result/eval_result.csv"
echo "  - result/judged_result.csv"
echo "============================================"