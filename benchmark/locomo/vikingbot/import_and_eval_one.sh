#!/bin/bash
# 单题/批量测试脚本：导入对话 + 提问验证
#
# Usage:
#   ./import_and_eval_one.sh 0 2                         # sample 0, question 2 (单题)
#   ./import_and_eval_one.sh conv-26 2                   # sample_id conv-26, question 2 (单题)
#   ./import_and_eval_one.sh conv-26                      # sample_id conv-26, 所有问题 (批量)
#   ./import_and_eval_one.sh conv-26 2 --skip-import      # 跳过导入，直接评测
#   ./import_and_eval_one.sh conv-26 --skip-import        # 跳过导入，批量评测

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP_IMPORT=false

# 解析参数
for arg in "$@"; do
    if [ "$arg" = "--skip-import" ]; then
        SKIP_IMPORT=true
    fi
done

# 过滤掉 --skip-import 获取实际参数
ARGS=()
for arg in "$@"; do
    if [ "$arg" != "--skip-import" ]; then
        ARGS+=("$arg")
    fi
done

SAMPLE=${ARGS[0]}
QUESTION_INDEX=${ARGS[1]}
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"

if [ -z "$SAMPLE" ]; then
    echo "Usage: $0 <sample_index|sample_id> [question_index] [--skip-import]"
    echo "  sample_index: 数字索引 (0,1,2...) 或 sample_id (conv-26)"
    echo "  question_index: 问题索引 (可选)，不传则测试该 sample 的所有问题"
    echo "  --skip-import: 跳过导入步骤，直接使用已导入的数据进行评测"
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
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/3] Skipping import (--skip-import)"
    else
        echo "[1/3] Importing sample $SAMPLE_INDEX, question $QUESTION_INDEX..."
        python benchmark/locomo/vikingbot/import_to_ov.py \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --question-index "$QUESTION_INDEX" \
            --force-ingest

        echo "Waiting for data processing..."
        sleep 3
    fi

    # 运行评测
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/2] Running evaluation (skip-import mode)..."
    else
        echo "[2/3] Running evaluation..."
    fi
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
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[2/2] Running judge..."
    else
        echo "[3/3] Running judge..."
    fi
    python benchmark/locomo/vikingbot/judge.py --input "$OUTPUT_FILE" --parallel 1

    # 输出结果
    echo ""
    echo "=== 评测结果 ==="
    python3 -c "
import csv
import json

question_index = $QUESTION_INDEX

with open('$OUTPUT_FILE') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# 找到指定 question_index 的结果
row = None
for r in rows:
    if int(r.get('question_index', -1)) == question_index:
        row = r
        break

if row is None:
    # 没找到则用最后一条
    row = rows[-1]

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
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/4] Skipping import (--skip-import)"
    else
        echo "[1/4] Importing all sessions for sample $SAMPLE_INDEX..."
        python benchmark/locomo/vikingbot/import_to_ov.py \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --force-ingest

        echo "Waiting for data processing..."
        sleep 10
    fi

    # 运行评测（所有问题）
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/3] Running evaluation for all questions (skip-import mode)..."
    else
        echo "[2/4] Running evaluation for all questions..."
    fi
    OUTPUT_FILE=./result/locomo_${SAMPLE}_result.csv
    python benchmark/locomo/vikingbot/run_eval.py \
        "$INPUT_FILE" \
        --sample "$SAMPLE_ID_FOR_CMD" \
        --output "$OUTPUT_FILE" \
        --threads 5

    # 运行 Judge 评分
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[2/3] Running judge..."
    else
        echo "[3/4] Running judge..."
    fi
    python benchmark/locomo/vikingbot/judge.py --input "$OUTPUT_FILE" --parallel 5

    # 输出统计结果
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[3/3] Calculating statistics..."
    else
        echo "[4/4] Calculating statistics..."
    fi
    python benchmark/locomo/vikingbot/stat_judge_result.py --input "$OUTPUT_FILE"

    echo ""
    echo "=== 批量评测完成 ==="
    echo "结果文件: $OUTPUT_FILE"
fi