#!/bin/bash

# Restart OpenViking Server with Bot API enabled
# Usage: ./restart_openviking_server.sh [--port PORT] [--bot-port PORT] [--config PATH] [--data-dir PATH]

set -e

# Default values
PORT="1933"
BOT_PORT="18790"
CONFIG="${OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov.conf}"
DATA_DIR="${OPENVIKING_DATA_DIR:-${HOME}/.openviking/data}"
KILL_ALL_VIKINGBOT="${OPENVIKING_KILL_ALL_VIKINGBOT:-1}"

usage() {
    echo "Usage: $0 [--port PORT] [--bot-port PORT] [--config PATH] [--data-dir PATH] [--no-kill-all-vikingbot]"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --port"
                usage
                exit 1
            fi
            PORT="$2"
            shift 2
            ;;
        --bot-port)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --bot-port"
                usage
                exit 1
            fi
            BOT_PORT="$2"
            shift 2
            ;;
        --config)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --config"
                usage
                exit 1
            fi
            CONFIG="$2"
            shift 2
            ;;
        --data-dir)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --data-dir"
                usage
                exit 1
            fi
            DATA_DIR="$2"
            shift 2
            ;;
        --no-kill-all-vikingbot)
            KILL_ALL_VIKINGBOT=0
            shift 1
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "Restarting OpenViking Server with Bot API"
echo "=========================================="
echo "OpenViking Server Port: $PORT"
echo "Bot Port: $BOT_PORT"
echo "Config: $CONFIG"
echo "Data Directory: $DATA_DIR"
echo ""

# Step 0: Kill process on port and delete data directory
echo "Step 0: Killing process on port $PORT..."
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then
    pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN)
    kill -9 $pid 2>/dev/null || true
    sleep 1
    echo "  ✓ Killed process $pid on port $PORT"
else
    echo "  ✓ No process found on port $PORT"
fi

echo ""
echo "Step 0b: Deleting data directory $DATA_DIR..."
if [ -d "$DATA_DIR" ]; then
    rm -rf "$DATA_DIR"
    echo "  ✓ Deleted $DATA_DIR"
else
    echo "  ✓ Data directory does not exist"
fi

# Kill existing vikingbot processes. Multi-slot launchers pass
# --no-kill-all-vikingbot so one slot restart does not kill other slots.
echo ""
echo "Step 0c: Stopping existing vikingbot processes..."
if [[ "$KILL_ALL_VIKINGBOT" == "1" ]]; then
    if pgrep -f "vikingbot.*openapi" > /dev/null 2>&1 || pgrep -f "vikingbot.*gateway" > /dev/null 2>&1; then
        pkill -f "vikingbot.*openapi" 2>/dev/null || true
        pkill -f "vikingbot.*gateway" 2>/dev/null || true
        sleep 2
        echo "  ✓ Stopped existing vikingbot processes"
    else
        echo "  ✓ No existing vikingbot processes found"
    fi
else
    echo "  ✓ Skipped global vikingbot kill"
fi

echo ""
echo "Step 0d: Killing process on bot port $BOT_PORT..."
if lsof -nP -iTCP:"$BOT_PORT" -sTCP:LISTEN > /dev/null 2>&1; then
    bot_pid=$(lsof -tiTCP:"$BOT_PORT" -sTCP:LISTEN)
    kill -9 $bot_pid 2>/dev/null || true
    sleep 1
    echo "  ✓ Killed process $bot_pid on bot port $BOT_PORT"
else
    echo "  ✓ No process found on bot port $BOT_PORT"
fi

# Step 1: Verify port is free
echo ""
echo "Step 1: Verifying port $PORT is free..."
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then
    echo "  ✗ Port $PORT is still in use, trying to force kill..."
    pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN)
    kill -9 $pid 2>/dev/null || true
    sleep 1
fi
echo "  ✓ Port $PORT is free"

# Step 2: Start openviking-server with --with-bot
echo ""
echo "Step 2: Starting openviking-server with Bot API..."
echo "  Command: OPENVIKING_CONFIG_FILE=$CONFIG openviking-server --with-bot --port $PORT --bot-port $BOT_PORT --config $CONFIG"
echo ""

# Start in background and log to file
#nohup openviking-server \
#    --with-bot \
#    --port "$PORT" \
#    --bot-port "$BOT_PORT" \
#    > /tmp/openviking-server.log 2>&1 &

export OPENVIKING_CONFIG_FILE="$CONFIG"
openviking-server \
    --with-bot \
    --port "$PORT" \
    --bot-port "$BOT_PORT" \
    --config "$CONFIG"


SERVER_PID=$!
echo "  Server PID: $SERVER_PID"

# Step 3: Wait for server to start
echo ""
echo "Step 3: Waiting for server to be ready..."
sleep 3

# First check if bot proxy health reports healthy
for i in {1..10}; do
    health_response=$(curl -fsS http://localhost:"$PORT"/bot/v1/health 2>/dev/null || true)
    if echo "${health_response//[[:space:]]/}" | grep -q '"status":"healthy"'; then
        echo ""
        echo "=========================================="
        echo "✓ OpenViking Server started successfully!"
        echo "=========================================="
        echo ""
        echo "Server URL: http://localhost:$PORT"
        echo "Health Check: http://localhost:$PORT/bot/v1/health"
        echo "Logs: tail -f /tmp/openviking-server.log"
        echo ""
        exit 0
    fi
    # Check actual health response
    if echo "$health_response" | grep -q "Bot service unavailable"; then
        echo "  ⏳ Waiting for Vikingbot to start (attempt $i/10)..."
    fi
    sleep 2
done

# If we reach here, server failed to start
echo ""
echo "=========================================="
echo "✗ Failed to start OpenViking Server"
echo "=========================================="
echo ""
echo "Recent logs:"
tail -20 /tmp/openviking-server.log 2>/dev/null || echo "(No logs available)"
echo ""
echo "Troubleshooting:"
echo "  1. Check if port $PORT is in use: lsof -i :$PORT"
echo "  2. Check Vikingbot is running on port $BOT_PORT"
echo "  3. Check logs: tail -f /tmp/openviking-server.log"
echo ""
exit 1
