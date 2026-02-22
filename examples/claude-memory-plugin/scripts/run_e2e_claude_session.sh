#!/usr/bin/env bash
# One-click E2E test runner for OpenViking Claude memory plugin.
#
# This script runs a real Claude Code headless session and validates:
# 1) SessionStart/UserPromptSubmit hooks executed
# 2) Stop hook ingested at least one turn
# 3) SessionEnd committed the OpenViking session
# 4) Session archive file was created
#
# Default source config:
#   /Users/quemingjian/Source/OpenViking/ov.conf

set -euo pipefail

SOURCE_OV_CONF_DEFAULT="/Users/quemingjian/Source/OpenViking/ov.conf"
PROMPT_DEFAULT="请只回复: E2E_HTTP_OK"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"

SOURCE_OV_CONF="${1:-$SOURCE_OV_CONF_DEFAULT}"
PROMPT="${2:-$PROMPT_DEFAULT}"

if [[ ! -f "$SOURCE_OV_CONF" ]]; then
  echo "ERROR: source ov.conf not found: $SOURCE_OV_CONF" >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI not found in PATH." >&2
  exit 1
fi

PYTHON_BIN=""
for c in /opt/homebrew/bin/python3.11 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "$c")"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: Python not found." >&2
  exit 1
fi

VENV_DIR="${TMPDIR:-/tmp}/ov-claude-e2e-venv-$(id -u)"
LOG_DIR="${TMPDIR:-/tmp}/ov-claude-e2e-logs"
mkdir -p "$LOG_DIR"
CLAUDE_DEBUG_LOG="$LOG_DIR/claude-plugin-e2e.log"
SERVER_LOG="$LOG_DIR/openviking-server.log"
STATE_FILE="$PROJECT_ROOT/.openviking/memory/session_state.json"
NOW_TS="$(date +%s)"
TEST_PLUGIN_DIR="${TMPDIR:-/tmp}/ov-plugin-e2e-${NOW_TS}"
TMP_DATA_DIR="${TMPDIR:-/tmp}/ov-claude-e2e-data-${NOW_TS}"
mkdir -p "$TMP_DATA_DIR"
SERVER_OV_CONF="${LOG_DIR}/ov-server-${NOW_TS}.conf.json"

cleanup() {
  local code=$?
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${ORIG_OV_CONF_BACKUP:-}" && -f "${ORIG_OV_CONF_BACKUP}" ]]; then
    mv -f "${ORIG_OV_CONF_BACKUP}" "$PROJECT_ROOT/ov.conf"
  elif [[ -f "$PROJECT_ROOT/ov.conf" && -n "${CREATED_OV_CONF:-}" ]]; then
    python3 - <<'PY' "$PROJECT_ROOT/ov.conf"
import os, sys
try:
    os.unlink(sys.argv[1])
except FileNotFoundError:
    pass
except OSError:
    pass
PY
  fi

  if [[ -n "${ENGINE_LINK_CREATED:-}" ]]; then
    python3 - <<'PY' "$PROJECT_ROOT/openviking/storage/vectordb/engine.so"
import os, sys
p = sys.argv[1]
if os.path.islink(p):
    os.unlink(p)
PY
  fi

  if [[ -n "${AGFS_LINK_CREATED:-}" ]]; then
    python3 - <<'PY' "$PROJECT_ROOT/openviking/bin/agfs-server"
import os, sys
p = sys.argv[1]
if os.path.islink(p):
    os.unlink(p)
PY
  fi

  if [[ $code -ne 0 ]]; then
    echo
    echo "E2E FAILED. Debug files:"
    echo "  Claude log: $CLAUDE_DEBUG_LOG"
    echo "  Server log: $SERVER_LOG"
    echo "  State file: $STATE_FILE"
  fi

  if [[ -d "$TEST_PLUGIN_DIR" ]]; then
    python3 - <<'PY' "$TEST_PLUGIN_DIR"
import shutil, sys
shutil.rmtree(sys.argv[1], ignore_errors=True)
PY
  fi
  exit $code
}
trap cleanup EXIT

echo "==> Preparing Python environment: $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
python --version

if ! python - <<'PY' >/dev/null 2>&1
import yaml, pydantic, httpx, typer, pyagfs
PY
then
  echo "==> Installing runtime dependencies (first run may take a few minutes)"
  pip install --upgrade pip setuptools wheel >/dev/null
  pip install \
    pyyaml pydantic httpx typer requests tabulate jinja2 fastapi uvicorn \
    openai markdownify readabilipy pdfplumber python-docx python-pptx openpyxl \
    ebooklib json-repair apscheduler volcengine "volcengine-python-sdk[ark]" \
    xxhash urllib3 protobuf "pdfminer-six>=20231228" >/dev/null
  pip install "$PROJECT_ROOT/third_party/agfs/agfs-sdk/python" >/dev/null
fi

echo "==> Ensuring local build artifacts are available"
SOURCE_REPO_DIR="$(cd "$(dirname "$SOURCE_OV_CONF")" && pwd)"
ENGINE_TARGET="$PROJECT_ROOT/openviking/storage/vectordb/engine.so"
SOURCE_ENGINE="$SOURCE_REPO_DIR/openviking/storage/vectordb/engine.so"
if [[ ! -f "$ENGINE_TARGET" && -f "$SOURCE_ENGINE" ]]; then
  ln -sf "$SOURCE_ENGINE" "$ENGINE_TARGET"
  ENGINE_LINK_CREATED=1
fi

mkdir -p "$PROJECT_ROOT/openviking/bin"
AGFS_TARGET="$PROJECT_ROOT/openviking/bin/agfs-server"
SOURCE_AGFS="$SOURCE_REPO_DIR/openviking/bin/agfs-server"
if [[ ! -f "$AGFS_TARGET" && -f "$SOURCE_AGFS" ]]; then
  ln -sf "$SOURCE_AGFS" "$AGFS_TARGET"
  AGFS_LINK_CREATED=1
fi

if [[ ! -f "$ENGINE_TARGET" ]]; then
  echo "ERROR: engine.so missing at $ENGINE_TARGET and no fallback found." >&2
  exit 1
fi
if [[ ! -f "$AGFS_TARGET" ]]; then
  echo "ERROR: agfs-server missing at $AGFS_TARGET and no fallback found." >&2
  exit 1
fi

PORT_JSON="$(
python - <<'PY'
import socket, json
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
print(json.dumps({"ov_port": free_port(), "agfs_port": free_port()}))
PY
)"
OV_PORT="$(python - <<'PY' "$PORT_JSON"
import json,sys
print(json.loads(sys.argv[1])["ov_port"])
PY
)"
AGFS_PORT="$(python - <<'PY' "$PORT_JSON"
import json,sys
print(json.loads(sys.argv[1])["agfs_port"])
PY
)"

echo "==> Generating configs for test (HTTP mode on 127.0.0.1:$OV_PORT)"
if [[ -e "$PROJECT_ROOT/ov.conf" ]]; then
  ORIG_OV_CONF_BACKUP="$PROJECT_ROOT/ov.conf.e2e.bak.${NOW_TS}"
  mv "$PROJECT_ROOT/ov.conf" "$ORIG_OV_CONF_BACKUP"
fi

python - <<'PY' "$SOURCE_OV_CONF" "$PROJECT_ROOT/ov.conf" "$SERVER_OV_CONF" "$OV_PORT" "$AGFS_PORT" "$TMP_DATA_DIR"
import json, sys, copy, os
src, plugin_cfg_path, server_cfg_path, ov_port, agfs_port, data_dir = sys.argv[1:]
with open(src, "r", encoding="utf-8") as f:
    cfg = json.load(f)

base = copy.deepcopy(cfg)
base["storage"] = {
    "vectordb": {
        "name": "context",
        "backend": "local",
        "path": data_dir,
    },
    "agfs": {
        "backend": "local",
        "path": data_dir,
        "port": int(agfs_port),
        "log_level": "warn",
    },
}

# Plugin config: includes server hint for HTTP auto-detection in hooks.
plugin_cfg = copy.deepcopy(base)
plugin_cfg["server"] = {
    "host": "127.0.0.1",
    "port": int(ov_port),
    "api_key": None,
}

os.makedirs(os.path.dirname(plugin_cfg_path), exist_ok=True)
with open(plugin_cfg_path, "w", encoding="utf-8") as f:
    json.dump(plugin_cfg, f, ensure_ascii=False, indent=2)

# Server config: must NOT include unknown top-level fields like "server".
with open(server_cfg_path, "w", encoding="utf-8") as f:
    json.dump(base, f, ensure_ascii=False, indent=2)

print(plugin_cfg_path)
print(server_cfg_path)
PY
CREATED_OV_CONF=1

echo "==> Starting OpenViking HTTP server"
PYTHONPATH="$PROJECT_ROOT" python -m openviking.server.bootstrap \
  --host 127.0.0.1 \
  --port "$OV_PORT" \
  --config "$SERVER_OV_CONF" \
  >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for i in {1..60}; do
  if curl -fsS "http://127.0.0.1:${OV_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
  if [[ $i -eq 60 ]]; then
    echo "ERROR: OpenViking server did not become healthy." >&2
    exit 1
  fi
done

echo "==> Running real Claude headless session"
mkdir -p "$PROJECT_ROOT/.openviking/memory"
rm -f "$STATE_FILE"

echo "==> Preparing deterministic test plugin copy (Stop hook sync)"
cp -R "$PLUGIN_DIR" "$TEST_PLUGIN_DIR"
python - <<'PY' "$TEST_PLUGIN_DIR/hooks/hooks.json"
import json, sys
p = sys.argv[1]
with open(p, "r", encoding="utf-8") as f:
    d = json.load(f)
stop = d["hooks"]["Stop"][0]["hooks"][0]
stop.pop("async", None)
with open(p, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
PY

PATH="$VENV_DIR/bin:$PATH" PYTHONPATH="$PROJECT_ROOT" \
claude -p \
  --plugin-dir "$TEST_PLUGIN_DIR" \
  --debug hooks \
  --debug-file "$CLAUDE_DEBUG_LOG" \
  "$PROMPT" >/tmp/ov-claude-e2e-response.txt

if [[ ! -f "$STATE_FILE" ]]; then
  # SessionEnd can finish shortly after claude -p returns. Wait briefly for state.
  for i in {1..60}; do
    [[ -f "$STATE_FILE" ]] && break
    sleep 0.5
  done
fi
if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: state file not found: $STATE_FILE" >&2
  exit 1
fi

STATE_SNAPSHOT="$(
python - <<'PY' "$STATE_FILE"
import json,sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    d=json.load(f)
print(f"{d.get('session_id','')}\t{d.get('ingested_turns',0)}\t{str(d.get('active',True)).lower()}\t{d.get('committed_at','')}")
PY
)"
IFS=$'\t' read -r SESSION_ID INGESTED_TURNS ACTIVE_FLAG COMMITTED_AT <<<"$STATE_SNAPSHOT"

# Poll a short time window for commit completion to avoid flaky false negatives.
for i in {1..80}; do
  if [[ -n "$SESSION_ID" && "${INGESTED_TURNS:-0}" -ge 1 && "$ACTIVE_FLAG" == "false" ]]; then
    break
  fi
  sleep 0.5
  STATE_SNAPSHOT="$(
  python - <<'PY' "$STATE_FILE"
import json,sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    d=json.load(f)
print(f"{d.get('session_id','')}\t{d.get('ingested_turns',0)}\t{str(d.get('active',True)).lower()}\t{d.get('committed_at','')}")
PY
  )"
  IFS=$'\t' read -r SESSION_ID INGESTED_TURNS ACTIVE_FLAG COMMITTED_AT <<<"$STATE_SNAPSHOT"
done

find_latest_transcript() {
python - <<'PY' "$HOME/.claude/projects" "$PROJECT_ROOT" "$PROMPT"
import json, sys
from pathlib import Path

base = Path(sys.argv[1]).expanduser()
cwd = sys.argv[2]
prompt = sys.argv[3]
best = None
best_mtime = -1.0

if base.exists():
    for p in base.rglob("*.jsonl"):
        if "/subagents/" in str(p):
            continue
        try:
            mt = p.stat().st_mtime
            if mt < best_mtime:
                continue
            hit_cwd = False
            hit_prompt = False
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("cwd") == cwd:
                        hit_cwd = True
                    if obj.get("type") == "user":
                        content = obj.get("message", {}).get("content", "")
                        if isinstance(content, str) and prompt in content:
                            hit_prompt = True
            if hit_cwd and hit_prompt:
                best = str(p)
                best_mtime = mt
        except Exception:
            pass

print(best or "")
PY
}

LATEST_TRANSCRIPT=""
for i in {1..40}; do
  LATEST_TRANSCRIPT="$(find_latest_transcript)"
  if [[ -n "$LATEST_TRANSCRIPT" && -f "$LATEST_TRANSCRIPT" ]]; then
    break
  fi
  sleep 0.5
done

if [[ -z "$SESSION_ID" ]]; then
  echo "ERROR: session_id missing in state file." >&2
  exit 1
fi
if [[ "${INGESTED_TURNS}" -lt 1 ]]; then
  echo "ERROR: expected ingested_turns >= 1, got $INGESTED_TURNS" >&2
  exit 1
fi
if [[ "$ACTIVE_FLAG" != "false" ]]; then
  echo "ERROR: expected state active=false after session-end, got $ACTIVE_FLAG" >&2
  exit 1
fi
if [[ -z "$LATEST_TRANSCRIPT" || ! -f "$LATEST_TRANSCRIPT" ]]; then
  echo "ERROR: Could not locate transcript for prompt: $PROMPT" >&2
  exit 1
fi

ARCHIVE_FILE="$TMP_DATA_DIR/viking/session/${SESSION_ID}/history/archive_001/messages.jsonl"
for i in {1..40}; do
  [[ -f "$ARCHIVE_FILE" ]] && break
  sleep 0.5
done
if [[ ! -f "$ARCHIVE_FILE" ]]; then
  echo "ERROR: archive file missing: $ARCHIVE_FILE" >&2
  exit 1
fi

echo "==> E2E PASSED"
echo "Claude response: $(cat /tmp/ov-claude-e2e-response.txt)"
echo "Session ID: $SESSION_ID"
echo "Ingested turns: $INGESTED_TURNS"
echo "State file: $STATE_FILE"
echo "Transcript: $LATEST_TRANSCRIPT"
echo "Archive: $ARCHIVE_FILE"
echo "Claude debug log: $CLAUDE_DEBUG_LOG"
echo "Server log: $SERVER_LOG"
echo "Temp data dir: $TMP_DATA_DIR"
