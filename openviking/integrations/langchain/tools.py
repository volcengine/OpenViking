# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain tool factory for OpenViking primitives."""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

try:
    from langchain_core.tools import StructuredTool
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from openviking.integrations.langchain.client import (
    OpenVikingConnection,
    call_openviking,
    compact_json,
    ensure_client,
    item_value,
    iter_result_items,
    stringify,
)

logger = logging.getLogger(__name__)


def create_openviking_tools(
    *,
    client: Any = None,
    url: str | None = None,
    api_key: str | None = None,
    account: str | None = None,
    user: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    path: str | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
    auto_initialize: bool = True,
    profile: str = "agent",
    tool_names: Iterable[str] | None = None,
    allow_forget: bool = False,
) -> list[StructuredTool]:
    """Create LangChain tools exposing OpenViking's common agent primitives.

    Tool names intentionally use the ``viking_*`` prefix so models see the same
    conceptual operations that OpenViking users know from plugins and MCP:
    find/search, browse/read, grep, store, add_resource, add_skill, and health.
    """

    cached_client: Any = None

    def get_client() -> Any:
        nonlocal cached_client
        if cached_client is None:
            cached_client = ensure_client(
                OpenVikingConnection(
                    client=client,
                    url=url,
                    api_key=api_key,
                    account=account,
                    user=user,
                    user_id=user_id,
                    agent_id=agent_id,
                    path=path,
                    timeout=timeout,
                    extra_headers=extra_headers,
                    auto_initialize=auto_initialize,
                )
            )
        return cached_client

    def viking_find(
        query: str,
        target_uri: str = "",
        limit: int = 8,
        min_score: float | None = None,
    ) -> str:
        """Quick semantic recall from OpenViking without session context."""

        result = call_openviking(
            get_client(),
            "find",
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=min_score,
        )
        return _format_retrieval_result(result)

    def viking_search(
        query: str,
        target_uri: str = "",
        session_id: str | None = None,
        limit: int = 8,
        min_score: float | None = None,
    ) -> str:
        """Session-aware OpenViking retrieval for memories, resources, and skills."""

        result = call_openviking(
            get_client(),
            "search",
            query=query,
            target_uri=target_uri,
            session_id=session_id,
            limit=limit,
            score_threshold=min_score,
        )
        return _format_retrieval_result(result)

    def viking_browse(
        uri: str = "viking://",
        recursive: bool = False,
        pattern: str | None = None,
    ) -> str:
        """Browse OpenViking namespaces with ls, or glob when pattern is set."""

        active_client = get_client()
        if pattern:
            result = call_openviking(active_client, "glob", pattern=pattern, uri=uri)
        else:
            result = call_openviking(active_client, "ls", uri=uri, recursive=recursive)
        return stringify(result, max_chars=12_000)

    def viking_read(
        uris: str | list[str],
        max_chars: int = 12_000,
        content_mode: str = "read",
    ) -> str:
        """Read one or more OpenViking URIs as L0 abstract, L1 overview, or L2 content."""

        active_client = get_client()
        uri_list = [uris] if isinstance(uris, str) else uris
        mode = str(content_mode or "read").lower().strip()
        if mode not in {"abstract", "overview", "read"}:
            raise ValueError("content_mode must be one of: abstract, overview, read")
        payload = []
        for uri in uri_list:
            content = call_openviking(active_client, mode, uri=uri)
            payload.append(
                {
                    "uri": uri,
                    "content_mode": mode,
                    "content": stringify(content, max_chars=max_chars),
                }
            )
        return stringify(payload, max_chars=max_chars * max(1, len(uri_list)))

    def viking_grep(
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: int = 20,
    ) -> str:
        """Search OpenViking file content with a grep-style pattern."""

        result = call_openviking(
            get_client(),
            "grep",
            uri=uri,
            pattern=pattern,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
        )
        return stringify(result, max_chars=12_000)

    def viking_store(
        messages: str | list[dict[str, Any]],
        session_id: str | None = None,
        commit: bool = True,
    ) -> str:
        """Store a conversation turn in an OpenViking session and optionally commit it."""

        active_client = get_client()
        if not session_id:
            created = call_openviking(active_client, "create_session")
            session_id = created.get("session_id") if isinstance(created, dict) else str(created)
        normalized_messages = _normalize_messages(messages)
        for message in normalized_messages:
            call_openviking(
                active_client,
                "add_message",
                session_id=session_id,
                role=message["role"],
                content=message.get("content"),
                parts=message.get("parts"),
            )
        result: dict[str, Any] = {
            "session_id": session_id,
            "messages_added": len(normalized_messages),
        }
        if commit:
            result["commit"] = call_openviking(
                active_client,
                "commit_session",
                session_id=session_id,
            )
        return compact_json(result)

    def viking_archive_search(
        session_id: str,
        query: str,
        archive_id: str | None = None,
        token_budget: int = 128_000,
        max_matches: int = 8,
    ) -> str:
        """Search committed OpenViking session archive context."""

        active_client = get_client()
        if archive_id:
            archive = call_openviking(
                active_client,
                "get_session_archive",
                session_id=session_id,
                archive_id=archive_id,
            )
            matches = _search_archive_payload(archive, query, max_matches=max_matches)
        else:
            matches = _grep_session_history(
                active_client,
                session_id=session_id,
                query=query,
                max_matches=max_matches,
            )
            if _match_count(matches) > 0:
                return stringify(matches, max_chars=12_000)
            context = call_openviking(
                active_client,
                "get_session_context",
                session_id=session_id,
                token_budget=token_budget,
            )
            matches = _search_archive_payload(context, query, max_matches=max_matches)
        return stringify(matches or {"matches": [], "count": 0}, max_chars=12_000)

    def viking_archive_expand(
        session_id: str,
        archive_id: str,
        max_chars: int = 20_000,
    ) -> str:
        """Expand one OpenViking session archive by archive ID."""

        archive = call_openviking(
            get_client(),
            "get_session_archive",
            session_id=session_id,
            archive_id=archive_id,
        )
        return stringify(archive, max_chars=max_chars)

    def viking_add_resource(
        path: str,
        to: str | None = None,
        parent: str | None = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: float | None = None,
    ) -> str:
        """Import a file, directory, URL, or repository into OpenViking resources."""

        result = call_openviking(
            get_client(),
            "add_resource",
            path=path,
            to=to,
            parent=parent,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout,
        )
        return stringify(result, max_chars=8_000)

    def viking_add_skill(
        data: dict[str, Any] | str,
        wait: bool = False,
        timeout: float | None = None,
    ) -> str:
        """Register a reusable skill in OpenViking."""

        result = call_openviking(
            get_client(),
            "add_skill",
            data=data,
            wait=wait,
            timeout=timeout,
        )
        return stringify(result, max_chars=8_000)

    def viking_health() -> str:
        """Check OpenViking health/status."""

        active_client = get_client()
        if hasattr(active_client, "get_status"):
            return stringify(call_openviking(active_client, "get_status"), max_chars=8_000)
        if hasattr(active_client, "is_healthy"):
            return compact_json({"healthy": call_openviking(active_client, "is_healthy")})
        return compact_json({"healthy": True})

    def viking_forget(uri: str, recursive: bool = False) -> str:
        """Remove a URI from OpenViking. Only expose this to trusted agents."""

        call_openviking(get_client(), "rm", uri=uri, recursive=recursive)
        return compact_json({"removed": uri, "recursive": recursive})

    all_tools: dict[str, StructuredTool] = {
        "viking_find": StructuredTool.from_function(viking_find),
        "viking_search": StructuredTool.from_function(viking_search),
        "viking_browse": StructuredTool.from_function(viking_browse),
        "viking_read": StructuredTool.from_function(viking_read),
        "viking_grep": StructuredTool.from_function(viking_grep),
        "viking_archive_search": StructuredTool.from_function(viking_archive_search),
        "viking_archive_expand": StructuredTool.from_function(viking_archive_expand),
        "viking_store": StructuredTool.from_function(viking_store),
        "viking_add_resource": StructuredTool.from_function(viking_add_resource),
        "viking_add_skill": StructuredTool.from_function(viking_add_skill),
        "viking_health": StructuredTool.from_function(viking_health),
        "viking_forget": StructuredTool.from_function(viking_forget),
    }

    if tool_names is None:
        selected = _profile_tool_names(profile, allow_forget=allow_forget)
    else:
        selected = list(tool_names)
    return [all_tools[name] for name in selected if name in all_tools]


def _profile_tool_names(profile: str, *, allow_forget: bool) -> list[str]:
    retrieval = [
        "viking_find",
        "viking_search",
        "viking_browse",
        "viking_read",
        "viking_grep",
        "viking_archive_search",
        "viking_archive_expand",
    ]
    if profile == "retrieval":
        names = retrieval + ["viking_health"]
    elif profile == "admin":
        names = retrieval + [
            "viking_store",
            "viking_add_resource",
            "viking_add_skill",
            "viking_health",
            "viking_forget",
        ]
    else:
        names = retrieval + [
            "viking_store",
            "viking_add_resource",
            "viking_add_skill",
            "viking_health",
        ]
    if allow_forget and "viking_forget" not in names:
        names.append("viking_forget")
    return names


def _normalize_messages(messages: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(messages, str):
        return [{"role": "user", "parts": [{"type": "text", "text": messages}]}]
    normalized = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role not in {"user", "assistant", "system", "tool"}:
            role = "user"
        if message.get("parts") is not None:
            normalized.append(
                {
                    "role": "assistant" if role in {"system", "tool"} else role,
                    "parts": list(message.get("parts") or []),
                }
            )
            continue
        content = str(message.get("content", ""))
        if role == "tool":
            normalized.append(
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool",
                            "tool_id": str(message.get("tool_call_id") or message.get("id") or ""),
                            "tool_name": str(message.get("name") or ""),
                            "tool_output": content,
                            "tool_status": "completed",
                        }
                    ],
                }
            )
        elif role == "system":
            normalized.append({"role": "assistant", "parts": [{"type": "text", "text": content}]})
        else:
            normalized.append({"role": role, "parts": [{"type": "text", "text": content}]})
    return normalized


def _format_retrieval_result(result: Any) -> str:
    lines: list[str] = []
    for index, (context_type, item) in enumerate(iter_result_items(result), start=1):
        uri = item_value(item, "uri", "")
        score = item_value(item, "score")
        abstract = item_value(item, "abstract") or item_value(item, "overview") or ""
        score_text = "" if score is None else f" score={score}"
        lines.append(f"[{index}] {context_type}{score_text} {uri}\n{abstract}".strip())
    if not lines:
        return "No OpenViking contexts matched."
    return "\n\n".join(lines)


def _search_archive_payload(
    payload: dict[str, Any],
    query: str,
    *,
    max_matches: int,
) -> dict[str, Any]:
    tokens = [token for token in re.findall(r"[a-z0-9_]+", query.lower()) if len(token) > 1]
    sections = _archive_sections(payload)
    matches: list[dict[str, str]] = []
    for label, text in sections:
        haystack = text.lower()
        if tokens and not all(token in haystack for token in tokens):
            continue
        matches.append({"section": label, "snippet": _snippet(text, tokens)})
        if len(matches) >= max_matches:
            break
    return {"matches": matches, "count": len(matches)}


def _grep_session_history(
    client: Any,
    *,
    session_id: str,
    query: str,
    max_matches: int,
) -> dict[str, Any]:
    history_uri = f"viking://session/{session_id}/history"
    tokens = _archive_query_tokens(query)
    try:
        result = call_openviking(
            client,
            "grep",
            uri=history_uri,
            pattern=_archive_grep_pattern(query),
            case_insensitive=True,
            node_limit=None,
        )
    except Exception:
        logger.debug("OpenViking archive history grep failed", exc_info=True)
        return {"matches": [], "count": 0, "source": history_uri}
    return _filter_grep_result(result, tokens=tokens, max_matches=max_matches, source=history_uri)


def _match_count(result: dict[str, Any] | None) -> int:
    if not result:
        return 0
    for key in ("count", "match_count"):
        value = result.get(key)
        if value is not None:
            return int(value or 0)
    matches = result.get("matches")
    return len(matches) if isinstance(matches, list) else 0


def _archive_grep_pattern(query: str) -> str:
    tokens = _archive_query_tokens(query)
    if not tokens:
        return re.escape(query) or ".*"
    return re.escape(tokens[0])


def _archive_query_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_]+", query.lower()) if len(token) > 1]


def _filter_grep_result(
    result: Any,
    *,
    tokens: list[str],
    max_matches: int,
    source: str,
) -> dict[str, Any]:
    raw_matches = result.get("matches", []) if isinstance(result, dict) else result
    matches: list[Any] = []
    for match in raw_matches if isinstance(raw_matches, list) else []:
        text = _grep_match_text(match).lower()
        if tokens and not all(token in text for token in tokens):
            continue
        matches.append(match)
        if len(matches) >= max_matches:
            break

    filtered = {"source": source}
    if isinstance(result, dict):
        filtered.update({key: value for key, value in result.items() if key != "matches"})
    filtered["matches"] = matches
    filtered["count"] = len(matches)
    filtered["match_count"] = len(matches)
    return filtered


def _grep_match_text(match: Any) -> str:
    if isinstance(match, dict):
        return str(
            match.get("content")
            or match.get("line")
            or match.get("text")
            or match.get("snippet")
            or ""
        )
    return str(match)


def _archive_sections(payload: dict[str, Any]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if payload.get("overview"):
        sections.append(
            (f"archive:{payload.get('archive_id', 'archive')}:overview", payload["overview"])
        )
    if payload.get("abstract"):
        sections.append(
            (f"archive:{payload.get('archive_id', 'archive')}:abstract", payload["abstract"])
        )
    if payload.get("latest_archive_overview"):
        sections.append(("latest_archive_overview", payload["latest_archive_overview"]))
    for archive in payload.get("pre_archive_abstracts") or []:
        sections.append(
            (
                f"archive:{archive.get('archive_id', 'archive')}:abstract",
                str(archive.get("abstract") or ""),
            )
        )
    for index, message in enumerate(payload.get("messages") or [], start=1):
        text_parts = []
        for part in message.get("parts") or []:
            text_parts.extend(
                str(part.get(key) or "")
                for key in ("text", "abstract", "tool_output")
                if part.get(key)
            )
        if text_parts:
            sections.append((f"message:{index}:{message.get('role', '')}", "\n".join(text_parts)))
    return sections


def _snippet(text: str, tokens: list[str], *, radius: int = 240) -> str:
    if not text:
        return ""
    lower = text.lower()
    positions = [lower.find(token) for token in tokens if token and lower.find(token) >= 0]
    start = max(0, min(positions) - radius) if positions else 0
    end = min(len(text), start + radius * 2)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix
