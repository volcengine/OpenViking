#!/usr/bin/env bash
# SessionStart hook: initialize OpenViking memory session.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Skip subagent sessions — they create empty server overhead
AGENT_ID="$(_json_val "$INPUT" "agent_id" "")"
if [[ -n "$AGENT_ID" ]]; then
  echo '{}'
  exit 0
fi

if [[ -z "$OV_CONF" || ! -f "$OV_CONF" ]]; then
  msg='[openviking-memory] ERROR: ov.conf not found (checked project dir, parents, plugin root, ~/.openviking/)'
  json_msg=$(_json_encode_str "$msg")
  echo "{\"systemMessage\": $json_msg}"
  exit 0
fi

PENDING_STATUS="$(maintain_pending_queue 2>/dev/null || true)"
OUT="$(run_bridge session-start 2>/dev/null || true)"
OK="$(_json_val "$OUT" "ok" "false")"
STATUS="$(_json_val "$OUT" "status_line" "[openviking-memory] initialization failed")"
ADDL="$(_json_val "$OUT" "additional_context" "")"
ARCHIVED_COUNT="$(_json_val "$PENDING_STATUS" "archived_count" "0")"
PENDING_AFTER="$(_json_val "$PENDING_STATUS" "pending_count_after" "0")"
WARN_BACKLOG="$(_json_val "$PENDING_STATUS" "warn" "false")"

if [[ "$ARCHIVED_COUNT" != "0" ]]; then
  STATUS="$STATUS archived_stale_pending=$ARCHIVED_COUNT"
fi
if [[ "$WARN_BACKLOG" == "true" && "$PENDING_AFTER" != "0" ]]; then
  STATUS="$STATUS pending_backlog=$PENDING_AFTER"
fi

json_status=$(_json_encode_str "$STATUS")

MODE="$(_json_val "$OUT" "mode" "")"

if [[ "$OK" == "true" ]]; then
  # Recall recent session history (skip when offline — server unreachable)
  HISTORY=""
  if [[ "$MODE" != "offline" ]]; then
    HISTORY="$(run_bridge recall --query "recent decisions changes fixes patterns" --top-k 5 2>/dev/null || true)"
  fi
  HIST_TEXT=""
  if [[ -n "$HISTORY" ]]; then
    HIST_TEXT="$(_json_val "$HISTORY" "formatted" "")"
  fi

  FULL_CTX="${ADDL}"
  if [[ -n "$HIST_TEXT" ]]; then
    FULL_CTX="${FULL_CTX}\n\nOpenViking session history:\n${HIST_TEXT}"
  fi

  if [[ -n "$FULL_CTX" ]]; then
    json_addl=$(_json_encode_str "$FULL_CTX")
    echo "{\"systemMessage\": $json_status, \"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $json_addl}}"
    exit 0
  fi
fi

echo "{\"systemMessage\": $json_status}"
