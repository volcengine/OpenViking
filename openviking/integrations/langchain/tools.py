# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain tool factory for OpenViking primitives."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Iterable, Literal

from pydantic import BeforeValidator, Field

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


def _normalize_read_mode_input(value: Any) -> str:
    return str(value or "read").lower().strip()


OpenVikingReadMode = Literal["abstract", "overview", "read"]
OpenVikingReadModeInput = Annotated[
    OpenVikingReadMode,
    BeforeValidator(_normalize_read_mode_input),
]


def create_openviking_tools(
    *,
    client: Any = None,
    url: str | None = None,
    api_key: str | None = None,
    account: str | None = None,
    user: str | None = None,
    user_id: str | None = None,
    path: str | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
    auto_initialize: bool = True,
    profile: str = "agent",
    peer_id: str | None = None,
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
                    path=path,
                    timeout=timeout,
                    extra_headers=extra_headers,
                    auto_initialize=auto_initialize,
                )
            )
        return cached_client

    def viking_find(
        query: Annotated[str, Field(description="Natural-language query to match semantically.")],
        target_uri: Annotated[
            str,
            Field(description="Optional OpenViking URI scope to search within."),
        ] = "",
        limit: Annotated[int, Field(description="Maximum number of matches to return.")] = 8,
        min_score: Annotated[
            float | None,
            Field(description="Optional backend relevance threshold."),
        ] = None,
    ) -> str:
        """Run stateless semantic retrieval over OpenViking targets."""

        result = call_openviking(
            get_client(),
            "find",
            query=query,
            target_uri=target_uri,
            peer_id=peer_id,
            limit=limit,
            score_threshold=min_score,
        )
        return _format_retrieval_result(result)

    def viking_search(
        query: Annotated[str, Field(description="Natural-language query to match semantically.")],
        target_uri: Annotated[
            str,
            Field(description="Optional OpenViking URI scope to search within."),
        ] = "",
        session_id: Annotated[
            str | None,
            Field(description="Optional OpenViking session id for session-aware retrieval."),
        ] = None,
        limit: Annotated[int, Field(description="Maximum number of matches to return.")] = 8,
        min_score: Annotated[
            float | None,
            Field(description="Optional backend relevance threshold."),
        ] = None,
    ) -> str:
        """Run session-aware semantic retrieval over OpenViking targets."""

        result = call_openviking(
            get_client(),
            "search",
            query=query,
            target_uri=target_uri,
            session_id=session_id,
            peer_id=peer_id,
            limit=limit,
            score_threshold=min_score,
        )
        return _format_retrieval_result(result)

    def viking_browse(
        uri: Annotated[
            str,
            Field(description="OpenViking namespace or directory URI to list."),
        ] = "viking://",
        recursive: Annotated[
            bool,
            Field(description="Whether to include nested descendants in the listing."),
        ] = False,
        pattern: Annotated[
            str | None,
            Field(description="Optional glob pattern for discovering matching OpenViking URIs."),
        ] = None,
    ) -> str:
        """List child entries under an OpenViking namespace or directory URI.

        Use this to inspect structure and discover file/document URIs. When
        pattern is set, the tool returns glob matches instead of a direct
        directory listing.
        """

        active_client = get_client()
        if pattern:
            result = call_openviking(active_client, "glob", pattern=pattern, uri=uri)
        else:
            result = call_openviking(active_client, "ls", uri=uri, recursive=recursive)
        return stringify(result, max_chars=12_000)

    def viking_read(
        uris: Annotated[
            str | list[str],
            Field(description="One or more file/document OpenViking URIs to read."),
        ],
        max_chars: Annotated[
            int,
            Field(description="Maximum characters to include per URI result."),
        ] = 12_000,
        content_mode: Annotated[
            OpenVikingReadModeInput,
            Field(
                description="Content depth: abstract is shortest, overview is medium, read is full."
            ),
        ] = "read",
    ) -> str:
        """Read file/document OpenViking URIs.

        Directory URIs are not readable; call viking_browse on directories to
        list children, then call viking_read on returned file/document URIs.
        """

        active_client = get_client()
        uri_list = [uris] if isinstance(uris, str) else uris
        mode = str(content_mode or "read").lower().strip()
        payload = []
        for uri in uri_list:
            try:
                content = call_openviking(active_client, mode, uri=uri)
            except Exception as exc:
                if not _is_directory_read_error(exc):
                    raise
                payload.append(
                    {
                        "uri": uri,
                        "content_mode": mode,
                        "error": "directory_uri_not_readable",
                        "message": (
                            "This URI is a directory. Use viking_browse on this URI to list "
                            "children, then call viking_read on file/document URIs."
                        ),
                    }
                )
            else:
                payload.append(
                    {
                        "uri": uri,
                        "content_mode": mode,
                        "content": stringify(content, max_chars=max_chars),
                    }
                )
        return stringify(payload, max_chars=max_chars * max(1, len(uri_list)))

    def viking_grep(
        uri: Annotated[
            str,
            Field(description="File/document OpenViking URI whose content should be searched."),
        ],
        pattern: Annotated[
            str,
            Field(description="Grep-style text or regex pattern to search for."),
        ],
        case_insensitive: Annotated[
            bool,
            Field(description="Whether to match pattern without case sensitivity."),
        ] = False,
        node_limit: Annotated[
            int,
            Field(description="Maximum number of matching content nodes to return."),
        ] = 20,
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
        messages: Annotated[
            str | list[dict[str, Any]],
            Field(description="Message text or role/content message objects to append."),
        ],
        session_id: Annotated[
            str | None,
            Field(description="OpenViking session id. A new session is created when omitted."),
        ] = None,
        commit: Annotated[
            bool,
            Field(description="Whether to commit the appended session messages immediately."),
        ] = True,
    ) -> str:
        """Append explicit durable memories or conversation messages to an OpenViking session.

        This is a write operation. User-facing hosts should expose it only for
        confirmed "remember/save this" workflows because normal conversation
        capture is usually handled by lifecycle hooks.
        """

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
                peer_id=peer_id,
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
        session_id: Annotated[
            str,
            Field(description="OpenViking session id whose committed archive context to search."),
        ],
        query: Annotated[
            str,
            Field(description="Natural-language query to match against committed session context."),
        ],
        archive_id: Annotated[
            str | None,
            Field(description="Optional specific archive id to search instead of all history."),
        ] = None,
        token_budget: Annotated[
            int,
            Field(description="Maximum session context token budget to inspect when needed."),
        ] = 128_000,
        max_matches: Annotated[
            int,
            Field(description="Maximum number of archive matches to return."),
        ] = 8,
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
        session_id: Annotated[
            str,
            Field(description="OpenViking session id that owns the archive."),
        ],
        archive_id: Annotated[
            str,
            Field(description="Committed archive id to expand."),
        ],
        max_chars: Annotated[
            int,
            Field(description="Maximum characters to include in the expanded archive result."),
        ] = 20_000,
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
        path: Annotated[
            str,
            Field(
                description=(
                    "Resource source to import, such as a URL, repository, uploaded file, "
                    "directory, or client-visible local path."
                )
            ),
        ],
        to: Annotated[
            str | None,
            Field(description="Optional destination OpenViking URI for the imported resource."),
        ] = None,
        parent: Annotated[
            str | None,
            Field(description="Optional parent OpenViking URI under which to place the resource."),
        ] = None,
        reason: Annotated[
            str,
            Field(description="Short reason for importing this resource."),
        ] = "",
        instruction: Annotated[
            str,
            Field(description="Optional indexing or extraction instruction for the resource."),
        ] = "",
        wait: Annotated[
            bool,
            Field(description="Whether to wait for resource ingestion to finish before returning."),
        ] = False,
        timeout: Annotated[
            float | None,
            Field(description="Optional wait timeout in seconds for resource ingestion."),
        ] = None,
    ) -> str:
        """Import an explicit resource into OpenViking.

        This is a resource-management operation for user-approved URLs,
        repositories, uploaded files, or local paths available to the client. It
        is not for ordinary chat facts or ad hoc memory notes.
        """

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
        data: Annotated[
            dict[str, Any] | str,
            Field(description="Skill definition as a mapping or serialized skill document."),
        ],
        wait: Annotated[
            bool,
            Field(description="Whether to wait for skill registration to finish before returning."),
        ] = False,
        timeout: Annotated[
            float | None,
            Field(description="Optional wait timeout in seconds for skill registration."),
        ] = None,
    ) -> str:
        """Register a reusable OpenViking skill for trusted admin workflows."""

        result = call_openviking(
            get_client(),
            "add_skill",
            data=data,
            wait=wait,
            timeout=timeout,
        )
        return stringify(result, max_chars=8_000)

    def viking_health() -> str:
        """Check OpenViking health/status for diagnostics."""

        active_client = get_client()
        if hasattr(active_client, "get_status"):
            status = call_openviking(active_client, "get_status")
            return stringify(_format_openviking_health(status), max_chars=8_000)
        if hasattr(active_client, "is_healthy"):
            return compact_json(
                _format_openviking_health({"healthy": call_openviking(active_client, "is_healthy")})
            )
        return compact_json(_format_openviking_health({"healthy": True}))

    def viking_forget(
        uri: Annotated[
            str,
            Field(description="OpenViking URI to remove."),
        ],
        recursive: Annotated[
            bool,
            Field(description="Whether to remove descendants recursively."),
        ] = False,
    ) -> str:
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


def _is_directory_read_error(exc: Exception) -> bool:
    details = getattr(exc, "details", {}) or {}
    return (
        getattr(exc, "code", None) == "INVALID_ARGUMENT"
        and details.get("expected") == "file"
        and details.get("actual") == "directory"
    )


def _format_openviking_health(status: Any) -> dict[str, Any]:
    state = _infer_health_state(status)
    return {
        "backend": "OpenViking",
        "healthy": state == "healthy",
        "state": state,
        "note": "OpenViking is the context memory backend; VikingDB is internal vector/index storage.",
        "summary": _safe_status_summary(status),
    }


def _infer_health_state(status: Any) -> str:
    if isinstance(status, dict):
        for key in ("healthy", "ok"):
            value = status.get(key)
            if isinstance(value, bool):
                return "healthy" if value else "unhealthy"
        state = str(status.get("status") or status.get("state") or "").lower()
        if state in {"ok", "healthy", "ready", "running"}:
            return "healthy"
        if state in {"error", "failed", "unhealthy"}:
            return "unhealthy"
        if state in {"degraded", "initializing", "starting", "pending"}:
            return state
    return "unknown"


def _safe_status_summary(status: Any) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {"type": type(status).__name__}

    summary: dict[str, Any] = {}
    for key in ("healthy", "ok", "status", "state", "state_detail"):
        if key in status:
            value = _safe_status_value(status[key])
            if value is not None:
                summary[key] = value

    components = status.get("components") or status.get("services")
    if isinstance(components, dict | list | tuple):
        summary["component_count"] = len(components)

    return summary or {"type": "dict"}


def _safe_status_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if "://" in lowered or "@" in lowered:
            return None
        return value if len(value) <= 64 else f"{value[:61]}..."
    return None


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
