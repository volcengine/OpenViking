#!/usr/bin/env bash
set -euo pipefail

OPENVIKING_REPO_DIR="${OPENVIKING_REPO_DIR:-$HOME/github/OpenViking}"
PYTHON_BIN="${OPENVIKING_PYTHON_BIN:-$OPENVIKING_REPO_DIR/.venv/bin/python}"
CLI_PATH="$OPENVIKING_REPO_DIR/examples/wechat_archive_agent.py"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" "$CLI_PATH" "$@"
