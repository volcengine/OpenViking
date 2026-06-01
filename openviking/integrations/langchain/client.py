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

_RETRYABLE_READ_METHODS = {
    "abstract",
    "archive_expand",
    "archive_search",
    "find",
    "get_session",
    "get_session_archive",
    "get_session_context",
    "get_status",
    "glob",
    "health",
    "is_healthy",
    "ls",
    "overview",
    "read",
    "relations",
    "search",
    "session_exists",
    "stat",
}

_RECOVERABLE_OPENVIKING_CODES = {"DEADLINE_EXCEEDED", "UNAVAILABLE"}
_RECOVERABLE_RUNTIME_MESSAGE_FRAGMENTS = (
    "event loop is closed",
    "attached to a different loop",
    "bound to a different event loop",
)


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


class OpenVikingClientHandle:
    """Lazy OpenViking client wrapper with one-shot recovery for safe reads."""

    def __init__(self, connection: OpenVikingConnection):
        self._connection = connection
        self._client: Any = None

    @property
    def _initialized(self) -> bool:
        client = self._client
        return bool(client is not None and getattr(client, "_initialized", False))

    def close(self) -> None:
        self.reset()

    def reset(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            logger.debug("OpenViking client close during recovery failed", exc_info=True)

    def get(self) -> Any:
        if self._client is None:
            self._client = _create_client_from_connection(self._connection)
        return self._client

    def _openviking_call(self, method_name: str, /, **kwargs: Any) -> Any:
        return self._call_with_recovery(method_name, **kwargs)

    def _call_with_recovery(self, method_name: str, /, *args: Any, **kwargs: Any) -> Any:
        try:
            return _call_client_method(self.get(), method_name, *args, **kwargs)
        except Exception as exc:
            if not _is_recoverable_client_error(exc):
                raise
            self.reset()
            if not _should_retry_method(method_name):
                raise
            try:
                return _call_client_method(self.get(), method_name, *args, **kwargs)
            except Exception as retry_exc:
                if _is_recoverable_client_error(retry_exc):
                    self.reset()
                raise

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.get(), name)
        if not callable(attr):
            return attr

        def recovered_method(*args: Any, **kwargs: Any) -> Any:
            return self._call_with_recovery(name, *args, **kwargs)

        return recovered_method


def ensure_client(connection: OpenVikingConnection) -> Any:
    """Return an initialized OpenViking client from explicit or connection settings."""

    client = connection.client
    if client is None:
        if connection.url or connection.path is None:
            handle = OpenVikingClientHandle(connection)
            if connection.auto_initialize:
                handle.get()
            return handle
        return _create_client_from_connection(connection)
    if connection.auto_initialize and hasattr(client, "initialize"):
        if not getattr(client, "_initialized", False):
            client.initialize()
    return client


def apply_commit_policy(
    client: Any,
    session_id: str,
    policy: OpenVikingCommitPolicy | None,
) -> dict[str, Any] | None:
    """Apply the configured session commit policy."""

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

    openviking_call = getattr(client, "_openviking_call", None)
    if callable(openviking_call):
        return openviking_call(method_name, **kwargs)
    return _call_client_method(client, method_name, **kwargs)


def _create_client_from_connection(connection: OpenVikingConnection) -> Any:
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


def _call_client_method(
    client: Any,
    method_name: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    method = getattr(client, method_name)
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(
            *args,
            **{key: value for key, value in kwargs.items() if value is not None},
        )

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
    return method(*args, **filtered)


def _should_retry_method(method_name: str) -> bool:
    return method_name in _RETRYABLE_READ_METHODS


def _is_recoverable_client_error(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.upper() in _RECOVERABLE_OPENVIKING_CODES:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        return any(fragment in message for fragment in _RECOVERABLE_RUNTIME_MESSAGE_FRAGMENTS)
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is available in HTTP installs
        return False
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


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
