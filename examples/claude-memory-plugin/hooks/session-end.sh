#!/usr/bin/env bash
# SessionEnd hook: commit OpenViking session and extract long-term memories.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Skip subagent sessions
AGENT_ID="$(_json_val "$INPUT" "agent_id" "")"
if [[ -n "$AGENT_ID" ]]; then
  exit 0
fi

if [[ -z "$OV_CONF" || ! -f "$OV_CONF" || ! -f "$STATE_FILE" ]]; then
  exit 0
fi

# Offline sessions: close directly (fast, no network), skip detached queue
SESSION_MODE="$(_json_val "$(cat "$STATE_FILE" 2>/dev/null || true)" "mode" "")"
if [[ "$SESSION_MODE" == "offline" ]]; then
  OUT="$(run_bridge session-end 2>/dev/null || true)"
else
  OUT="$(queue_session_end_commit 2>/dev/null || run_bridge session-end 2>/dev/null || true)"
fi
STATUS="$(_json_val "$OUT" "status_line" "")"

if [[ -n "$STATUS" ]]; then
  json_status=$(_json_encode_str "$STATUS")
  echo "{\"systemMessage\": $json_status}"
  exit 0
fi

exit 0
