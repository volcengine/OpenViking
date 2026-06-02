#!/usr/bin/env python3
"""
ov_session.py  v2.0  — OpenViking session manager
Called by Windows Scheduled Task (autosave every 30 min) and manually.

Commands:
  python ov_session.py status               -- show current session state
  python ov_session.py start                -- create new session
  python ov_session.py commit [session_id]  -- commit session to extract memories
  python ov_session.py autosave             -- commit if active + start new session
  python ov_session.py add <id> <role> <text>

API notes (OpenViking v0.3.16):
  POST /api/v1/sessions                         -> create session
  POST /api/v1/sessions/{id}/messages           -> add message {role, content}
  POST /api/v1/sessions/{id}/commit             -> extract memories
  GET  /health
  All requests require 4 headers (see HEADERS below).
"""
import sys
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

OV_URL     = os.environ.get("OV_URL",     "http://localhost:1933")
OV_API_KEY = os.environ.get("OV_API_KEY", "YOUR_LOCAL_API_KEY")

HOME       = Path(os.environ["USERPROFILE"])
LOG_FILE   = HOME / ".claude-memory" / "logs" / "session-hooks.log"
STATE_FILE = HOME / ".claude-memory" / ".session_state.json"

HEADERS = {
    "Content-Type":           "application/json",
    "Authorization":          "Bearer " + OV_API_KEY,
    "x-api-key":              OV_API_KEY,
    "x-openviking-user":      "default",
    "x-openviking-account":   "default",
}


def log(msg):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def api_post(path, body=None):
    data = json.dumps(body or {}).encode()
    req  = urllib.request.Request(OV_URL + path, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def health_check():
    try:
        urllib.request.urlopen(OV_URL + "/health", timeout=4)
        return True
    except Exception:
        return False


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log("State save error: {}".format(e))


def create_session():
    try:
        result = api_post("/api/v1/sessions")
        sid = (result.get("result") or {}).get("session_id") or result.get("session_id")
        return sid
    except Exception as e:
        log("Create session error: {}".format(e))
        return None


def commit_session(session_id):
    try:
        result    = api_post("/api/v1/sessions/{}/commit".format(session_id))
        r         = result.get("result") or result or {}
        extracted = r.get("memories_extracted", 0)
        updated   = r.get("active_count_updated", 0)
        archived  = r.get("archived", False)
        log("Committed: {} memories extracted, {} updated, archived={}".format(extracted, updated, archived))
        return True
    except Exception as e:
        log("Commit error: {}".format(e))
        return False


def add_message(session_id, role, content):
    try:
        # Important: use {role, content} format, NOT {role, parts:[{type, text}]}
        api_post("/api/v1/sessions/{}/messages".format(session_id), {"role": role, "content": content})
        return True
    except Exception as e:
        log("Add-message error: {}".format(e))
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        state = load_state()
        log("Server: {}".format("UP" if health_check() else "DOWN"))
        log("Current session: {}".format(state.get("current_session", "None")))
        log("Started at:      {}".format(state.get("started_at", "N/A")))
        if "last_commit" in state:
            lc = state["last_commit"]
            log("Last commit: {} -- {} memories".format(lc.get("committed_at", "?"), lc.get("memories_extracted", 0)))

    elif cmd == "start":
        if not health_check():
            log("Server not running -- start skipped")
            sys.exit(0)
        state = load_state()
        if state.get("current_session"):
            log("Session already active: {}".format(state["current_session"]))
            sys.exit(0)
        sid = create_session()
        if sid:
            state["current_session"] = sid
            state["started_at"]      = datetime.now().isoformat()
            save_state(state)
            log("Session started: {}".format(sid))

    elif cmd == "add":
        if len(sys.argv) < 5:
            log("Usage: ov_session.py add <session_id> <role> <text>")
            sys.exit(1)
        _, _, session_id, role, text = sys.argv[:5]
        if not health_check():
            sys.exit(0)
        if add_message(session_id, role, text):
            log("Message added to {}: {} ({} chars)".format(session_id, role, len(text)))

    elif cmd == "commit":
        session_id = sys.argv[2] if len(sys.argv) > 2 else None
        if not session_id:
            state      = load_state()
            session_id = state.get("current_session")
        if not session_id:
            log("No session to commit")
            sys.exit(0)
        if not health_check():
            log("Server not running -- commit skipped")
            sys.exit(0)
        if commit_session(session_id):
            state = load_state()
            state["last_commit"] = {"session_id": session_id, "committed_at": datetime.now().isoformat()}
            state.pop("current_session", None)
            state.pop("started_at", None)
            save_state(state)

    elif cmd == "autosave":
        if not health_check():
            log("Server not running -- autosave skipped")
            sys.exit(0)
        state      = load_state()
        session_id = state.get("current_session")
        if session_id:
            log("Autosave: committing session {}".format(session_id))
            if commit_session(session_id):
                state["last_commit"] = {"session_id": session_id, "committed_at": datetime.now().isoformat()}
                state.pop("current_session", None)
                state.pop("started_at", None)
                save_state(state)
        log("Autosave: creating new session for next activity")
        new_sid = create_session()
        if new_sid:
            state["current_session"] = new_sid
            state["started_at"]      = datetime.now().isoformat()
            save_state(state)
            log("Autosave: session ready: {}".format(new_sid))

    else:
        log("Unknown command: {}".format(cmd))
        sys.exit(1)
