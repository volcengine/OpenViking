#!/usr/bin/env bash
# UserPromptSubmit hook: lightweight memory availability hint.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

PROMPT="$(_json_val "$INPUT" "prompt" "")"
if [[ -z "$PROMPT" || ${#PROMPT} -lt 10 ]]; then
  echo '{}'
  exit 0
fi

if [[ ! -f "$OV_CONF" || ! -f "$STATE_FILE" ]]; then
  echo '{}'
  exit 0
fi

echo '{"systemMessage":"[openviking-memory] Memory available (use memory-recall when historical context matters)"}'
