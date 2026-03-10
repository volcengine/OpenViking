#!/bin/bash

# test_all.sh - Run all tests without triggering uv sync
# Usage: scripts/test_all.sh

set -e

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo "Running OpenViking Tests"
echo "=========================================="
echo ""

# Check if virtual environment exists
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment not found at $VENV_PYTHON"
    echo "Please create a virtual environment first."
    exit 1
fi

echo "Using Python from virtual environment: $VENV_PYTHON"
echo ""

# Change to project root
cd "$PROJECT_ROOT"

# Run pytest directly
echo "Running tests..."
echo "-----------------------------------------"
"$VENV_PYTHON" -m pytest tests/ -v
TEST_EXIT_CODE=$?
echo "-----------------------------------------"
echo ""

# Show summary
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "=========================================="
    echo "✅ All tests passed!"
    echo "=========================================="
else
    echo "=========================================="
    echo "❌ Some tests failed (exit code: $TEST_EXIT_CODE)"
    echo "=========================================="
fi

exit $TEST_EXIT_CODE
