#!/bin/bash
# VikingBot Gateway 启动脚本

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
# 激活虚拟环境
echo "Uv sync..."
uv sync

# 激活虚拟环境
echo "Activating virtual environment..."
source "$PROJECT_ROOT/.venv/bin/activate"

# 确保日志目录存在
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/output.log"

# 查找并 kill vikingbot gateway 进程
echo "Killing existing vikingbot gateway processes..."
pkill -f "vikingbot gateway" || true
pkill -f "uvicorn" || true
pkill -f "agfs" || true

# 等待进程结束
sleep 1

# 启动 vikingbot gateway
echo "Starting vikingbot gateway..."
nohup vikingbot gateway > "$LOG_FILE" 2>&1 &
PID=$!

echo "VikingBot gateway started with PID: $PID"
echo "Log file: $LOG_FILE"
echo ""
echo "Tailing log file (Ctrl+C to exit)..."
echo "========================================"

# tail 日志文件
tail -f "$LOG_FILE"
