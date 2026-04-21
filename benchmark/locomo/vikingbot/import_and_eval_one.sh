#!/bin/bash
# 单题/批量测试脚本：导入对话 + 提问验证
#
# Usage:
#   ./import_and_eval_one.sh 0 2                         # sample 0, question 2 (单题)
#   ./import_and_eval_one.sh conv-26 2                   # sample_id conv-26, question 2 (单题)
#   ./import_and_eval_one.sh conv-26                     # sample_id conv-26, 所有问题 (批量)
#   ./import_and_eval_one.sh conv-26 2 --skip-import     # 跳过导入，直接评测
#   ./import_and_eval_one.sh conv-26 --skip-import       # 跳过导入，批量评测

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP_IMPORT=false

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "未找到 python3/python，请先安装 Python。" >&2
    exit 1
fi

# 评测前预检配置
PRECHECK_STATUS=0
"$PYTHON_BIN" "$SCRIPT_DIR/preflight_eval_config.py" || PRECHECK_STATUS=$?
if [ "$PRECHECK_STATUS" -ne 0 ]; then
    if [ "$PRECHECK_STATUS" -eq 2 ]; then
        echo "[preflight] 已完成 root_api_key 初始化，请先重启 openviking-server，再重新执行评测脚本。" >&2
    fi
    exit "$PRECHECK_STATUS"
fi

# 从 ovcli.conf 获取 account（为空时使用 default）
ACCOUNT="$($PYTHON_BIN - <<'PY'
import json
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OVCLI_CONF, OPENVIKING_CLI_CONFIG_ENV

path = resolve_config_path(None, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
if path is None:
    print("default")
else:
    try:
        with open(Path(path), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        account = str(data.get("account") or "").strip()
        print(account or "default")
    except Exception:
        print("default")
PY
)"

# 从 ov.conf 获取 OpenViking 服务地址（默认 127.0.0.1:1933）
OPENVIKING_URL="$($PYTHON_BIN - <<'PY'
import json
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

host = "127.0.0.1"
port = 1933

path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
if path is not None:
    try:
        with open(Path(path), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        server = data.get("server") or {}
        parsed_host = str(server.get("host") or "").strip()
        parsed_port = server.get("port")
        if parsed_host:
            host = parsed_host
        if isinstance(parsed_port, int):
            port = parsed_port
        elif isinstance(parsed_port, str) and parsed_port.strip().isdigit():
            port = int(parsed_port.strip())
    except Exception:
        pass

print(f"http://{host}:{port}")
PY
)"

echo "[preflight] 本次导入与评测使用 account: $ACCOUNT"
echo "[preflight] 本次导入使用 OpenViking URL: $OPENVIKING_URL"

if [ -t 0 ] && [ -t 1 ]; then
    INTERACTIVE=1
else
    INTERACTIVE=0
fi

# 导入前检查：验证 OpenViking server 可用且 account 存在
OPENVIKING_URL="$OPENVIKING_URL" ACCOUNT="$ACCOUNT" INTERACTIVE="$INTERACTIVE" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

url = os.environ.get("OPENVIKING_URL", "").strip().rstrip("/")
account = os.environ.get("ACCOUNT", "default").strip() or "default"
interactive = os.environ.get("INTERACTIVE", "0") == "1"

ov_conf_path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
if ov_conf_path is None:
    print("[preflight] 未找到 ov.conf，无法读取 root_api_key。", file=sys.stderr)
    raise SystemExit(1)

try:
    with open(Path(ov_conf_path), "r", encoding="utf-8-sig") as f:
        ov_data = json.load(f)
except Exception as exc:
    print(f"[preflight] 读取 ov.conf 失败: {exc}", file=sys.stderr)
    raise SystemExit(1)

root_key = str((ov_data.get("server") or {}).get("root_api_key") or "").strip()
if not root_key:
    print("[preflight] server.root_api_key 为空，无法执行服务连通性检查。", file=sys.stderr)
    raise SystemExit(1)

admin_user_id = str(
    ((ov_data.get("bot") or {}).get("ov_server") or {}).get("admin_user_id") or "default"
).strip() or "default"

req = urllib.request.Request(
    f"{url}/api/v1/admin/accounts",
    headers={
        "X-API-Key": root_key,
        "Content-Type": "application/json",
    },
    method="GET",
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as e:
    detail = e.read().decode("utf-8", errors="replace")
    print(f"[preflight] OpenViking server 检查失败（HTTP {e.code}）: {detail}", file=sys.stderr)
    raise SystemExit(1)
except Exception as exc:
    print(f"[preflight] OpenViking server 不可用: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    payload = json.loads(body)
except Exception as exc:
    print(f"[preflight] /api/v1/admin/accounts 返回非 JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

accounts = payload.get("result", payload)
if not isinstance(accounts, list):
    print("[preflight] /api/v1/admin/accounts 返回格式异常。", file=sys.stderr)
    raise SystemExit(1)

exists = False
for item in accounts:
    if isinstance(item, dict):
        account_id = str(item.get("account_id") or item.get("id") or "").strip()
        if account_id == account:
            exists = True
            break
    elif isinstance(item, str) and item == account:
        exists = True
        break

if not exists:
    if not interactive:
        print(f"[preflight] account '{account}' 不存在，非交互模式下不会自动创建。", file=sys.stderr)
        raise SystemExit(1)

    prompt = f"[preflight] account '{account}' 不存在，是否自动创建该 account? [Y/n]: "
    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty_in:
            print(prompt, end="", flush=True)
            answer = tty_in.readline().strip().lower()
    except Exception as exc:
        print(f"[preflight] 无法读取终端输入: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if answer not in ("", "y", "yes"):
        print("[preflight] 已取消自动创建 account。", file=sys.stderr)
        raise SystemExit(1)

    create_req = urllib.request.Request(
        f"{url}/api/v1/admin/accounts",
        headers={
            "X-API-Key": root_key,
            "Content-Type": "application/json",
        },
        data=json.dumps({"account_id": account, "admin_user_id": admin_user_id}).encode("utf-8"),
        method="POST",
    )

    try:
        with urllib.request.urlopen(create_req, timeout=10) as create_resp:
            create_body = create_resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"[preflight] 创建 account 失败（HTTP {e.code}）: {detail}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"[preflight] 创建 account 失败: {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        create_payload = json.loads(create_body)
    except Exception:
        create_payload = {}

    if isinstance(create_payload, dict) and create_payload.get("status") == "error":
        print(f"[preflight] 创建 account 失败: {create_payload}", file=sys.stderr)
        raise SystemExit(1)

    print(f"[preflight] 已创建 account '{account}'（admin_user_id={admin_user_id}）。")

print(f"[preflight] OpenViking server 可用，account '{account}' 已就绪。")
PY

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
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --question-index "$QUESTION_INDEX" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL"

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
        "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
            "$INPUT_FILE" \
            --sample "$SAMPLE_ID_FOR_CMD" \
            --question-index "$QUESTION_INDEX" \
            --count 1
    else
        # sample_id 模式直接更新批量结果文件
        OUTPUT_FILE=./result/locomo_${SAMPLE}_result.csv
        "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
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
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$OUTPUT_FILE" --parallel 1

    # 输出结果
    echo ""
    echo "=== 评测结果 ==="
    OUTPUT_FILE="$OUTPUT_FILE" QUESTION_INDEX="$QUESTION_INDEX" "$PYTHON_BIN" - <<'PY'
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

else
    # ========== 批量模式 ==========
    echo "=== 批量模式: sample $SAMPLE, 所有问题 ==="

    # 获取该 sample 的问题数量
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
    echo "Found $QUESTION_COUNT questions for sample $SAMPLE"

    # 导入所有 sessions
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[1/4] Skipping import (--skip-import)"
    else
        echo "[1/4] Importing all sessions for sample $SAMPLE_INDEX..."
        "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" \
            --input "$INPUT_FILE" \
            --sample "$SAMPLE_INDEX" \
            --force-ingest \
            --account "$ACCOUNT" \
            --openviking-url "$OPENVIKING_URL"

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
    "$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" \
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
    "$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$OUTPUT_FILE" --parallel 5

    # 输出统计结果
    if [ "$SKIP_IMPORT" = "true" ]; then
        echo "[3/3] Calculating statistics..."
    else
        echo "[4/4] Calculating statistics..."
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$OUTPUT_FILE"

    echo ""
    echo "=== 批量评测完成 ==="
    echo "结果文件: $OUTPUT_FILE"
fi
