# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""WorkBuddy (Tencent agentic coding assistant) adapter.

Logs: ``~/.workbuddy/projects/<project-slug>/<session-uuid>.jsonl`` (append-only JSONL).

The on-disk layout mirrors Claude Code's, but the record schema does not:

* turns are ``type == "message"`` with a **top-level** ``role``;
* text lives in flat ``content[]`` blocks -- ``input_text`` for user turns,
  ``output_text`` for assistant turns;
* timestamps are epoch **milliseconds**;
* the session title comes from a separate ``type == "ai-title"`` record
  (``aiTitle``), and ``cwd`` / ``sessionId`` ride along on every record.

WorkBuddy also composes each user turn out of host-injected blocks -- system prompt,
project context, quoted history and reminders -- and wraps the human ask in
``<user_query>``. A naive adapter that maps the whole turn to memory therefore
archives the agent's own instructions: on a real corpus the injected block is ~10 KB
per turn, so recall degrades into extracts of the system prompt. ``parse_line``
strips the host blocks, keeps only the ``<user_query>`` body, and drops the turn when
nothing survives (compaction summaries, continuation prompts).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openviking.ingest.models import NormalizedMessage, SessionRef, iso_from_epoch_ms
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import JsonlLogSource

_TEXT_BLOCK_TYPES = ("input_text", "output_text")

# Host-injected blocks inside a user turn. Stripping them before looking for
# ``<user_query>`` matters because some of them *quote* earlier user turns
# (``<previous_user_message>`` wraps a full ``<user_query>``), and a quoted history
# block must never be mistaken for the current ask.
_HOST_BLOCK_TAGS = (
    "system-reminder",
    "memory_and_skills_reminder",
    "automation_system_reminder",
    "previous_user_message",
    "previous_assistant_message",
    "previous_tool_call",
    "cb_summary",
    "conversation_history_summary",
    "additional_data",
    "user_info",
    "identity_context",
    "craft_mode",
    "connector-status",
)
_HOST_BLOCK_RE = re.compile(
    r"<(?P<tag>" + "|".join(_HOST_BLOCK_TAGS) + r")(?:\s[^>]*)?>.*?</(?P=tag)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_USER_QUERY_RE = re.compile(r"<user_query\s*>(.*?)</user_query\s*>", re.DOTALL | re.IGNORECASE)

# Discovery runs on every poll, so the metadata is cached on (mtime, size). It is read
# from two bounded windows rather than one, because the fields do not live in the same
# place: ``sessionId`` / ``cwd`` / ``timestamp`` ride along on every record (always
# line 1), while the title is *appended* -- see ``_TITLE_TAIL_BYTES``.
_PEEK_MAX_LINES = 2000
_PEEK_MAX_BYTES = 4 * 1024 * 1024
_PEEK_CACHE_MAX_ENTRIES = 4096
_PEEK_CACHE: Dict[str, Tuple[Tuple[float, int], Dict[str, Any]]] = {}

# WorkBuddy re-emits an ``ai-title`` record every time it re-titles the session, so a log
# can hold several and only the **last** is the settled name -- the earlier ones are the
# name the session carried before it found its topic. On a real corpus the newest title sat
# at a 7.3 MB / 8.2 MB offset of a 9 MB log, far outside the head budget above, so the
# title is read from a second bounded window at the *tail*: the log is append-only, so the
# newest title is the last one written. Neither window alone suffices -- of 40 titled
# sessions, 36 had the settled title inside both, 2 only in the head (never re-titled), and
# 2 only in the tail -- hence the tail result wins and the head result stands as fallback.
# Logs smaller than the window are covered whole.
_TITLE_TAIL_BYTES = 4 * 1024 * 1024


def _blocks_text(content: Any) -> str:
    """Join the text blocks of a ``content`` array (or accept a bare string)."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in _TEXT_BLOCK_TYPES:
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks)


def user_turn_text(raw: str) -> str:
    """Return the human ask from a host-composed user turn, or ``""`` if there is none.

    WorkBuddy wraps the actual prompt in ``<user_query>`` and surrounds it with
    injected context. Everything else is host-authored, so a turn that has no
    ``<user_query>`` at all (compaction summary, "continue from the summary") carries
    no human text and must be dropped instead of being stored as a memory.
    """
    if not raw:
        return ""
    stripped = _HOST_BLOCK_RE.sub(" ", raw)
    queries = [match.strip() for match in _USER_QUERY_RE.findall(stripped)]
    queries = [query for query in queries if query]
    if len(queries) == 1:
        return queries[0]
    if not queries:
        return ""
    # Unexpected shape (the corpus never hits it): prefer the longest candidate rather
    # than silently dropping a turn that probably carries the real ask.
    return max(queries, key=len)


@register_source("workbuddy")
class WorkBuddySource(JsonlLogSource):
    file_glob = "*/*.jsonl"

    def default_paths(self) -> List[Path]:
        return [Path.home() / ".workbuddy" / "projects"]

    def session_ref_for_file(self, path: Path) -> SessionRef:
        meta = self._peek_meta(path)
        return SessionRef(
            harness=self.name,
            native_session_id=meta.get("session_id") or path.stem,
            locator=str(path),
            title=meta.get("title"),
            started_at=meta.get("started_at"),
            meta={"model": meta.get("model"), "cwd": meta.get("cwd")},
        )

    @staticmethod
    def _peek_meta(path: Path) -> Dict[str, Any]:
        """Best-effort session metadata, cached on ``(mtime, size)``."""
        try:
            st = path.stat()
        except OSError:
            return {}
        key = str(path)
        signature = (st.st_mtime, st.st_size)
        cached = _PEEK_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]

        meta: Dict[str, Any] = {}
        consumed = 0
        try:
            with open(path, "rb") as handle:
                for index, raw in enumerate(handle):
                    if index >= _PEEK_MAX_LINES or consumed >= _PEEK_MAX_BYTES:
                        break
                    consumed += len(raw)
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if not meta.get("session_id") and isinstance(obj.get("sessionId"), str):
                        meta["session_id"] = obj["sessionId"]
                    if not meta.get("cwd") and isinstance(obj.get("cwd"), str):
                        meta["cwd"] = obj["cwd"]
                    if meta.get("started_at") is None and obj.get("timestamp") is not None:
                        meta["started_at"] = iso_from_epoch_ms(obj.get("timestamp"))
                    if meta.get("title") is None and obj.get("type") == "ai-title":
                        # Provisional: superseded below if the log holds a newer title.
                        candidate = obj.get("aiTitle")
                        if isinstance(candidate, str) and candidate.strip():
                            meta["title"] = candidate.strip()
                    if meta.get("model") is None and obj.get("role") == "assistant":
                        provider_data = obj.get("providerData")
                        if isinstance(provider_data, dict) and provider_data.get("model"):
                            meta["model"] = provider_data["model"]
                    if all(
                        meta.get(field)
                        for field in ("session_id", "cwd", "started_at", "title", "model")
                    ):
                        break
        except OSError:
            return {}

        tail_title = WorkBuddySource._peek_title(path, signature[1])
        if tail_title is not None:
            meta["title"] = tail_title

        if len(_PEEK_CACHE) >= _PEEK_CACHE_MAX_ENTRIES:
            _PEEK_CACHE.clear()
        _PEEK_CACHE[key] = (signature, meta)
        return meta

    @staticmethod
    def _peek_title(path: Path, size: int) -> Optional[str]:
        """The last non-empty ``ai-title`` in a bounded window at the tail of the log."""
        start = max(0, size - _TITLE_TAIL_BYTES)
        title: Optional[str] = None
        try:
            with open(path, "rb") as handle:
                if start:
                    handle.seek(start)
                    # The window can open in the middle of a record, so drop that first
                    # partial line instead of trying to salvage it.
                    handle.readline()
                for raw in handle:
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict) or obj.get("type") != "ai-title":
                        continue
                    candidate = obj.get("aiTitle")
                    if isinstance(candidate, str) and candidate.strip():
                        title = candidate.strip()
        except OSError:
            return None
        return title

    def parse_line(self, obj: Dict[str, Any], ref: SessionRef) -> List[NormalizedMessage]:
        if obj.get("type") != "message":
            return []
        role = obj.get("role")
        if role not in ("user", "assistant"):
            return []
        text = _blocks_text(obj.get("content"))
        if not text:
            return []
        if role == "user":
            # Keep only what the human actually typed; host-composed turns yield "".
            text = user_turn_text(text)
            if not text:
                return []

        provider_data = obj.get("providerData")
        model = provider_data.get("model") if isinstance(provider_data, dict) else None
        cwd = obj.get("cwd") or ref.meta.get("cwd")
        peer = self.assistant_peer(model) if role == "assistant" else self.user_peer(cwd=cwd)
        return [
            NormalizedMessage(
                role=role,
                text=text,
                created_at=iso_from_epoch_ms(obj.get("timestamp")),
                peer_id=peer,
                meta={"model": model, "cwd": cwd},
            )
        ]
