#!/bin/bash
# 单题/批量测试脚本：导入对话 + 提问验证
#
# Usage:
#   ./import_and_eval_one.sh 0 2          # sample 0, question 2 (单题)
#   ./import_and_eval_one.sh conv-26 2    # sample_id conv-26, question 2 (单题)
#   ./import_and_eval_one.sh conv-26       # sample_id conv-26, 所有问题 (批量)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SAMPLE=$1
QUESTION_INDEX=$2
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"

if [ -z "$SAMPLE" ]; then
    echo "Usage: $0 <sample_index|sample_id> [question_index]"
    echo "  sample_index: 数字索引 (0,1,2...) 或 sample_id (conv-26)"
    echo "  question_index: 问题索引 (可选)，不传则测试该 sample 的所有问题"
    exit 1
fi

# 判断是数字还是 sample_id
if [[ "$SAMPLE" =~ ^-?[0-9]+$ ]]; then
    SAMPLE_INDEX=$SAMPLE
    SAMPLE_ID_FOR_CMD=$SAMPLE_INDEX
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
    SAMPLE_ID_FOR_CMD=$SAMPLE
    echo "Using sample_id: $SAMPLE (index: $SAMPLE_INDEX)"
fi

# 判断是单题模式还是批量模式
if [ -n "$QUESTION_INDEX" ]; then
    # ========== 单题模式 ==========
    echo "=== 单题模式: sample $SAMPLE, question $QUESTION_INDEX ==="

    # 导入对话（只导入 question 对应的 session）
    echo "[1/3] Importing sample $SAMPLE_INDEX, question $QUESTION_INDEX..."
    python benchmark/locomo/vikingbot/import_to_ov.py \
        --input "$INPUT_FILE" \
        --sample "$SAMPLE_INDEX" \
        --question-index "$QUESTION_INDEX" \
        --force-ingest

    echo "Waiting for data processing..."
    sleep 3

    # 运行评测
    echo "[2/3] Running evaluation..."
    if [[ "$SAMPLE" =~ ^-?[0-9]+$ ]]; then
        # 数字索引用默认输出文件
        OUTPUT_FILE=./result/locomo_qa_result.csv
        python benchmark/locomo/vikingbot/run_eval.py \
            "$INPUT_FILE" \
            --sample "$SAMPLE_ID_FOR_CMD" \
            --question-index "$QUESTION_INDEX" \
            --count 1
    else
        # sample_id 模式直接更新批量结果文件
        OUTPUT_FILE=./result/locomo_${SAMPLE}_result.csv
        python benchmark/locomo/vikingbot/run_eval.py \
            "$INPUT_FILE" \
            --sample "$SAMPLE_ID_FOR_CMD" \
            --question-index "$QUESTION_INDEX" \
            --count 1 \
            --output "$OUTPUT_FILE" \
            --update-mode
    fi

    # 运行 Judge 评分
    echo "[3/3] Running judge..."
    python benchmark/locomo/vikingbot/judge.py --input "$OUTPUT_FILE" --parallel 1

    # 输出结果
    echo ""
    echo "=== 评测结果 ==="
    python3 -c "
import csv
import json
with open('$OUTPUT_FILE') as f:
    reader = csv.DictReader(f)
    row = list(reader)[-1]  # 最后一条结果

# 解析 evidence_text
evidence_text = json.loads(row.get('evidence_text', '[]'))
evidence_str = '\\n'.join(evidence_text) if evidence_text else ''

print(f\"问题: {row['question']}\")
print(f\"期望答案: {row['answer']}\")
print(f\"模型回答: {row['response']}\")
print(f\"证据原文:\\n{evidence_str}\")
print(f\"结果: {row.get('result', 'N/A')}\")
print(f\"原因: {row.get('reasoning', 'N/A')}\")
"

else
    # ========== 批量模式 ==========
    echo "=== 批量模式: sample $SAMPLE, 所有问题 ==="

    # 获取该 sample 的问题数量
    QUESTION_COUNT=$(python3 -c "
import json
data = json.load(open('$INPUT_FILE'))
sample = data[$SAMPLE_INDEX]
print(len(sample.get('qa', [])))
")
    echo "Found $QUESTION_COUNT questions for sample $SAMPLE"

    # 导入所有 sessions
    echo "[1/4] Importing all sessions for sample $SAMPLE_INDEX..."
    python benchmark/locomo/vikingbot/import_to_ov.py \
        --input "$INPUT_FILE" \
        --sample "$SAMPLE_INDEX" \
        --force-ingest

    echo "Waiting for data processing..."
    sleep 10

    # 运行评测（所有问题）
    echo "[2/4] Running evaluation for all questions..."
    OUTPUT_FILE=./result/locomo_${SAMPLE}_result.csv
    python benchmark/locomo/vikingbot/run_eval.py \
        "$INPUT_FILE" \
        --sample "$SAMPLE_ID_FOR_CMD" \
        --output "$OUTPUT_FILE" \
        --threads 5

    # 运行 Judge 评分
    echo "[3/4] Running judge..."
    python benchmark/locomo/vikingbot/judge.py --input "$OUTPUT_FILE" --parallel 5

    # 输出统计结果
    echo "[4/4] Calculating statistics..."
    python benchmark/locomo/vikingbot/stat_judge_result.py --input "$OUTPUT_FILE"

    echo ""
    echo "=== 批量评测完成 ==="
    echo "结果文件: $OUTPUT_FILE"
fi