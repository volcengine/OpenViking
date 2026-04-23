#!/bin/bash

set -e

# 基于脚本所在目录计算路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"
RESULT_FILE="./result/locomo_result_multi_read_all.csv"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "未找到 python3/python，请先安装 Python。" >&2
    exit 1
fi

DEFAULT_OV_CONF_PATH="$($PYTHON_BIN - <<'PY'
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
print(str(path) if path is not None else str(Path.home() / ".openviking" / "ov.conf"))
PY
)"

if [ -t 0 ] && [ -t 1 ]; then
    echo "[preflight] OpenViking 配置默认路径: $DEFAULT_OV_CONF_PATH"
    printf "[preflight] 直接回车使用默认，或输入新路径 [%s]: " "$DEFAULT_OV_CONF_PATH"
    if ! read -r OV_CONF_PATH < /dev/tty; then
        OV_CONF_PATH="$DEFAULT_OV_CONF_PATH"
    fi
    if [ -z "$OV_CONF_PATH" ]; then
        OV_CONF_PATH="$DEFAULT_OV_CONF_PATH"
    fi
else
    OV_CONF_PATH="$DEFAULT_OV_CONF_PATH"
fi

if [ "$OV_CONF_PATH" = "~" ]; then
    OV_CONF_PATH="$HOME"
elif [[ "$OV_CONF_PATH" == ~/* ]]; then
    OV_CONF_PATH="$HOME/${OV_CONF_PATH#~/}"
fi

export OPENVIKING_CONFIG_FILE="$OV_CONF_PATH"
echo "[preflight] 本次使用 ov.conf: $OPENVIKING_CONFIG_FILE"

# 评测前预检配置
PRECHECK_STATUS=0
"$PYTHON_BIN" "$SCRIPT_DIR/preflight_eval_config.py" || PRECHECK_STATUS=$?
if [ "$PRECHECK_STATUS" -ne 0 ]; then
    if [ "$PRECHECK_STATUS" -eq 2 ]; then
        echo "[preflight] 已完成 root_api_key 初始化，请先重启 openviking-server，再重新执行评测脚本。" >&2
    fi
    exit "$PRECHECK_STATUS"
fi

RUNTIME_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/ov_eval_runtime.XXXXXX")"
trap 'rm -f "$RUNTIME_ENV_FILE"' EXIT

if [ -t 0 ] && [ -t 1 ]; then
    INTERACTIVE=1
else
    INTERACTIVE=0
fi

INTERACTIVE="$INTERACTIVE" "$PYTHON_BIN" "$SCRIPT_DIR/preflight_eval_runtime.py" --output-env-file "$RUNTIME_ENV_FILE"
# shellcheck disable=SC1090
source "$RUNTIME_ENV_FILE"

# Step 1: 导入数据（可跳过）
if [ "$1" != "--skip-import" ]; then
    echo "[1/4] 导入数据..."
    "$PYTHON_BIN" "$SCRIPT_DIR/import_to_ov.py" --input "$INPUT_FILE" --force-ingest --account "$ACCOUNT" --openviking-url "$OPENVIKING_URL"
    echo "等待 1 分钟..."
    sleep 60
else
    echo "[1/4] 跳过导入数据..."
fi

# Step 2: 评估
echo "[2/4] 评估..."
"$PYTHON_BIN" "$SCRIPT_DIR/run_eval.py" "$INPUT_FILE" --output "$RESULT_FILE"

# Step 3: 裁判打分
echo "[3/4] 裁判打分..."
"$PYTHON_BIN" "$SCRIPT_DIR/judge.py" --input "$RESULT_FILE" --parallel 40

# Step 4: 计算结果
echo "[4/4] 计算结果..."
"$PYTHON_BIN" "$SCRIPT_DIR/stat_judge_result.py" --input "$RESULT_FILE"

echo "完成!"
