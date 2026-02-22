#!/bin/bash
# ov-memory-subagent-stop.sh
# Hook: SubagentStop
#
# WHAT: Save a finished subagent's conversation into OpenViking memory.
#
# PSEUDOCODE:
#   read stdin → agent_type, transcript_path, last_assistant_message
#   if no transcript → exit
#   parse transcript → keep user/assistant text messages only
#   if no messages → exit
#   if last message is not assistant → append last_assistant_message from payload
#   write messages to tmpfile
#   log: ov add-memory (content truncated)
#   background: ov add-memory <tmpfile> → log result → rm tmpfile
#
# SPECIAL CASES:
#   no transcript      — subagent exited before writing output (e.g. killed early)
#   empty messages     — transcript exists but has no readable text blocks
#   race condition     — SubagentStop fires before assistant response is flushed to
#                        agent_transcript_path; fixed by using last_assistant_message
#                        from the hook payload as a guaranteed fallback
#   nohup background   — ov call survives if parent exits before it completes

LOG=/tmp/ov.log

_log()    { [ "$OV_HOOK_DEBUG" = "1" ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
_logcmd() { [ "$OV_HOOK_DEBUG" = "1" ] && printf "\033[90m%s\033[0m \033[35m%s\033[0m\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
_trunc()  { printf '%s' "$1" | python3 -c "import sys; s=sys.stdin.read(); print(s[:120]+('...' if len(s)>120 else ''), end='')"; }

INPUT=$(cat)
AGENT_TYPE=$(echo "$INPUT" | jq -r '.agent_type // "unknown"')
AGENT_TRANSCRIPT=$(echo "$INPUT" | jq -r '.agent_transcript_path // empty')
LAST_ASSISTANT_TEXT=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')

if [ -z "$AGENT_TRANSCRIPT" ] || [ ! -f "$AGENT_TRANSCRIPT" ]; then
  _log "SubagentStop: no transcript for $AGENT_TYPE"
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
' "$AGENT_TRANSCRIPT")

COUNT=$(echo "$MESSAGES" | jq 'length')

if [ "$COUNT" -eq 0 ]; then
  _log "SubagentStop: no text messages for $AGENT_TYPE"
  exit 0
fi

# Race condition: SubagentStop fires before the final assistant response is flushed
# to agent_transcript_path. If the last captured entry is not from the assistant,
# append last_assistant_message from the hook payload (always present at hook time).
LAST_ROLE=$(echo "$MESSAGES" | jq -r '.[-1].role // empty')
if [ "$LAST_ROLE" != "assistant" ] && [ -n "$LAST_ASSISTANT_TEXT" ]; then
  MESSAGES=$(echo "$MESSAGES" | jq --arg txt "$LAST_ASSISTANT_TEXT" '. + [{role: "assistant", content: $txt}]')
  COUNT=$(echo "$MESSAGES" | jq 'length')
fi

TMPFILE=$(mktemp /tmp/ov-hook-XXXXXX.json)
echo "$MESSAGES" > "$TMPFILE"

_logcmd "ov add-memory '$(jq -c 'map(.content = (.content | if length > 120 then .[0:120] + "..." else . end))' "$TMPFILE")'"
nohup bash -c "
  ov add-memory \"\$(cat $TMPFILE)\" >> '$LOG' 2>&1
  [ \"\$OV_HOOK_DEBUG\" = '1' ] && echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] SubagentStop: saved $COUNT msgs from $AGENT_TYPE\" >> '$LOG'
  rm -f '$TMPFILE'
" > /dev/null 2>&1 &

_log "SubagentStop: queued $COUNT msgs from $AGENT_TYPE"
