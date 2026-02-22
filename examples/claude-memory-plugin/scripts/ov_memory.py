#!/usr/bin/env python3
"""OpenViking memory bridge for Claude Code hooks.

This script provides a stable interface for hook scripts:
- session-start: detect backend mode and open an OpenViking session
- ingest-stop: parse transcript last turn and append to session
- session-end: commit session to trigger OpenViking memory extraction
- recall: search extracted memories for skill-based retrieval
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request


@dataclass
class BackendInfo:
    mode: str  # "http" | "local"
    url: str = ""
    api_key: str = ""
    local_data_path: str = ""


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return _load_json(path)
    except Exception:
        return {}


def _health_check(url: str, timeout: float = 1.2) -> bool:
    try:
        with request.urlopen(f"{url.rstrip('/')}/health", timeout=timeout) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("status") == "ok"
    except (error.URLError, error.HTTPError, TimeoutError, ValueError, OSError):
        return False


def _resolve_local_data_path(project_dir: Path, ov_conf: Dict[str, Any]) -> str:
    raw = (
        ov_conf.get("storage", {})
        .get("vectordb", {})
        .get("path", "./data")
    )
    if not raw:
        raw = "./data"
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = project_dir / p
    return str(p)


def detect_backend(project_dir: Path, ov_conf: Dict[str, Any]) -> BackendInfo:
    server_cfg = ov_conf.get("server", {}) if isinstance(ov_conf, dict) else {}
    host = str(server_cfg.get("host", "")).strip()
    port = server_cfg.get("port")
    api_key = server_cfg.get("api_key") or ""

    if host and port:
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        if host.startswith("http://") or host.startswith("https://"):
            base = host.rstrip("/")
            url = f"{base}:{port}" if ":" not in base.split("//", 1)[-1] else base
        else:
            url = f"http://{host}:{port}"

        if _health_check(url):
            return BackendInfo(mode="http", url=url, api_key=str(api_key))

    return BackendInfo(
        mode="local",
        local_data_path=_resolve_local_data_path(project_dir, ov_conf),
    )


class OVClient:
    def __init__(self, backend: BackendInfo, ov_conf_path: Path):
        self.backend = backend
        self.ov_conf_path = ov_conf_path
        self.client: Any = None

    def __enter__(self) -> "OVClient":
        if self.backend.mode == "http":
            from openviking import SyncHTTPClient

            self.client = SyncHTTPClient(
                url=self.backend.url,
                api_key=self.backend.api_key or None,
            )
            self.client.initialize()
            return self

        os.environ["OPENVIKING_CONFIG_FILE"] = str(self.ov_conf_path)
        from openviking import SyncOpenViking

        self.client = SyncOpenViking(path=self.backend.local_data_path)
        self.client.initialize()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    def create_session(self) -> Dict[str, Any]:
        return self.client.create_session()

    def add_message(self, session_id: str, role: str, content: str) -> Dict[str, Any]:
        return self.client.add_message(session_id, role, content)

    def commit_session(self, session_id: str) -> Dict[str, Any]:
        return self.client.commit_session(session_id)

    def find(self, query: str, target_uri: str, limit: int) -> Any:
        return self.client.find(query=query, target_uri=target_uri, limit=limit)

    def read(self, uri: str) -> str:
        return self.client.read(uri)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _short(text: str, n: int) -> str:
    t = " ".join(text.split())
    if len(t) <= n:
        return t
    return t[: n - 3] + "..."


def _extract_text_parts(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    chunks: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = _as_text(block.get("text", ""))
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_tool_result(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    if not content:
        return ""

    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "tool_result":
        return ""

    payload = first.get("content")
    if isinstance(payload, str):
        return _short(payload, 220)
    if isinstance(payload, list):
        buf: List[str] = []
        for item in payload:
            if isinstance(item, dict) and item.get("type") == "text":
                t = _as_text(item.get("text", ""))
                if t:
                    buf.append(t)
        return _short("\n".join(buf), 220)
    return _short(_as_text(payload), 220)


def _is_user_prompt(entry: Dict[str, Any]) -> bool:
    if entry.get("type") != "user":
        return False
    msg = entry.get("message", {})
    content = msg.get("content")
    if _extract_tool_result(content):
        return False
    return bool(_extract_text_parts(content))


def _assistant_chunks(entry: Dict[str, Any]) -> List[str]:
    if entry.get("type") != "assistant":
        return []

    msg = entry.get("message", {})
    content = msg.get("content")

    if isinstance(content, str):
        text = _as_text(content)
        return [text] if text else []

    if not isinstance(content, list):
        return []

    chunks: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")

        if btype == "text":
            text = _as_text(block.get("text", ""))
            if text:
                chunks.append(text)
        elif btype == "tool_use":
            name = _as_text(block.get("name", "tool"))
            raw_input = block.get("input")
            try:
                inp = _short(json.dumps(raw_input, ensure_ascii=False), 180)
            except Exception:
                inp = _short(_as_text(raw_input), 180)
            chunks.append(f"[tool-use] {name}({inp})")

    return chunks


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def extract_last_turn(transcript_path: Path) -> Optional[Dict[str, str]]:
    rows = _read_jsonl(transcript_path)
    if not rows:
        return None

    last_user_idx = -1
    for i, row in enumerate(rows):
        if _is_user_prompt(row):
            last_user_idx = i

    if last_user_idx < 0:
        return None

    user_row = rows[last_user_idx]
    user_text = _extract_text_parts(user_row.get("message", {}).get("content"))
    turn_uuid = _as_text(user_row.get("uuid") or user_row.get("id"))

    chunks: List[str] = []
    for row in rows[last_user_idx + 1 :]:
        if _is_user_prompt(row):
            break

        if row.get("type") == "assistant":
            chunks.extend(_assistant_chunks(row))
            continue

        if row.get("type") == "user":
            tool_result = _extract_tool_result(row.get("message", {}).get("content"))
            if tool_result:
                chunks.append(f"[tool-result] {tool_result}")

    assistant_text = "\n".join([c for c in chunks if c]).strip()

    if not turn_uuid:
        turn_uuid = str(abs(hash(user_text + assistant_text)))

    if not user_text and not assistant_text:
        return None

    return {
        "turn_uuid": turn_uuid,
        "user_text": user_text,
        "assistant_text": assistant_text,
    }


def _summarize_with_claude(raw: str) -> str:
    if not shutil.which("claude"):
        return ""

    system_prompt = (
        "You are a session memory writer. Output ONLY 3-6 bullet points. "
        "Each line must start with '- '. Focus on decisions, fixes, and concrete changes. "
        "No intro or outro."
    )

    try:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                "--model",
                "haiku",
                "--no-session-persistence",
                "--no-chrome",
                "--system-prompt",
                system_prompt,
            ],
            input=raw,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except Exception:
        return ""

    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _fallback_summary(turn: Dict[str, str]) -> str:
    user = _short(turn.get("user_text", ""), 200)
    assistant = _short(turn.get("assistant_text", ""), 360)
    lines = []
    if user:
        lines.append(f"- User request: {user}")
    if assistant:
        lines.append(f"- Assistant response: {assistant}")
    if not lines:
        lines.append("- Captured a conversation turn.")
    return "\n".join(lines)


def summarize_turn(turn: Dict[str, str]) -> str:
    raw = (
        "Summarize this conversation turn for long-term engineering memory.\n\n"
        f"User:\n{turn.get('user_text', '')}\n\n"
        f"Assistant:\n{turn.get('assistant_text', '')}\n"
    )
    summary = _summarize_with_claude(raw)
    if summary:
        return summary
    return _fallback_summary(turn)


def _contexts_from_find_result(result: Any) -> List[Dict[str, Any]]:
    contexts: List[Dict[str, Any]] = []

    def push(obj: Any) -> None:
        if obj is None:
            return
        uri = _as_text(getattr(obj, "uri", "") if not isinstance(obj, dict) else obj.get("uri"))
        if not uri:
            return

        score = getattr(obj, "score", None) if not isinstance(obj, dict) else obj.get("score")
        abstract = (
            getattr(obj, "abstract", "") if not isinstance(obj, dict) else obj.get("abstract", "")
        )
        contexts.append(
            {
                "uri": uri,
                "score": float(score or 0.0),
                "abstract": _as_text(abstract),
            }
        )

    if isinstance(result, dict):
        for key in ("memories", "resources", "skills"):
            for row in result.get(key, []) or []:
                push(row)
        return contexts

    for key in ("memories", "resources", "skills"):
        rows = getattr(result, key, []) or []
        for row in rows:
            push(row)

    return contexts


def _build_backend_from_state_or_detect(
    state: Dict[str, Any], project_dir: Path, ov_conf: Dict[str, Any]
) -> BackendInfo:
    mode = _as_text(state.get("mode"))
    if mode == "http":
        url = _as_text(state.get("url"))
        if url:
            return BackendInfo(
                mode="http",
                url=url,
                api_key=_as_text(state.get("api_key")),
            )
    if mode == "local":
        local_data_path = _as_text(state.get("local_data_path"))
        if local_data_path:
            return BackendInfo(mode="local", local_data_path=local_data_path)

    return detect_backend(project_dir, ov_conf)


def cmd_session_start(args: argparse.Namespace) -> Dict[str, Any]:
    project_dir = Path(args.project_dir).resolve()
    ov_conf_path = project_dir / "ov.conf"
    state_file = Path(args.state_file)

    if not ov_conf_path.exists():
        return {
            "ok": False,
            "status_line": "[openviking-memory] ERROR: ./ov.conf not found",
            "error": "ov.conf not found",
        }

    ov_conf = _load_json(ov_conf_path)
    backend = detect_backend(project_dir, ov_conf)

    with OVClient(backend, ov_conf_path) as cli:
        session = cli.create_session()
        session_id = _as_text(session.get("session_id"))
        if not session_id:
            raise RuntimeError("Failed to create OpenViking session")

    state = {
        "active": True,
        "project_dir": str(project_dir),
        "ov_conf": str(ov_conf_path),
        "mode": backend.mode,
        "url": backend.url,
        "api_key": backend.api_key,
        "local_data_path": backend.local_data_path,
        "session_id": session_id,
        "last_turn_uuid": "",
        "ingested_turns": 0,
        "started_at": int(time.time()),
    }
    _save_json(state_file, state)

    status = f"[openviking-memory] mode={backend.mode} session={session_id}"
    if backend.mode == "http":
        status += f" server={backend.url}"

    additional = (
        "OpenViking memory is active. "
        "For historical context, use the memory-recall skill when needed."
    )

    return {
        "ok": True,
        "mode": backend.mode,
        "session_id": session_id,
        "status_line": status,
        "additional_context": additional,
    }


def cmd_ingest_stop(args: argparse.Namespace) -> Dict[str, Any]:
    project_dir = Path(args.project_dir).resolve()
    ov_conf_path = project_dir / "ov.conf"
    state_file = Path(args.state_file)
    transcript = Path(args.transcript_path)

    state = _load_state(state_file)
    if not state.get("active"):
        return {"ok": True, "ingested": False, "reason": "inactive session"}
    if not state.get("session_id"):
        return {"ok": True, "ingested": False, "reason": "missing session_id"}
    if not transcript.exists():
        return {"ok": True, "ingested": False, "reason": "transcript not found"}
    if not ov_conf_path.exists():
        return {"ok": True, "ingested": False, "reason": "ov.conf not found"}

    turn = extract_last_turn(transcript)
    if not turn:
        return {"ok": True, "ingested": False, "reason": "no turn parsed"}

    if _as_text(turn.get("turn_uuid")) == _as_text(state.get("last_turn_uuid")):
        return {"ok": True, "ingested": False, "reason": "duplicate turn"}

    ov_conf = _load_json(ov_conf_path)
    backend = _build_backend_from_state_or_detect(state, project_dir, ov_conf)

    summary = summarize_turn(turn)

    user_text = _as_text(turn.get("user_text"))
    if not user_text:
        user_text = "(No user prompt captured)"

    assistant_excerpt = _as_text(turn.get("assistant_text"))
    assistant_msg = f"Turn summary:\n{summary}"
    if assistant_excerpt:
        assistant_msg += f"\n\nAssistant excerpt:\n{_short(assistant_excerpt, 1500)}"

    with OVClient(backend, ov_conf_path) as cli:
        session_id = _as_text(state.get("session_id"))
        cli.add_message(session_id, "user", user_text)
        cli.add_message(session_id, "assistant", assistant_msg)

    state["mode"] = backend.mode
    state["url"] = backend.url
    state["api_key"] = backend.api_key
    state["local_data_path"] = backend.local_data_path
    state["last_turn_uuid"] = _as_text(turn.get("turn_uuid"))
    state["ingested_turns"] = int(state.get("ingested_turns", 0)) + 1
    state["last_ingested_at"] = int(time.time())
    _save_json(state_file, state)

    return {
        "ok": True,
        "ingested": True,
        "session_id": state.get("session_id"),
        "turn_uuid": turn.get("turn_uuid"),
        "ingested_turns": state.get("ingested_turns"),
    }


def cmd_session_end(args: argparse.Namespace) -> Dict[str, Any]:
    project_dir = Path(args.project_dir).resolve()
    ov_conf_path = project_dir / "ov.conf"
    state_file = Path(args.state_file)

    state = _load_state(state_file)
    if not state.get("active") or not state.get("session_id"):
        return {
            "ok": True,
            "committed": False,
            "status_line": "[openviking-memory] no active session",
        }

    if not ov_conf_path.exists():
        return {
            "ok": False,
            "committed": False,
            "status_line": "[openviking-memory] ERROR: ./ov.conf not found",
            "error": "ov.conf not found",
        }

    ov_conf = _load_json(ov_conf_path)
    backend = _build_backend_from_state_or_detect(state, project_dir, ov_conf)

    with OVClient(backend, ov_conf_path) as cli:
        result = cli.commit_session(_as_text(state.get("session_id")))

    state["active"] = False
    state["committed_at"] = int(time.time())
    state["commit_result"] = result
    _save_json(state_file, state)

    extracted = int(result.get("memories_extracted", 0)) if isinstance(result, dict) else 0
    status = (
        "[openviking-memory] session committed"
        f" id={state.get('session_id')}"
        f" memories_extracted={extracted}"
    )

    return {
        "ok": True,
        "committed": True,
        "status_line": status,
        "result": result,
    }


def cmd_recall(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).resolve()
    ov_conf_path = project_dir / "ov.conf"
    state_file = Path(args.state_file)
    query = _as_text(args.query)

    if not query:
        print("No relevant memories found.")
        return 0

    if not ov_conf_path.exists():
        print("Memory unavailable: ./ov.conf not found.")
        return 0

    state = _load_state(state_file)
    ov_conf = _load_json(ov_conf_path)
    backend = _build_backend_from_state_or_detect(state, project_dir, ov_conf)

    roots = ["viking://user/memories/", "viking://agent/memories/"]
    contexts: List[Dict[str, Any]] = []

    with OVClient(backend, ov_conf_path) as cli:
        for root in roots:
            try:
                result = cli.find(query=query, target_uri=root, limit=max(args.top_k, 3))
            except Exception:
                continue
            contexts.extend(_contexts_from_find_result(result))

        dedup: Dict[str, Dict[str, Any]] = {}
        for item in contexts:
            uri = item.get("uri", "")
            if not uri:
                continue
            if uri not in dedup or float(item.get("score", 0.0)) > float(dedup[uri].get("score", 0.0)):
                dedup[uri] = item

        ranked = sorted(
            dedup.values(),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )[: args.top_k]

        if not ranked:
            print("No relevant memories found.")
            return 0

        output_lines = [f"Relevant memories for: {query}", ""]

        for i, item in enumerate(ranked, start=1):
            uri = _as_text(item.get("uri"))
            score = float(item.get("score", 0.0))
            abstract = _as_text(item.get("abstract", ""))
            try:
                content = _as_text(cli.read(uri))
            except Exception:
                content = ""

            output_lines.append(f"{i}. [{score:.3f}] {uri}")
            if abstract:
                output_lines.append(f"   abstract: {_short(abstract, 220)}")
            if content:
                output_lines.append(f"   snippet: {_short(content, 420)}")
            output_lines.append("")

    print("\n".join(output_lines).strip())
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenViking memory bridge")
    parser.add_argument("--project-dir", required=True, help="Claude project directory")
    parser.add_argument("--state-file", required=True, help="Plugin state file path")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("session-start", help="Start memory session")

    p_stop = sub.add_parser("ingest-stop", help="Ingest last transcript turn")
    p_stop.add_argument("--transcript-path", required=True, help="Claude transcript path")

    sub.add_parser("session-end", help="Commit memory session")

    p_recall = sub.add_parser("recall", help="Search extracted memories")
    p_recall.add_argument("--query", required=True, help="Recall query")
    p_recall.add_argument("--top-k", type=int, default=5, help="Number of memories to return")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "session-start":
            print(json.dumps(cmd_session_start(args), ensure_ascii=False))
            return 0

        if args.command == "ingest-stop":
            print(json.dumps(cmd_ingest_stop(args), ensure_ascii=False))
            return 0

        if args.command == "session-end":
            print(json.dumps(cmd_session_end(args), ensure_ascii=False))
            return 0

        if args.command == "recall":
            return cmd_recall(args)

        parser.error(f"Unknown command: {args.command}")
        return 2

    except Exception as exc:  # noqa: BLE001
        if args.command == "recall":
            print(f"Memory recall failed: {exc}")
            return 1
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
