#!/usr/bin/env bash
# Stop hook: ingest latest turn into OpenViking session memory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

STOP_HOOK_ACTIVE="$(_json_val "$INPUT" "stop_hook_active" "false")"
if [[ "$STOP_HOOK_ACTIVE" == "true" ]]; then
  echo '{}'
  exit 0
fi

if [[ ! -f "$OV_CONF" || ! -f "$STATE_FILE" ]]; then
  echo '{}'
  exit 0
fi

TRANSCRIPT_PATH="$(_json_val "$INPUT" "transcript_path" "")"
if [[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]]; then
  echo '{}'
  exit 0
fi

run_bridge ingest-stop --transcript-path "$TRANSCRIPT_PATH" >/dev/null 2>&1 || true

echo '{}'
