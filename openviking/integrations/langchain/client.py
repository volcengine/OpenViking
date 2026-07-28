# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for LangChain/LangGraph integration adapters."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

from openviking.utils.async_client_cache import LoopScopedAsyncClientCache

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
_ASYNC_INITIALIZATION_LOCKS_ATTR = "_openviking_integration_initialize_locks"


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
    actor_peer_id: str | None = None
    path: str | None = None
    timeout: float = 60.0
    extra_headers: dict[str, str] | None = None
    auto_initialize: bool = True
    async_client: Any = None


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


class OpenVikingAsyncClientHandle:
    """Lazy, loop-local async client wrapper with one-shot recovery for safe reads.

    Method calls through the handle are recovery-safe. Direct client attribute
    access is best-effort and may be unavailable while recovery replaces the
    underlying client.
    """

    def __init__(self, connection: OpenVikingConnection):
        self._connection = connection
        self._client: Any = None
        self._client_lock = asyncio.Lock()
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    @property
    def _initialized(self) -> bool:
        client = self._client
        return bool(client is not None and _async_client_is_initialized(client))

    async def initialize(self) -> None:
        """Initialize the underlying client once."""

        await self.get()

    async def close(self) -> None:
        owner_loop = self._loop
        current_loop = asyncio.get_running_loop()
        if owner_loop is None or owner_loop is current_loop:
            await self.reset()
            return
        if owner_loop.is_running():
            reset = self.reset()
            try:
                future = asyncio.run_coroutine_threadsafe(reset, owner_loop)
            except RuntimeError:
                reset.close()
            else:
                await asyncio.wrap_future(future)
                return
        await self._reset_without_lock()

    async def reset(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        await self._close_client(client)

    async def _reset_if_current(self, client: Any) -> None:
        """Reset only when ``client`` is still the active snapshot."""

        async with self._client_lock:
            if self._client is not client:
                return
            self._client = None
        await self._close_client(client)

    async def _reset_without_lock(self) -> None:
        """Detach a client after its originating loop has stopped."""

        client = self._client
        self._client = None
        await self._close_client(client)

    async def _close_client(self, client: Any) -> None:
        if client is None:
            return
        try:
            await aclose_openviking_client(client)
        except Exception:
            logger.debug("OpenViking async client close during recovery failed", exc_info=True)

    async def get(self) -> Any:
        """Return the current client snapshot, initializing it when necessary.

        Read raw properties immediately and do not retain this client: a later
        recovery may replace and close it.
        """

        current_loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current_loop
        elif self._loop is not current_loop:
            raise RuntimeError(
                "OpenVikingAsyncClientHandle is loop-local and cannot be reused "
                "from another event loop"
            )
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = await _create_async_client_from_connection(self._connection)
        return self._client

    async def _openviking_acall(self, method_name: str, /, **kwargs: Any) -> Any:
        return await self._call_with_recovery(method_name, **kwargs)

    async def _call_with_recovery(
        self,
        method_name: str,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        client = await self.get()
        try:
            return await _acall_client_method(
                client,
                method_name,
                *args,
                **kwargs,
            )
        except Exception as exc:
            if not _is_recoverable_client_error(exc):
                raise
            await self._reset_if_current(client)
            if not _should_retry_method(method_name):
                raise
            retry_client = await self.get()
            try:
                return await _acall_client_method(
                    retry_client,
                    method_name,
                    *args,
                    **kwargs,
                )
            except Exception as retry_exc:
                if _is_recoverable_client_error(retry_exc):
                    await self._reset_if_current(retry_client)
                raise

    def __copy__(self) -> OpenVikingAsyncClientHandle:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> OpenVikingAsyncClientHandle:
        copied = type(self)(copy.deepcopy(self._connection, memo))
        memo[id(self)] = copied
        return copied

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        client = self._client
        if client is not None:
            attr = getattr(client, name)
            if not callable(attr):
                return attr

        async def recovered_method(*args: Any, **kwargs: Any) -> Any:
            return await self._call_with_recovery(name, *args, **kwargs)

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


async def ensure_async_client(
    connection: OpenVikingConnection,
    *,
    client_cache: LoopScopedAsyncClientCache | None = None,
    embedded_client_factory: Callable[[], Any] | None = None,
) -> Any:
    """Return a client suitable for non-blocking OpenViking calls.

    An explicitly supplied async client is preferred. Existing synchronous
    clients remain supported and are dispatched through a worker thread by
    :func:`acall_openviking`. Internally created HTTP handles can be scoped to
    the running loop with ``client_cache``. Embedded ``path=`` connections
    intentionally use the synchronous client so their stateful async internals
    remain on OpenViking's shared background loop.
    """

    client = connection.async_client
    if client is None and connection.client is not None:
        client = connection.client
    if client is None:
        if _uses_http_client(connection):
            if client_cache is None:
                handle = OpenVikingAsyncClientHandle(connection)
            else:
                handle = client_cache.get(lambda: OpenVikingAsyncClientHandle(connection))
            if connection.auto_initialize:
                await handle.get()
            return handle
        if embedded_client_factory is not None:
            if client_cache is not None:
                # The thread hop is load-bearing: without a running event loop,
                # the embedded singleton occupies the cache's shared fallback slot.
                return await asyncio.to_thread(
                    client_cache.get,
                    embedded_client_factory,
                )
            return await asyncio.to_thread(embedded_client_factory)
        return await asyncio.to_thread(_create_client_from_connection, connection)
    if connection.auto_initialize and hasattr(client, "initialize"):
        await _ainitialize_client(client)
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


async def aapply_commit_policy(
    client: Any,
    session_id: str,
    policy: OpenVikingCommitPolicy | None,
) -> dict[str, Any] | None:
    """Apply the configured session commit policy without blocking the event loop."""

    if policy is None or policy.mode == "never":
        return None
    if policy.mode == "always":
        return await acall_openviking(
            client,
            "commit_session",
            session_id=session_id,
        )
    if policy.mode != "pending_tokens":
        raise ValueError(f"Unsupported OpenViking commit policy: {policy.mode}")

    try:
        session = await acall_openviking(
            client,
            "get_session",
            session_id=session_id,
            auto_create=False,
        )
    except Exception:
        logger.debug(
            "Skipping OpenViking pending-token commit because session lookup failed",
            exc_info=True,
        )
        return None
    pending_tokens = int(item_value(session, "pending_tokens", 0) or 0)
    if pending_tokens < policy.pending_token_threshold:
        return None
    return await acall_openviking(
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


async def acall_openviking(client: Any, method_name: str, /, **kwargs: Any) -> Any:
    """Call an async or sync client method without blocking the event loop."""

    openviking_call = getattr(client, "_openviking_acall", None)
    if callable(openviking_call):
        return await openviking_call(method_name, **kwargs)
    return await _acall_client_method(client, method_name, **kwargs)


def _create_client_from_connection(connection: OpenVikingConnection) -> Any:
    client: Any
    if connection.url or connection.path is None:
        from openviking.client import SyncHTTPClient

        client = SyncHTTPClient(
            url=connection.url,
            api_key=connection.api_key,
            account=connection.account,
            user=connection.user,
            user_id=connection.user_id,
            actor_peer_id=connection.actor_peer_id,
            timeout=connection.timeout,
            extra_headers=connection.extra_headers,
        )
    else:
        from openviking.sync_client import SyncOpenViking

        client = SyncOpenViking(path=connection.path, actor_peer_id=connection.actor_peer_id)

    if connection.auto_initialize and hasattr(client, "initialize"):
        if not getattr(client, "_initialized", False):
            client.initialize()
    return client


async def _create_async_client_from_connection(connection: OpenVikingConnection) -> Any:
    if not _uses_http_client(connection):
        raise ValueError("Native async clients are created automatically only for HTTP connections")

    from openviking.client import AsyncHTTPClient

    client: Any = AsyncHTTPClient(
        url=connection.url,
        api_key=connection.api_key,
        account=connection.account,
        user=connection.user,
        user_id=connection.user_id,
        actor_peer_id=connection.actor_peer_id,
        timeout=connection.timeout,
        extra_headers=connection.extra_headers,
    )

    try:
        if connection.auto_initialize and hasattr(client, "initialize"):
            await _ainitialize_client(client)
    except BaseException:
        try:
            await aclose_openviking_client(client)
        except Exception:
            logger.debug(
                "OpenViking async client close after initialization failure failed",
                exc_info=True,
            )
        raise
    return client


async def aclose_openviking_client(client: Any) -> None:
    """Close an async or sync OpenViking client without blocking the event loop."""

    close = getattr(client, "close", None)
    if not callable(close):
        return
    if inspect.iscoroutinefunction(close):
        await close()
        return
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        await result


async def aclose_openviking_clients(*clients: Any) -> None:
    """Close every distinct client and re-raise the first cleanup failure."""

    seen: set[int] = set()
    first_error: BaseException | None = None
    for client in clients:
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        try:
            await aclose_openviking_client(client)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _call_client_method(
    client: Any,
    method_name: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    method = getattr(client, method_name)
    return method(*args, **_filter_client_kwargs(method, kwargs))


async def _acall_client_method(
    client: Any,
    method_name: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    method = getattr(client, method_name)
    filtered_kwargs = _filter_client_kwargs(method, kwargs)
    if inspect.iscoroutinefunction(method):
        return await method(*args, **filtered_kwargs)
    result = await asyncio.to_thread(method, *args, **filtered_kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _ainitialize_client(client: Any) -> None:
    if _async_client_is_initialized(client):
        return
    lock = _async_client_initialization_lock(client)
    async with lock:
        if _async_client_is_initialized(client):
            return
        await _acall_client_method(client, "initialize")
        try:
            client._openviking_integration_initialized = True
        except Exception:
            pass


def _async_client_initialization_lock(client: Any) -> asyncio.Lock:
    try:
        locks = inspect.getattr_static(client, _ASYNC_INITIALIZATION_LOCKS_ATTR)
    except AttributeError:
        locks = LoopScopedAsyncClientCache()
        try:
            setattr(client, _ASYNC_INITIALIZATION_LOCKS_ATTR, locks)
        except Exception:
            return asyncio.Lock()
    if not isinstance(locks, LoopScopedAsyncClientCache):
        locks = LoopScopedAsyncClientCache()
        try:
            setattr(client, _ASYNC_INITIALIZATION_LOCKS_ATTR, locks)
        except Exception:
            return asyncio.Lock()
    return locks.get(asyncio.Lock)


def _uses_http_client(connection: OpenVikingConnection) -> bool:
    """Return whether connection settings select the HTTP transport."""

    return bool(connection.url) or connection.path is None


def _async_client_is_initialized(client: Any) -> bool:
    for name in ("_initialized", "_http"):
        try:
            inspect.getattr_static(client, name)
        except AttributeError:
            continue
        value = getattr(client, name, None)
        if name == "_http":
            return value is not None
        return bool(value)
    try:
        inspect.getattr_static(client, "_openviking_integration_initialized")
    except AttributeError:
        return False
    return bool(getattr(client, "_openviking_integration_initialized", False))


def _filter_client_kwargs(method: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` and arguments unsupported by a concrete client variant."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {key: value for key, value in kwargs.items() if value is not None}

    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        return {key: value for key, value in kwargs.items() if value is not None}
    return {
        key: value
        for key, value in kwargs.items()
        if value is not None and key in signature.parameters
    }


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
