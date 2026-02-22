#!/bin/bash
# ov-memory-session-end.sh
# Hook: SessionEnd
#
# WHAT: Archive the full session conversation into OpenViking when a session closes.
#
# PSEUDOCODE:
#   read stdin → transcript_path, reason
#   if no transcript → exit
#   parse transcript → keep user/assistant text messages only
#   if no messages → exit
#   if last msg is not assistant → sleep + re-read (race condition)
#   ov session new → get session_id
#   if no session_id → exit (ov server likely down)
#   for each message → log + ov session add-message (content truncated in log)
#   log: ov session commit
#   background: ov session commit → triggers LLM memory extraction → log result
#
# SPECIAL CASES:
#   reason=clear              — user ran /clear; session wiped intentionally
#   reason=logout             — user logged out of Claude Code
#   reason=prompt_input_exit  — Ctrl+C or natural exit
#   race condition            — SessionEnd fires before the final assistant response is
#                               flushed; retry once after brief sleep
#   failed session new        — ov server is down; skip gracefully
#   nohup background          — session process exits before LLM extraction finishes

LOG=/tmp/ov.log

_log()    { [ "$OV_HOOK_DEBUG" = "1" ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
_logcmd() { [ "$OV_HOOK_DEBUG" = "1" ] && printf "\033[90m%s\033[0m \033[35m%s\033[0m\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
_trunc()  { printf '%s' "$1" | python3 -c "import sys; s=sys.stdin.read(); print(s[:120]+('...' if len(s)>120 else ''), end='')"; }

INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
REASON=$(echo "$INPUT" | jq -r '.reason // "other"')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  _log "SessionEnd: no transcript (reason=$REASON)"
  exit 0
fi

MESSAGES=$(jq -sc '
  map(select(.type == "user" or .type == "assistant"))
  | map({
      role: .message.role,
      content: (
        .message.content
        | if type == "string" then .
          elif type == "array" then (map(select(.type == "text") | .text) | join("\n"))
          else ""
          end
      )
    })
  | map(select(.content != "" and .content != null))
' "$TRANSCRIPT")

COUNT=$(echo "$MESSAGES" | jq 'length')

if [ "$COUNT" -eq 0 ]; then
  _log "SessionEnd: no messages (reason=$REASON)"
  exit 0
fi

# Race condition: SessionEnd fires before the final assistant response is flushed to disk.
# Retry once after a brief wait if the last captured entry is not from the assistant.
LAST_ROLE=$(echo "$MESSAGES" | jq -r '.[-1].role // empty')
if [ "$LAST_ROLE" != "assistant" ]; then
  sleep 0.5
  MESSAGES=$(jq -sc '
    map(select(.type == "user" or .type == "assistant"))
    | map({
        role: .message.role,
        content: (
          .message.content
          | if type == "string" then .
            elif type == "array" then (map(select(.type == "text") | .text) | join("\n"))
            else ""
            end
        )
      })
    | map(select(.content != "" and .content != null))
  ' "$TRANSCRIPT")
  COUNT=$(echo "$MESSAGES" | jq 'length')
fi

_logcmd "ov session new -o json -c"
OV_RAW=$(ov session new -o json -c 2>>"$LOG")
OV_SESSION_ID=$(echo "$OV_RAW" | jq -r '.result.session_id // empty')

if [ -z "$OV_SESSION_ID" ]; then
  _log "SessionEnd: failed to create ov session"
  exit 0
fi

while IFS= read -r msg; do
  ROLE=$(echo "$msg" | jq -r '.role')
  CONTENT=$(echo "$msg" | jq -r '.content')
  _logcmd "ov session add-message --role '$ROLE' --content '$(_trunc "$CONTENT")' $OV_SESSION_ID"
  ov session add-message --role "$ROLE" --content "$CONTENT" "$OV_SESSION_ID" > /dev/null 2>&1
done < <(echo "$MESSAGES" | jq -c '.[]')

_logcmd "ov session commit $OV_SESSION_ID"
nohup bash -c "
  ov session commit '$OV_SESSION_ID' >> '$LOG' 2>&1
  [ \"\$OV_HOOK_DEBUG\" = '1' ] && echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] SessionEnd: committed $COUNT msgs (ov=$OV_SESSION_ID, reason=$REASON)\" >> '$LOG'
" > /dev/null 2>&1 &

_log "SessionEnd: queued commit $COUNT msgs (ov=$OV_SESSION_ID, reason=$REASON)"
