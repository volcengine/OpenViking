# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic test utilities for framework smoke tests."""

from __future__ import annotations

import fnmatch
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class InMemoryOpenVikingClient:
    """Small OpenViking-compatible client for examples and CI smoke tests.

    This class intentionally implements the OpenViking methods used by the
    LangChain/LangGraph adapters. It is not a replacement for OpenViking.
    """

    def __init__(self, records: dict[str, str] | None = None):
        self.records: dict[str, str] = dict(records or {})
        self.sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.archives: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.pending_tokens: dict[str, int] = defaultdict(int)
        self.find_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self._initialized = False

    def initialize(self) -> None:
        self._initialized = True

    def close(self) -> None:
        self._initialized = False

    def find(
        self,
        query: str,
        target_uri: str | list[str] = "",
        limit: int = 10,
        score_threshold: float | None = None,
        filter: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.find_calls.append(
            {
                "query": query,
                "target_uri": target_uri,
                "limit": limit,
                "score_threshold": score_threshold,
            }
        )
        return self._search(query, target_uri, limit, score_threshold)

    def search(
        self,
        query: str,
        target_uri: str | list[str] = "",
        session_id: str | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        filter: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.search_calls.append(
            {
                "query": query,
                "target_uri": target_uri,
                "session_id": session_id,
                "limit": limit,
                "score_threshold": score_threshold,
            }
        )
        session_text = " ".join(
            _message_text(message) for message in self.sessions.get(session_id or "", [])
        )
        return self._search(f"{query} {session_text}", target_uri, limit, score_threshold)

    def _search(
        self,
        query: str,
        target_uri: str | list[str],
        limit: int,
        score_threshold: float | None,
    ) -> dict[str, Any]:
        targets = [target_uri] if isinstance(target_uri, str) else list(target_uri)
        targets = [target.rstrip("/") for target in targets if target]
        tokens = {token for token in re.findall(r"[a-z0-9_]+", query.lower()) if len(token) > 1}
        scored: list[tuple[float, str, str]] = []
        for uri, content in self.records.items():
            if targets and not any(uri.startswith(target) for target in targets):
                continue
            haystack = f"{uri}\n{content}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score == 0 and tokens:
                continue
            normalized = float(score or 1)
            if score_threshold is not None and normalized < score_threshold:
                continue
            scored.append((normalized, uri, content))
        scored.sort(key=lambda row: (-row[0], row[1]))
        result = {"memories": [], "resources": [], "skills": [], "total": 0}
        for score, uri, content in scored[:limit]:
            item = {
                "uri": uri,
                "level": 2,
                "abstract": content[:240],
                "overview": content,
                "score": score,
                "match_reason": "deterministic token match",
            }
            if "/skills/" in uri:
                result["skills"].append(item)
            elif "/memories/" in uri:
                result["memories"].append(item)
            else:
                result["resources"].append(item)
        result["total"] = sum(len(result[key]) for key in ("memories", "resources", "skills"))
        return result

    def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        if uri not in self.records:
            raise FileNotFoundError(uri)
        lines = self.records[uri].splitlines()
        if offset or limit >= 0:
            end = None if limit < 0 else offset + limit
            return "\n".join(lines[offset:end])
        return self.records[uri]

    def abstract(self, uri: str) -> str:
        return self.read(uri)[:240]

    def overview(self, uri: str) -> str:
        return self.read(uri)

    def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if mode == "create" and uri in self.records:
            raise FileExistsError(uri)
        if mode == "replace" and uri not in self.records:
            raise FileNotFoundError(uri)
        if mode == "append":
            self.records[uri] = self.records.get(uri, "") + content
        else:
            self.records[uri] = content
        return {"uri": uri, "mode": mode, "content_updated": True}

    def mkdir(self, uri: str, description: str | None = None) -> None:
        return None

    def rm(self, uri: str, recursive: bool = False) -> None:
        if recursive:
            prefix = uri.rstrip("/") + "/"
            for key in list(self.records):
                if key == uri or key.startswith(prefix):
                    del self.records[key]
            return
        self.records.pop(uri, None)

    def ls(self, uri: str, simple: bool = False, recursive: bool = False, **_: Any) -> list[Any]:
        prefix = uri.rstrip("/") + "/"
        seen: set[str] = set()
        values: list[Any] = []
        for key in sorted(self.records):
            if not key.startswith(prefix):
                continue
            rel = key[len(prefix) :]
            if not recursive and "/" in rel:
                rel = rel.split("/", 1)[0]
            child_uri = prefix + rel
            if child_uri in seen:
                continue
            seen.add(child_uri)
            values.append(child_uri if simple else {"uri": child_uri, "rel_path": rel})
        return values

    def glob(self, pattern: str, uri: str = "viking://") -> dict[str, Any]:
        prefix = uri.rstrip("/") + "/"
        matches = []
        for key in sorted(self.records):
            if not key.startswith(prefix):
                continue
            rel = key[len(prefix) :]
            if fnmatch.fnmatch(rel, pattern):
                matches.append(key)
        return {"matches": matches, "count": len(matches)}

    def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)
        prefix = uri.rstrip("/") + "/"
        matches: list[dict[str, Any]] = []
        for key, content in sorted(self.records.items()):
            if key != uri and not key.startswith(prefix):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append({"uri": key, "line_number": line_number, "line": line})
                    if node_limit and len(matches) >= node_limit:
                        return {"matches": matches, "count": len(matches)}
        return {"matches": matches, "count": len(matches)}

    def create_session(self, session_id: str | None = None) -> dict[str, Any]:
        session_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
        self.sessions.setdefault(session_id, [])
        return {"session_id": session_id}

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        message_parts = list(parts or [{"type": "text", "text": content or ""}])
        message = {
            "id": f"msg_{uuid.uuid4().hex}",
            "role": role,
            "parts": message_parts,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sessions.setdefault(session_id, []).append(message)
        self.pending_tokens[session_id] += max(1, len(_message_text(message)) // 4)
        return {
            "session_id": session_id,
            "role": role,
            "message_count": len(self.sessions[session_id]),
        }

    def get_session(self, session_id: str, auto_create: bool = False) -> dict[str, Any]:
        if auto_create:
            self.create_session(session_id=session_id)
        return {
            "session_id": session_id,
            "message_count": len(self.sessions.get(session_id, [])),
            "pending_tokens": self.pending_tokens.get(session_id, 0),
        }

    def get_session_context(
        self,
        session_id: str,
        token_budget: int = 128_000,
        **_: Any,
    ) -> dict[str, Any]:
        del token_budget
        latest_archive = self.archives.get(session_id, [])[-1:] or []
        latest = latest_archive[0] if latest_archive else {}
        messages = list(self.sessions.get(session_id, []))
        active_tokens = sum(max(1, len(_message_text(message)) // 4) for message in messages)
        archive_tokens = max(0, len(str(latest.get("overview", ""))) // 4)
        return {
            "latest_archive_overview": latest.get("overview", ""),
            "pre_archive_abstracts": [
                {"archive_id": archive["archive_id"], "abstract": archive["abstract"]}
                for archive in self.archives.get(session_id, [])[:-1]
            ],
            "messages": messages,
            "estimatedTokens": active_tokens + archive_tokens,
            "stats": {
                "totalArchives": len(self.archives.get(session_id, [])),
                "includedArchives": 1 if latest else 0,
                "droppedArchives": 0,
                "failedArchives": 0,
                "activeTokens": active_tokens,
                "archiveTokens": archive_tokens,
            },
        }

    def get_session_archive(self, session_id: str, archive_id: str, **_: Any) -> dict[str, Any]:
        for archive in self.archives.get(session_id, []):
            if archive["archive_id"] == archive_id:
                return dict(archive)
        raise FileNotFoundError(archive_id)

    def commit_session(
        self,
        session_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        messages = list(self.sessions.get(session_id, []))
        archive_id = f"archive_{len(self.archives[session_id]) + 1:03d}"
        overview = "\n".join(_message_text(message) for message in messages)
        if messages:
            archive_uri = f"viking://session/{session_id}/history/{archive_id}"
            self.archives[session_id].append(
                {
                    "archive_id": archive_id,
                    "abstract": overview[:240],
                    "overview": overview,
                    "messages": messages,
                }
            )
            self.records[f"{archive_uri}/messages.jsonl"] = (
                "\n".join(_message_text(message) for message in messages) + "\n"
            )
            self.records[f"{archive_uri}/.abstract.md"] = overview[:240]
            self.records[f"{archive_uri}/.overview.md"] = overview
            self.records[f"{archive_uri}/.done"] = "{}"
        self.sessions[session_id] = []
        self.pending_tokens[session_id] = 0
        return {
            "session_id": session_id,
            "status": "completed",
            "archive_id": archive_id if messages else None,
            "archived": bool(messages),
        }

    def delete_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        self.archives.pop(session_id, None)
        self.pending_tokens.pop(session_id, None)
        session_uri = f"viking://session/{session_id}"
        for uri in list(self.records):
            if uri == session_uri or uri.startswith(f"{session_uri}/"):
                del self.records[uri]

    def add_resource(self, path: str, to: str | None = None, **_: Any) -> dict[str, Any]:
        uri = to or f"viking://resources/{path.rstrip('/').split('/')[-1]}"
        self.records.setdefault(uri, f"Resource imported from {path}")
        return {"status": "completed", "root_uri": uri}

    def add_skill(self, data: Any, **_: Any) -> dict[str, Any]:
        name = data.get("name", "skill") if isinstance(data, dict) else "skill"
        uri = f"viking://agent/skills/{name}.md"
        self.records[uri] = str(data)
        return {"status": "completed", "uri": uri, "name": name}

    def get_status(self) -> dict[str, Any]:
        return {"healthy": True, "backend": "in-memory"}

    def is_healthy(self) -> bool:
        return True


def _message_text(message: dict[str, Any]) -> str:
    chunks: list[str] = []
    for part in message.get("parts") or []:
        if part.get("type") == "text" and part.get("text"):
            chunks.append(str(part["text"]))
        elif part.get("type") == "context" and part.get("abstract"):
            chunks.append(str(part["abstract"]))
        elif part.get("type") == "tool" and part.get("tool_output"):
            chunks.append(str(part["tool_output"]))
    return "\n".join(chunks)
