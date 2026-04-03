#!/usr/bin/env bash
# Shared helpers for OpenViking Claude Code hooks.

set -uo pipefail

# Read stdin if available (hooks receive JSON input), with timeout to avoid hang
if [[ -t 0 ]]; then
  INPUT=""
else
  INPUT="$(timeout 3 cat 2>/dev/null || true)"
fi

for p in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/bin" "/usr/local/bin"; do
  [[ -d "$p" ]] && [[ ":$PATH:" != *":$p:"* ]] && export PATH="$p:$PATH"
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

STATE_DIR="$PROJECT_DIR/.openviking/memory"
STATE_FILE="$STATE_DIR/session_state.json"
LOG_DIR="$STATE_DIR/logs"
PENDING_DIR="$STATE_DIR/pending"
ARCHIVE_DIR="$STATE_DIR/archive"
BRIDGE="$PLUGIN_ROOT/scripts/ov_memory.py"
PENDING_WARN_COUNT="${OPENVIKING_PENDING_WARN_COUNT:-20}"
PENDING_MAX_COUNT="${OPENVIKING_PENDING_MAX_COUNT:-60}"
PENDING_STALE_MINUTES="${OPENVIKING_PENDING_STALE_MINUTES:-360}"

# Search for ov.conf: project dir → upward → plugin root → ~/.openviking/
_find_ov_conf() {
  local dir="$PROJECT_DIR"
  local depth=0
  while [[ -n "$dir" && "$dir" != "/" && "$dir" != "." && $depth -lt 10 ]]; do
    if [[ -f "$dir/ov.conf" ]]; then echo "$dir/ov.conf"; return 0; fi
    local parent
    parent="$(dirname "$dir")"
    [[ "$parent" == "$dir" ]] && break
    dir="$parent"
    depth=$((depth + 1))
  done
  if [[ -f "$PLUGIN_ROOT/ov.conf" ]]; then echo "$PLUGIN_ROOT/ov.conf"; return 0; fi
  if [[ -f "$HOME/.openviking/ov.conf" ]]; then echo "$HOME/.openviking/ov.conf"; return 0; fi
  echo ""
  return 0
}
OV_CONF="$(_find_ov_conf)" || true

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
  mkdir -p "$STATE_DIR" "$LOG_DIR" "$PENDING_DIR" "$ARCHIVE_DIR"
}

maintain_pending_queue() {
  ensure_state_dir

  if [[ -z "$PYTHON_BIN" ]]; then
    echo '{"ok": false, "error": "python not found"}'
    return 1
  fi

  "$PYTHON_BIN" - "$STATE_FILE" "$PENDING_DIR" "$ARCHIVE_DIR" "$PENDING_WARN_COUNT" "$PENDING_MAX_COUNT" "$PENDING_STALE_MINUTES" <<'PY'
import json
import shutil
import sys
import time
from pathlib import Path


state_file, pending_dir, archive_dir, warn_count, max_count, stale_minutes = sys.argv[1:7]
pending_path = Path(pending_dir)
archive_root = Path(archive_dir)
state_path = Path(state_file)
now = time.time()

warn_count = max(int(warn_count or 20), 1)
max_count = max(int(max_count or 60), warn_count)
stale_seconds = max(int(stale_minutes or 360), 1) * 60

current_pending = ""
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        current_pending = str(state.get("pending_commit_file") or "").strip()
    except Exception:
        current_pending = ""

pending_files = sorted(
    [p for p in pending_path.glob("*.json") if p.is_file()],
    key=lambda p: p.stat().st_mtime,
)
count_before = len(pending_files)
archived = []
warn_reasons = []

eligible = []
for item in pending_files:
    age_seconds = max(0, int(now - item.stat().st_mtime))
    if current_pending and str(item) == current_pending:
        continue
    if age_seconds >= stale_seconds:
        eligible.append((item, age_seconds))

if count_before > max_count and not eligible:
    warn_reasons.append("backlog-over-limit-without-stale-files")

if eligible:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
    archive_path = archive_root / f"pending-{stamp}"
    archive_path.mkdir(parents=True, exist_ok=True)
    for item, age_seconds in eligible:
        target = archive_path / item.name
        shutil.move(str(item), str(target))
        archived.append(
            {
                "name": item.name,
                "path": str(target),
                "age_seconds": age_seconds,
            }
        )

pending_after = sorted(
    [p for p in pending_path.glob("*.json") if p.is_file()],
    key=lambda p: p.stat().st_mtime,
)
count_after = len(pending_after)
if count_after >= warn_count:
    warn_reasons.append("backlog-warning-threshold")

print(
    json.dumps(
        {
            "ok": True,
            "pending_count_before": count_before,
            "pending_count_after": count_after,
            "archived_count": len(archived),
            "archive_dir": str(archive_path) if archived else "",
            "warn": bool(warn_reasons),
            "warn_reasons": warn_reasons,
            "archived": archived[:20],
        }
    )
)
PY
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
  # OpenViking logs to stdout; extract only the last JSON line
  local raw
  raw="$("$PYTHON_BIN" "$BRIDGE" \
    --project-dir "$PROJECT_DIR" \
    --state-file "$STATE_FILE" \
    --ov-conf "$OV_CONF" \
    "$@" 2>/dev/null)"
  # Return only the last line that starts with '{'
  printf '%s\n' "$raw" | grep '^{' | tail -1
}

queue_session_end_commit() {
  if [[ -z "$PYTHON_BIN" ]]; then
    echo '{"ok": false, "error": "python not found"}'
    return 1
  fi
  if [[ ! -f "$BRIDGE" ]]; then
    echo '{"ok": false, "error": "bridge script not found"}'
    return 1
  fi
  if [[ ! -f "$STATE_FILE" ]]; then
    echo '{"ok": false, "error": "state file not found"}'
    return 1
  fi

  ensure_state_dir

  "$PYTHON_BIN" - "$PYTHON_BIN" "$BRIDGE" "$PROJECT_DIR" "$STATE_FILE" "$OV_CONF" "$LOG_DIR" "$PENDING_DIR" <<'PY'
import json
import os
import subprocess
import sys
import time
from pathlib import Path


python_bin, bridge, project_dir, state_file, ov_conf, log_dir, pending_dir = sys.argv[1:8]
state_path = Path(state_file)

try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"ok": False, "error": f"failed to read state file: {exc}"}))
    raise SystemExit(1)

session_id = str(state.get("session_id") or "").strip()
if not state.get("active") or not session_id:
    print(
        json.dumps(
            {
                "ok": True,
                "queued": False,
                "status_line": "[openviking-memory] no active session",
            }
        )
    )
    raise SystemExit(0)

now = int(time.time())
log_dir_path = Path(log_dir)
pending_dir_path = Path(pending_dir)
log_dir_path.mkdir(parents=True, exist_ok=True)
pending_dir_path.mkdir(parents=True, exist_ok=True)

pending_path = pending_dir_path / f"{session_id}.json"
log_path = log_dir_path / f"session-end-{session_id}-{now}.log"

pending_state = dict(state)
pending_state["commit_requested_at"] = now
pending_state["commit_mode"] = "detached"
pending_state["commit_in_progress"] = False
pending_state["pending_commit_log"] = str(log_path)
pending_state["last_commit_error"] = ""
pending_path.write_text(
    json.dumps(pending_state, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

command = [
    python_bin,
    bridge,
    "--project-dir",
    project_dir,
    "--state-file",
    str(pending_path),
]
if ov_conf:
    command += ["--ov-conf", ov_conf]
command += ["session-end"]

header = (
    f"ts={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}\n"
    f"session_id={session_id}\n"
    f"mode=detached\n"
    f"pending_file={pending_path}\n"
    f"command={' '.join(command)}\n"
    "---\n"
)

try:
    with open(log_path, "ab", buffering=0) as log_handle:
        log_handle.write(header.encode("utf-8"))
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "cwd": project_dir,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"ok": False, "error": f"failed to queue session-end commit: {exc}"}))
    raise SystemExit(1)

live_state = dict(state)
live_state["active"] = False
live_state["commit_requested_at"] = now
live_state["commit_mode"] = "detached"
live_state["commit_in_progress"] = False
live_state["pending_commit_file"] = str(pending_path)
live_state["pending_commit_log"] = str(log_path)
live_state["last_commit_error"] = ""
state_path.write_text(
    json.dumps(live_state, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(
    json.dumps(
        {
            "ok": True,
            "queued": True,
            "session_id": session_id,
            "pending_file": str(pending_path),
            "log_file": str(log_path),
            "status_line": f"[openviking-memory] session commit queued id={session_id}",
        }
    )
)
PY
}
