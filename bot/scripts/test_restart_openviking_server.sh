#!/bin/bash

# Restart OpenViking Server with Test Config (~/.openviking_test/ov.conf)
# Usage: ./test_restart_openviking_server.sh [--port PORT] [--bot-url URL]

set -e

# Default values
PORT="1933"
BOT_URL="http://localhost:18790"
TEST_CONFIG="$HOME/.openviking_test/ov.conf"
TEST_DATA_DIR="$HOME/.openviking_test/data"

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
echo "Restarting OpenViking Server (TEST MODE)"
echo "=========================================="
echo "OpenViking Server Port: $PORT"
echo "Bot URL: $BOT_URL"
echo "Bot Port: $BOT_PORT"
echo "Config File: $TEST_CONFIG"
echo "Data Dir: $TEST_DATA_DIR"
echo ""

# Step 0: Clean up test data directory
echo "Step 0: Cleaning up test data directory..."
if [ -d "$TEST_DATA_DIR" ]; then
    rm -rf "$TEST_DATA_DIR"
    echo "  ✓ Removed $TEST_DATA_DIR"
fi
mkdir -p "$TEST_DATA_DIR"
echo "  ✓ Created clean $TEST_DATA_DIR"

# Step 1: Kill existing vikingbot processes
echo ""
echo "Step 1: Stopping existing vikingbot processes..."
if pgrep -f "vikingbot.*openapi" > /dev/null 2>&1 || pgrep -f "vikingbot.*gateway" > /dev/null 2>&1; then
    pkill -f "vikingbot.*openapi" 2>/dev/null || true
    pkill -f "vikingbot.*gateway" 2>/dev/null || true
    sleep 2
    echo "  ✓ Stopped existing vikingbot processes"
else
    echo "  ✓ No existing vikingbot processes found"
fi

# Step 2: Kill existing openviking-server processes
echo ""
echo "Step 2: Stopping existing openviking-server processes..."
if pgrep -f "openviking-server" > /dev/null 2>&1; then
    pkill -f "openviking-server" 2>/dev/null || true
    sleep 2
    # Force kill if still running
    if pgrep -f "openviking-server" > /dev/null 2>&1; then
        echo "  Force killing remaining processes..."
        pkill -9 -f "openviking-server" 2>/dev/null || true
        sleep 1
    fi
    echo "  ✓ Stopped existing processes"
else
    echo "  ✓ No existing processes found"
fi

# Step 3: Wait for port to be released
echo ""
echo "Step 3: Waiting for port $PORT to be released..."
for i in {1..10}; do
    if ! lsof -i :"$PORT" > /dev/null 2>&1; then
        echo "  ✓ Port $PORT is free"
        break
    fi
    sleep 1
done

# Step 4: Verify test config exists
echo ""
echo "Step 4: Checking test config..."
if [ ! -f "$TEST_CONFIG" ]; then
    echo "  ✗ Config file not found: $TEST_CONFIG"
    echo ""
    echo "Please create $TEST_CONFIG with:"
    echo "  - storage.workspace = $TEST_DATA_DIR"
    echo "  - memory.version = \"v2\" (optional)"
    echo ""
    exit 1
fi
echo "  ✓ Using config: $TEST_CONFIG"

# Step 5: Start openviking-server with test config
echo ""
echo "Step 5: Starting openviking-server with TEST config..."
echo "  Config: $TEST_CONFIG"
echo "  Command: OPENVIKING_CONFIG_FILE=$TEST_CONFIG openviking-server --with-bot --port $PORT --bot-url $BOT_URL"
echo ""

# Set environment variable to use test config
export OPENVIKING_CONFIG_FILE="$TEST_CONFIG"

# Start server
openviking-server \
    --with-bot \
    --port "$PORT" \
    --bot-url "$BOT_URL"

SERVER_PID=$!
echo "  Server PID: $SERVER_PID"

# Step 6: Wait for server to start
echo ""
echo "Step 6: Waiting for server to be ready..."
sleep 3

# First check if server is responding at all
for i in {1..10}; do
    if curl -s http://localhost:"$PORT"/api/v1/bot/health > /dev/null 2>&1; then
        echo ""
        echo "=========================================="
        echo "✓ OpenViking Server started successfully! (TEST MODE)"
        echo "=========================================="
        echo ""
        echo "Server URL: http://localhost:$PORT"
        echo "Config File: $TEST_CONFIG"
        echo "Data Dir: $TEST_DATA_DIR"
        echo "Health Check: http://localhost:$PORT/api/v1/bot/health"
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
echo "✗ Failed to start OpenViking Server (TEST MODE)"
echo "=========================================="
echo ""
echo "Config used: $TEST_CONFIG"
echo "Data dir: $TEST_DATA_DIR"
echo ""
echo "Troubleshooting:"
echo "  1. Check if port $PORT is in use: lsof -i :$PORT"
echo "  2. Check Vikingbot is running on $BOT_URL"
echo "  3. Verify config file exists: $TEST_CONFIG"
echo ""
exit 1
