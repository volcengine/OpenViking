#!/bin/bash

# Kill OpenViking Server and vikingbot processes
# Usage: ./kill_openviking_server.sh

set -e

echo "=========================================="
echo "Stopping OpenViking processes"
echo "=========================================="

# Kill existing vikingbot processes
echo ""
echo "Step 1: Stopping vikingbot processes..."
if pgrep -f "vikingbot.*openapi" > /dev/null 2>&1 || pgrep -f "vikingbot.*gateway" > /dev/null 2>&1; then
    pkill -f "vikingbot.*openapi" 2>/dev/null || true
    pkill -f "vikingbot.*gateway" 2>/dev/null || true
    sleep 2
    echo "  ✓ Stopped vikingbot processes"
else
    echo "  ✓ No vikingbot processes found"
fi

# Kill existing openviking-server processes
echo ""
echo "Step 2: Stopping openviking-server processes..."
if pgrep -f "openviking-server" > /dev/null 2>&1; then
    pkill -f "openviking-server" 2>/dev/null || true
    sleep 2
    # Force kill if still running
    if pgrep -f "openviking-server" > /dev/null 2>&1; then
        echo "  Force killing remaining processes..."
        pkill -9 -f "openviking-server" 2>/dev/null || true
        sleep 1
    fi
    echo "  ✓ Stopped openviking-server processes"
else
    echo "  ✓ No openviking-server processes found"
fi

echo ""
echo "=========================================="
echo "✓ All processes stopped"
echo "=========================================="