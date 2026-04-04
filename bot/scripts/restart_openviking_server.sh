#!/bin/bash

# Restart OpenViking Server with Bot API enabled
# Usage: ./restart_openviking_server.sh [--port PORT] [--bot-url URL]

set -e

# Default values
PORT="1933"
BOT_URL="http://localhost:18790"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        --bot-url)
            BOT_URL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--port PORT] [--bot-url URL]"
            exit 1
            ;;
    esac
done

# Parse Bot URL to extract port
BOT_PORT=$(echo "$BOT_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
if [ -z "$BOT_PORT" ]; then
    BOT_PORT="18790"
fi

echo "=========================================="
echo "Restarting OpenViking Server with Bot API"
echo "=========================================="
echo "OpenViking Server Port: $PORT"
echo "Bot URL: $BOT_URL"
echo "Bot Port: $BOT_PORT"
echo ""

# Step 0: Kill process on port and delete data directory
echo "Step 0: Killing process on port $PORT..."
if lsof -i :"$PORT" > /dev/null 2>&1; then
    pid=$(lsof -ti :"$PORT")
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
    echo "  ✓ Killed process $pid on port $PORT"
else
    echo "  ✓ No process found on port $PORT"
fi

echo ""
echo "Step 0b: Deleting data directory /Users/bytedance/.openviking/data..."
if [ -d "/Users/bytedance/.openviking/data" ]; then
    rm -rf /Users/bytedance/.openviking/data
    echo "  ✓ Deleted /Users/bytedance/.openviking/data"
else
    echo "  ✓ Data directory does not exist"
fi

# Kill existing vikingbot processes
echo ""
echo "Step 0c: Stopping existing vikingbot processes..."
if pgrep -f "vikingbot.*openapi" > /dev/null 2>&1 || pgrep -f "vikingbot.*gateway" > /dev/null 2>&1; then
    pkill -f "vikingbot.*openapi" 2>/dev/null || true
    pkill -f "vikingbot.*gateway" 2>/dev/null || true
    sleep 2
    echo "  ✓ Stopped existing vikingbot processes"
else
    echo "  ✓ No existing vikingbot processes found"
fi

# Step 1: Verify port is free
echo ""
echo "Step 1: Verifying port $PORT is free..."
if lsof -i :"$PORT" > /dev/null 2>&1; then
    echo "  ✗ Port $PORT is still in use, trying to force kill..."
    pid=$(lsof -ti :"$PORT")
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
fi
echo "  ✓ Port $PORT is free"

# Step 2: Start openviking-server with --with-bot
echo ""
echo "Step 2: Starting openviking-server with Bot API..."
echo "  Command: openviking-server --with-bot --port $PORT --bot-url $BOT_URL"
echo ""

# Start in background and log to file
#nohup openviking-server \
    --with-bot \
    --port "$PORT" \
    --bot-url "$BOT_URL" \
    > /tmp/openviking-server.log 2>&1 &

openviking-server \
    --with-bot \
    --port "$PORT" \
    --bot-url "$BOT_URL"


SERVER_PID=$!
echo "  Server PID: $SERVER_PID"

# Step 3: Wait for server to start
echo ""
echo "Step 3: Waiting for server to be ready..."
sleep 3

# First check if server is responding at all
for i in {1..10}; do
    if curl -s http://localhost:"$PORT"/api/v1/bot/health > /dev/null 2>&1; then
        echo ""
        echo "=========================================="
        echo "✓ OpenViking Server started successfully!"
        echo "=========================================="
        echo ""
        echo "Server URL: http://localhost:$PORT"
        echo "Health Check: http://localhost:$PORT/api/v1/bot/health"
        echo "Logs: tail -f /tmp/openviking-server.log"
        echo ""
        exit 0
    fi
    # Check actual health response
    health_response=$(curl -s http://localhost:"$PORT"/api/v1/bot/health 2>/dev/null)
    if echo "$health_response" | grep -q "Vikingbot"; then
        echo "  ✓ Vikingbot is healthy"
    elif echo "$health_response" | grep -q "Bot service unavailable"; then
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
echo "  2. Check Vikingbot is running on $BOT_URL"
echo "  3. Check logs: tail -f /tmp/openviking-server.log"
echo ""
exit 1
