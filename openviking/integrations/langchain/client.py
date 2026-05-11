# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for LangChain/LangGraph integration adapters."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)


class OptionalDependencyError(ImportError):
    """Raised when an optional framework dependency is not installed."""


def missing_dependency(extra: str, package: str | None = None) -> OptionalDependencyError:
    package = package or extra
    return OptionalDependencyError(
        f"{package} is required for this OpenViking integration. "
        f'Install it with `pip install "openviking[{extra}]"`.'
    )


@dataclass(slots=True)
class OpenVikingConnection:
    """Connection settings for lazily creating an OpenViking client."""

    client: Any = None
    url: str | None = None
    api_key: str | None = None
    account: str | None = None
    user: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    path: str | None = None
    timeout: float = 60.0
    extra_headers: dict[str, str] | None = None
    auto_initialize: bool = True


@dataclass(slots=True)
class OpenVikingCommitPolicy:
    """Commit behavior for OpenViking-backed agent sessions."""

    mode: Literal["never", "always", "pending_tokens"] = "never"
    pending_token_threshold: int = 8_000


def ensure_client(connection: OpenVikingConnection) -> Any:
    """Return an initialized OpenViking client from explicit or connection settings."""

    client = connection.client
    if client is None:
        if connection.url or connection.path is None:
            from openviking.client import SyncHTTPClient

            client = SyncHTTPClient(
                url=connection.url,
                api_key=connection.api_key,
                account=connection.account,
                user=connection.user,
                user_id=connection.user_id,
                agent_id=connection.agent_id,
                timeout=connection.timeout,
                extra_headers=connection.extra_headers,
            )
        else:
            from openviking.sync_client import SyncOpenViking

            client = SyncOpenViking(path=connection.path)

    if connection.auto_initialize and hasattr(client, "initialize"):
        if not getattr(client, "_initialized", False):
            client.initialize()
    return client


def maybe_commit_session(
    client: Any,
    session_id: str,
    policy: OpenVikingCommitPolicy | None,
) -> dict[str, Any] | None:
    """Commit a session if the configured policy says the live tail is ready."""

    if policy is None or policy.mode == "never":
        return None
    if policy.mode == "always":
        return call_openviking(
            client,
            "commit_session",
            session_id=session_id,
        )
    if policy.mode != "pending_tokens":
        raise ValueError(f"Unsupported OpenViking commit policy: {policy.mode}")

    try:
        session = call_openviking(client, "get_session", session_id=session_id, auto_create=False)
    except Exception:
        logger.debug(
            "Skipping OpenViking pending-token commit because session lookup failed",
            exc_info=True,
        )
        return None
    pending_tokens = int(item_value(session, "pending_tokens", 0) or 0)
    if pending_tokens < policy.pending_token_threshold:
        return None
    return call_openviking(
        client,
        "commit_session",
        session_id=session_id,
    )


def call_openviking(client: Any, method_name: str, /, **kwargs: Any) -> Any:
    """Call a client method, filtering kwargs unsupported by local/HTTP variants."""

    method = getattr(client, method_name)
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**{key: value for key, value in kwargs.items() if value is not None})

    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        filtered = {key: value for key, value in kwargs.items() if value is not None}
    else:
        filtered = {
            key: value
            for key, value in kwargs.items()
            if value is not None and key in signature.parameters
        }
    return method(**filtered)


def result_groups(result: Any) -> list[tuple[str, list[Any]]]:
    """Normalize OpenViking retrieval results into named context groups."""

    if result is None:
        return []
    if isinstance(result, dict):
        return [
            ("memory", list(result.get("memories") or [])),
            ("resource", list(result.get("resources") or [])),
            ("skill", list(result.get("skills") or [])),
        ]
    return [
        ("memory", list(getattr(result, "memories", []) or [])),
        ("resource", list(getattr(result, "resources", []) or [])),
        ("skill", list(getattr(result, "skills", []) or [])),
    ]


def item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def iter_result_items(
    result: Any,
    context_types: Iterable[str] = ("memory", "resource", "skill"),
) -> Iterable[tuple[str, Any]]:
    allowed = set(context_types)
    for context_type, items in result_groups(result):
        if context_type not in allowed:
            continue
        for item in items:
            yield context_type, item


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def stringify(value: Any, *, max_chars: int = 12_000) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated]"
    return text


def extract_message_text(content: Any) -> str:
    """Extract text from LangChain/OpenAI-style message content."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
                elif isinstance(block.get("content"), str):
                    chunks.append(block["content"])
        return "\n".join(chunk for chunk in chunks if chunk)
    if content is None:
        return ""
    return str(content)


def get_latest_user_text(messages: Iterable[Any]) -> str:
    for message in reversed(list(messages)):
        role = getattr(message, "type", None) or getattr(message, "role", None)
        if isinstance(message, dict):
            role = message.get("type") or message.get("role")
            content = message.get("content")
        else:
            content = getattr(message, "content", "")
        if role in {"human", "user"}:
            text = extract_message_text(content).strip()
            if text:
                return text
    return ""
