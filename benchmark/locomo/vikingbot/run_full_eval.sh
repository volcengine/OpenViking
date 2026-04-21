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
