from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:1933"
DEFAULT_TARGET_URI = "viking://user/memories"


@dataclass
class Message:
    role: str
    content: str
    timestamp: str


@dataclass
class Session:
    session_id: str
    cwd: str
    created_at: str


class OpenVikingClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENVIKING_API_KEY", "")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-OpenViking-Account": os.environ.get("OPENVIKING_ACCOUNT", "default"),
            "X-OpenViking-User": os.environ.get("OPENVIKING_USER", "default"),
            "X-OpenViking-Agent": os.environ.get("OPENVIKING_AGENT", "default"),
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _resolve_target_uri(self, target_uri: str) -> str:
        normalized = target_uri.rstrip("/")
        if normalized == DEFAULT_TARGET_URI:
            user_space = self._headers().get("X-OpenViking-User", "default") or "default"
            return f"viking://user/{user_space}/memories/"
        return target_uri

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers(),
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as decode_exc:
                raise RuntimeError(f"HTTP {exc.code}: {raw}") from decode_exc
            error = body.get("error") or {}
            detail = body.get("detail")
            message = error.get("message") or detail or f"HTTP {exc.code}"
            raise RuntimeError(message) from exc
        if body.get("status") == "error":
            message = body.get("error", {}).get("message", "unknown error")
            raise RuntimeError(message)
        return body.get("result", body)

    def add_session_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            {"role": role, "content": content},
        )

    def commit_session(self, session_id: str, wait: bool = True) -> dict[str, Any]:
        suffix = "?wait=true" if wait else ""
        return self._request("POST", f"/api/v1/sessions/{session_id}/commit{suffix}", {})

    def recall(self, query: str, limit: int = 5, target_uri: str = DEFAULT_TARGET_URI) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/search/find",
            {
                "query": query,
                "limit": limit,
                "target_uri": self._resolve_target_uri(target_uri),
            },
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_sync_state(state_root: Path) -> dict[str, Any]:
    path = state_root / "ov_dream_sync.json"
    if not path.exists():
        return {"sessions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_sync_state(state_root: Path, state: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / "ov_dream_sync.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_session_path(sessions_root: Path, session_id: str) -> Path:
    return sessions_root / f"{session_id}.jsonl"


def get_active_session(openclaw_root: Path) -> Session | None:
    sessions_root = openclaw_root / "agents" / "main" / "sessions"
    if not sessions_root.exists():
        return None

    indexed = _get_indexed_active_session(sessions_root)
    if indexed is not None:
        return indexed

    files = [
        path
        for path in sessions_root.glob("*.jsonl")
        if ".reset." not in path.name and ".checkpoint." not in path.name
    ]
    if not files:
        return None

    latest = max(files, key=lambda path: path.stat().st_mtime)
    lines = latest.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    first = json.loads(lines[0])
    return Session(
        session_id=first["id"],
        cwd=first.get("cwd", ""),
        created_at=first.get("timestamp", ""),
    )


def _get_indexed_active_session(sessions_root: Path) -> Session | None:
    index_path = sessions_root / "sessions.json"
    if not index_path.exists():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(index, dict):
        return None

    active = index.get("agent:main:main")
    if not isinstance(active, dict):
        return None

    session_id = active.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None

    session_file = active.get("sessionFile")
    path = Path(session_file) if isinstance(session_file, str) and session_file else get_session_path(sessions_root, session_id)
    if not path.exists():
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    return Session(
        session_id=session_id,
        cwd=first.get("cwd", ""),
        created_at=first.get("timestamp", ""),
    )


def parse_messages(sessions_root: Path, session_id: str, after_timestamp: str | None) -> Iterable[Message]:
    path = get_session_path(sessions_root, session_id)
    if not path.exists():
        return []

    messages: list[Message] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") != "message":
            continue
        timestamp = row.get("timestamp", "")
        if after_timestamp and timestamp <= after_timestamp:
            continue
        message = row.get("message", {})
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        blocks = message.get("content", [])
        text_parts = [
            block.get("text", "").strip()
            for block in blocks
            if block.get("type") == "text"
        ]
        content = "\n".join(part for part in text_parts if part)
        if not content:
            continue
        messages.append(Message(role=role, content=content, timestamp=timestamp))
    return messages


def sync_active_session(client: OpenVikingClient, openclaw_root: Path, state_root: Path) -> dict[str, Any]:
    sessions_root = openclaw_root / "agents" / "main" / "sessions"
    session = get_active_session(openclaw_root)
    if session is None:
        raise RuntimeError("No active OpenClaw session found.")

    state = load_sync_state(state_root)
    sessions = state.setdefault("sessions", {})
    session_state = sessions.get(session.session_id)
    if not isinstance(session_state, dict):
        session_state = {}

    last_synced_timestamp = session_state.get("last_synced_timestamp")
    messages = [
        message
        for message in parse_messages(sessions_root, session.session_id, last_synced_timestamp)
        if message.timestamp and (last_synced_timestamp is None or message.timestamp > last_synced_timestamp)
    ]
    messages.sort(key=lambda message: message.timestamp)

    synced_count = 0
    committed = False
    now = _utc_now_iso()
    try:
        for message in messages:
            client.add_session_message(session.session_id, message.role, message.content)
            synced_count += 1

        session_state["last_status"] = "ok"
        session_state["last_synced_count"] = synced_count
        session_state["last_sync_at"] = now
        session_state["committed"] = False

        if synced_count:
            client.commit_session(session.session_id, wait=True)
            committed = True
            session_state["committed"] = True
            session_state["last_commit_at"] = now
            last_synced_timestamp = messages[-1].timestamp
            session_state["last_synced_timestamp"] = last_synced_timestamp
    except Exception:
        session_state["last_status"] = "error"
        session_state["last_synced_count"] = synced_count
        session_state["last_sync_at"] = now
        session_state["committed"] = False
        sessions[session.session_id] = session_state
        save_sync_state(state_root, state)
        raise

    sessions[session.session_id] = session_state
    save_sync_state(state_root, state)
    return {
        "session_id": session.session_id,
        "synced_count": synced_count,
        "committed": committed,
        "last_synced_timestamp": last_synced_timestamp,
    }


def _normalize_ov_command(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    if not argv:
        return argv
    if len(argv) >= 2 and argv[0] == "ov":
        if argv[1] == "dream":
            return ["dream"]
        if argv[1] == "recall":
            query = " ".join(argv[2:]).strip()
            return ["recall", query] if query else ["recall", ""]
    if len(argv) == 1:
        raw = argv[0].strip()
        if raw == "ov dream":
            return ["dream"]
        if raw.startswith("ov recall "):
            return ["recall", raw[len("ov recall ") :].strip()]
    return argv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ov dream")
    parser.add_argument("--base-url", default=os.environ.get("OPENVIKING_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--openclaw-root", default=str(Path.home() / ".openclaw"))
    parser.add_argument("--state-root", default=str(Path.home() / ".openclaw" / "memory"))

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("dream", help="Sync the active OpenClaw session to OpenViking.")

    recall = subparsers.add_parser("recall", help="Recall memories from OpenViking.")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=5)

    return parser


def _iter_memories(result: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    memories = result.get("memories")
    if not isinstance(memories, list):
        return []
    return [item for item in memories if isinstance(item, dict)]


def _print_sync_summary(summary: dict[str, Any]) -> None:
    print(
        "session_id={session_id} synced_count={synced_count} committed={committed} last_synced_timestamp={last_synced_timestamp}".format(
            session_id=summary.get("session_id", ""),
            synced_count=summary.get("synced_count", 0),
            committed=str(summary.get("committed", False)).lower(),
            last_synced_timestamp=summary.get("last_synced_timestamp", ""),
        )
    )


def _print_recall_results(result: Any) -> None:
    memories = list(_iter_memories(result))
    if not memories:
        print("No memories found.")
        return

    for item in memories:
        uri = item.get("uri", "")
        score = item.get("score", "")
        summary = item.get("abstract") or item.get("overview") or ""
        print(f"{uri}|{score}|{summary}")


def run_dream(args: argparse.Namespace) -> int:
    client = OpenVikingClient(base_url=args.base_url, api_key=args.api_key)
    summary = sync_active_session(
        client=client,
        openclaw_root=Path(args.openclaw_root),
        state_root=Path(args.state_root),
    )
    _print_sync_summary(summary)
    return 0


def run_recall(args: argparse.Namespace) -> int:
    client = OpenVikingClient(base_url=args.base_url, api_key=args.api_key)
    result = client.recall(query=args.query, limit=args.limit)
    _print_recall_results(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(_normalize_ov_command(argv))
        if args.command == "dream":
            return run_dream(args)
        if args.command == "recall":
            return run_recall(args)
        raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
