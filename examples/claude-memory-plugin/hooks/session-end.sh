#!/usr/bin/env bash
# SessionEnd hook: commit OpenViking session and extract long-term memories.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if [[ ! -f "$OV_CONF" || ! -f "$STATE_FILE" ]]; then
  exit 0
fi

OUT="$(run_bridge session-end 2>/dev/null || true)"
STATUS="$(_json_val "$OUT" "status_line" "")"

if [[ -n "$STATUS" ]]; then
  json_status=$(_json_encode_str "$STATUS")
  echo "{\"systemMessage\": $json_status}"
  exit 0
fi

exit 0
