#!/bin/bash
# 单题测试脚本：导入对话 + 提问验证
#
# Usage:
#   ./import_and_eval_one.sh 0 2          # sample 0, question 2
#   ./import_and_eval_one.sh conv-26 2    # sample_id conv-26, question 2

set -e

SAMPLE=$1
QUESTION_INDEX=${2:-0}
INPUT_FILE=~/.test_data/locomo10.json

if [ -z "$SAMPLE" ]; then
    echo "Usage: $0 <sample_index> <question_index>"
    echo "  sample_index: 数字索引 (0,1,2...) 或 sample_id (conv-26)"
    echo "  question_index: 问题索引，默认 0"
    exit 1
fi

# 判断是数字还是 sample_id
if [[ "$SAMPLE" =~ ^-?[0-9]+$ ]]; then
    SAMPLE_INDEX=$SAMPLE
    echo "Using sample index: $SAMPLE_INDEX"
else
    # 通过 sample_id 查找索引
    SAMPLE_INDEX=$(python3 -c "
import json
data = json.load(open('$INPUT_FILE'))
for i, s in enumerate(data):
    if s.get('sample_id') == '$SAMPLE':
        print(i)
        break
else:
    print('NOT_FOUND')
")
    if [ "$SAMPLE_INDEX" = "NOT_FOUND" ]; then
        echo "Error: sample_id '$SAMPLE' not found"
        exit 1
    fi
    echo "Using sample_id: $SAMPLE (index: $SAMPLE_INDEX)"
fi

# 导入对话
echo "[1/2] Importing sample $SAMPLE_INDEX..."
python benchmark/locomo/vikingbot/import_to_ov.py \
    --input "$INPUT_FILE" \
    --sample "$SAMPLE_INDEX" \
    --force-ingest

echo "Waiting for data processing..."
sleep 3

# 运行评测
echo "[2/2] Running evaluation..."
python benchmark/locomo/vikingbot/run_eval.py \
    "$INPUT_FILE" \
    --sample "$SAMPLE_INDEX" \
    --question-index "$QUESTION_INDEX" \
    --count 1

echo "Done!"