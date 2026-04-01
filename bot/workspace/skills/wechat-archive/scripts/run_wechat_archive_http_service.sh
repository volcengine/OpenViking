#!/usr/bin/env bash
set -euo pipefail

OPENVIKING_REPO_DIR="${OPENVIKING_REPO_DIR:-$HOME/github/OpenViking}"
PYTHON_BIN="${OPENVIKING_SERVER_PYTHON:-$OPENVIKING_REPO_DIR/.venv/bin/python}"
CONFIG_PATH="${OPENVIKING_SERVER_CONFIG:-$HOME/.openviking/wechat_archive_local_gpu_server.conf}"
HOST="${OPENVIKING_SERVER_HOST:-127.0.0.1}"
PORT="${OPENVIKING_SERVER_PORT:-1934}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

if [ ! -f "$CONFIG_PATH" ]; then
  echo "missing OpenViking server config at $CONFIG_PATH" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m openviking.server.bootstrap \
  --config "$CONFIG_PATH" \
  --host "$HOST" \
  --port "$PORT"
