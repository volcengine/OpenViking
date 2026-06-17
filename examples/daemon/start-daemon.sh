#!/bin/bash
# Start OpenViking with Active Daemon enabled
set -e

echo "Starting OpenViking with Active Daemon..."

export OV_DAEMON_ENABLED=true
export OV_DAEMON_WATCH_DIR="${OV_DAEMON_WATCH_DIR:-$HOME/.claude/projects}"
export OV_DAEMON_BATCH_LINES="${OV_DAEMON_BATCH_LINES:-50}"
export OV_DAEMON_BATCH_SECONDS="${OV_DAEMON_BATCH_SECONDS:-300}"

openviking serve "$@"
