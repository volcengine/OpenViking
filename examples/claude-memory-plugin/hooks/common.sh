#!/usr/bin/env bash
# Shared helpers for OpenViking Claude Code hooks.

set -euo pipefail

INPUT="$(cat || true)"

for p in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/bin" "/usr/local/bin"; do
  [[ -d "$p" ]] && [[ ":$PATH:" != *":$p:"* ]] && export PATH="$p:$PATH"
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

STATE_DIR="$PROJECT_DIR/.openviking/memory"
STATE_FILE="$STATE_DIR/session_state.json"
OV_CONF="$PROJECT_DIR/ov.conf"
BRIDGE="$PLUGIN_ROOT/scripts/ov_memory.py"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  PYTHON_BIN=""
fi

_json_val() {
  local json="$1" key="$2" default="${3:-}"
  local result=""

  if command -v jq >/dev/null 2>&1; then
    result=$(printf '%s' "$json" | jq -r ".${key} // empty" 2>/dev/null) || true
  elif [[ -n "$PYTHON_BIN" ]]; then
    result=$(
      "$PYTHON_BIN" -c '
import json, sys
obj = json.loads(sys.argv[1])
val = obj
for k in sys.argv[2].split("."):
    if isinstance(val, dict):
        val = val.get(k)
    else:
        val = None
        break
if val is None:
    print("")
elif isinstance(val, bool):
    print("true" if val else "false")
else:
    print(val)
' "$json" "$key" 2>/dev/null
    ) || true
  fi

  if [[ -z "$result" ]]; then
    printf '%s' "$default"
  else
    printf '%s' "$result"
  fi
}

_json_encode_str() {
  local str="$1"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$str" | jq -Rs .
    return 0
  fi
  if [[ -n "$PYTHON_BIN" ]]; then
    printf '%s' "$str" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
    return 0
  fi
  printf '"%s"' "$str"
}

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

run_bridge() {
  if [[ -z "$PYTHON_BIN" ]]; then
    echo '{"ok": false, "error": "python not found"}'
    return 1
  fi
  if [[ ! -f "$BRIDGE" ]]; then
    echo '{"ok": false, "error": "bridge script not found"}'
    return 1
  fi

  ensure_state_dir
  "$PYTHON_BIN" "$BRIDGE" \
    --project-dir "$PROJECT_DIR" \
    --state-file "$STATE_FILE" \
    "$@"
}
