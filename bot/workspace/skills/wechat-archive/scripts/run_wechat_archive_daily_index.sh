#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/run_wechat_archive_agent.sh" index \
  --embedding-text-source "content_only" \
  --semantic-concurrency 2 \
  --embedding-concurrency 4
