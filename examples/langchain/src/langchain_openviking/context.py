# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""High-level OpenViking context lifecycle helpers for LangChain."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterator

from pydantic import PrivateAttr

try:
    from langchain_core.messages import BaseMessage, SystemMessage
    from langchain_core.runnables import ConfigurableFieldSpec, RunnableLambda
    from langchain_core.runnables.history import RunnableWithMessageHistory
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from langchain_openviking.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from langchain_openviking._async_client_cache import LoopScopedAsyncClientCache
from langchain_openviking.client import (
    OpenVikingCommitPolicy,
    OpenVikingConnection,
    acall_openviking,
    aclose_openviking_clients,
    call_openviking,
    ensure_async_client,
    ensure_client,
    extract_message_text,
    get_latest_user_text,
    is_not_found_error,
)
from langchain_openviking.history import (
    OpenVikingChatMessageHistory,
    context_parts_from_documents,
)
from langchain_openviking.messages import OPENVIKING_CONTEXT_MARKER
from langchain_openviking.recording import OpenVikingSessionRecorder
from langchain_openviking.retrievers import OpenVikingRetriever

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenVikingAssembledContext:
    """Context block plus structured references assembled before an agent turn."""

    block: str = ""
    context_parts: list[dict[str, Any]] = field(default_factory=list)
    session_context: dict[str, Any] = field(default_factory=dict)
    recall_documents: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class _AsyncSessionWriteLockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class _AsyncSessionWriteLockPool:
    """Serialize completed writes without retaining inactive session IDs."""

    def __init__(self) -> None:
        self._entries: dict[str, _AsyncSessionWriteLockEntry] = {}

    @asynccontextmanager
    async def acquire(self, session_id: str) -> AsyncIterator[None]:
        entry = self._entries.get(session_id)
        if entry is None:
            entry = _AsyncSessionWriteLockEntry()
            self._entries[session_id] = entry
        entry.users += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.users -= 1
            if entry.users == 0 and self._entries.get(session_id) is entry:
                self._entries.pop(session_id, None)


@dataclass(slots=True)
class _SyncSessionWriteLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


class _SyncSessionWriteLockPool:
    """Serialize synchronous writes without retaining inactive session IDs."""

    def __init__(self) -> None:
        self._entries: dict[str, _SyncSessionWriteLockEntry] = {}
        self._entries_lock = threading.Lock()

    @contextmanager
    def acquire(self, session_id: str) -> Iterator[None]:
        with self._entries_lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = _SyncSessionWriteLockEntry()
                self._entries[session_id] = entry
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._entries_lock:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(session_id) is entry:
                    self._entries.pop(session_id, None)


class _InvocationOpenVikingChatMessageHistory(OpenVikingChatMessageHistory):
    """Hold the entry history snapshot for one runnable invocation."""

    def __init__(
        self,
        *args: Any,
        recorder: OpenVikingSessionRecorder,
        sync_write_lock_pool: _SyncSessionWriteLockPool,
        async_write_lock_pools: LoopScopedAsyncClientCache,
        **kwargs: Any,
    ):
        super().__init__(*args, _recorder=recorder, **kwargs)
        self._entry_snapshot: list[BaseMessage] | None = None
        self._sync_write_lock_pool = sync_write_lock_pool
        self._async_write_lock_pools = async_write_lock_pools

    @property
    def messages(self) -> list[BaseMessage]:  # type: ignore[override]
        if self._entry_snapshot is None:
            self._entry_snapshot = list(super().messages)
        return list(self._entry_snapshot)

    async def aget_messages(self) -> list[BaseMessage]:
        if self._entry_snapshot is None:
            self._entry_snapshot = list(await super().aget_messages())
        return list(self._entry_snapshot)

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        try:
            with self._sync_write_lock_pool.acquire(self.session_id):
                super().add_messages(messages)
        finally:
            self._entry_snapshot = None

    async def aadd_messages(self, messages: Sequence[BaseMessage]) -> None:
        try:
            lock_pool = self._async_write_lock_pools.get(_AsyncSessionWriteLockPool)
            async with lock_pool.acquire(self.session_id):
                await super().aadd_messages(messages)
        finally:
            self._entry_snapshot = None


class OpenVikingSessionContextAssembler:
    """Assemble session working memory, archive context, and recall results."""

    def __init__(
        self,
        *,
        client: Any = None,
        async_client: Any = None,
        retriever: OpenVikingRetriever | None = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        actor_peer_id: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        auto_initialize: bool = True,
        target_uri: str | list[str] = "",
        limit: int = 5,
        score_threshold: float | None = None,
        token_budget: int = 128_000,
        include_session_context: bool = True,
        include_active_messages: bool = True,
        include_recall: bool = True,
        recall_header: str = "Relevant OpenViking context:",
    ):
        self._connection = OpenVikingConnection(
            client=client,
            async_client=async_client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
        )
        self.retriever = retriever or OpenVikingRetriever(
            client=client,
            async_client=async_client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            search_mode="search",
        )
        self.token_budget = token_budget
        self.include_session_context = include_session_context
        self.include_active_messages = include_active_messages
        self.include_recall = include_recall
        self.recall_header = recall_header
        self._owns_client = client is None
        self._owns_retriever = retriever is None
        self._client_cache: Any = None
        self._client_cache_lock = threading.Lock()
        self._async_clients = LoopScopedAsyncClientCache()
        self._closed = False

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None = None,
    ) -> OpenVikingSessionContextAssembler:
        """Copy configuration with fresh caches and preserved injected ownership."""

        memo = {} if memo is None else memo
        for client in (self._connection.client, self._connection.async_client):
            if client is not None:
                memo[id(client)] = client
        if not self._owns_retriever:
            memo[id(self.retriever)] = self.retriever

        copied = type(self).__new__(type(self))
        memo[id(self)] = copied
        copied.__dict__ = {
            name: (
                None
                if name == "_client_cache"
                else threading.Lock()
                if name == "_client_cache_lock"
                else LoopScopedAsyncClientCache()
                if name == "_async_clients"
                else deepcopy(value, memo)
            )
            for name, value in self.__dict__.items()
        }
        return copied

    def assemble(
        self,
        *,
        session_id: str,
        query: str = "",
    ) -> OpenVikingAssembledContext:
        client = self._get_client()
        session_context = self._get_session_context(client, session_id)
        recall_documents = self._get_recall_documents(
            session_id,
            query,
        )
        return self._assembled_context(session_context, recall_documents)

    async def aassemble(
        self,
        *,
        session_id: str,
        query: str = "",
    ) -> OpenVikingAssembledContext:
        """Asynchronously assemble session and recall context."""

        client = await self._get_async_client()
        session_context = await self._aget_session_context(client, session_id)
        recall_documents = await self._aget_recall_documents(
            session_id,
            query,
        )
        return self._assembled_context(session_context, recall_documents)

    def _assembled_context(
        self,
        session_context: dict[str, Any],
        recall_documents: list[Any],
    ) -> OpenVikingAssembledContext:
        block = self._format_context_block(session_context, recall_documents)
        return OpenVikingAssembledContext(
            block=block,
            context_parts=context_parts_from_documents(recall_documents),
            session_context=session_context,
            recall_documents=recall_documents,
        )

    def _get_client(self) -> Any:
        self._raise_if_closed()
        if self._client_cache is None:
            with self._client_cache_lock:
                self._raise_if_closed()
                if self._client_cache is None:
                    self._client_cache = ensure_client(self._connection)
        client = self._client_cache
        self._raise_if_closed()
        return client

    async def get_async_client(self) -> Any:
        """Return the async client interface used by this assembler.

        Injected clients are returned unchanged and remain caller-owned.
        HTTP-backed connections return an internally managed, loop-local handle
        whose method calls support recovery. Direct attributes on that handle
        are best-effort during recovery; use ``await handle.get()`` only to read
        raw properties immediately because recovery may replace that snapshot.
        """

        self._raise_if_closed()
        client = await ensure_async_client(
            self._connection,
            client_cache=self._async_clients,
        )
        self._raise_if_closed()
        return client

    async def _get_async_client(self) -> Any:
        return await self.get_async_client()

    async def aclose(self) -> None:
        """Release clients and the default retriever owned by this assembler."""

        if self._closed:
            return
        self._closed = True
        with self._client_cache_lock:
            sync_client = self._client_cache
            self._client_cache = None
        async_clients = await asyncio.to_thread(self._async_clients.pop_all)

        first_error: BaseException | None = None
        try:
            await aclose_openviking_clients(
                sync_client if self._owns_client else None,
                *async_clients,
            )
        except BaseException as exc:
            first_error = exc
        if self._owns_retriever:
            try:
                await self.retriever.aclose()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError("OpenVikingSessionContextAssembler is closed")

    def _ensure_session(self, client: Any, session_id: str) -> None:
        try:
            call_openviking(client, "create_session", session_id=session_id)
        except Exception:
            logger.debug("OpenViking session ensure failed", exc_info=True)
            pass

    async def _aensure_session(self, client: Any, session_id: str) -> None:
        try:
            await acall_openviking(client, "create_session", session_id=session_id)
        except Exception:
            logger.debug("OpenViking session ensure failed", exc_info=True)

    def _get_session_context(self, client: Any, session_id: str) -> dict[str, Any]:
        if not self.include_session_context:
            # Without a context read there is no NOT_FOUND signal to create from.
            # Preserve the previous guarantee that recall receives a valid session.
            self._ensure_session(client, session_id)
            return {}
        try:
            return call_openviking(
                client,
                "get_session_context",
                session_id=session_id,
                token_budget=self.token_budget,
            )
        except Exception as exc:
            if is_not_found_error(exc):
                # First use: materialize the empty session, but do not issue a second
                # context read because the newly created session has no context yet.
                self._ensure_session(client, session_id)
                return {}
            logger.debug("OpenViking session context assembly failed", exc_info=True)
            return {}

    async def _aget_session_context(
        self,
        client: Any,
        session_id: str,
    ) -> dict[str, Any]:
        if not self.include_session_context:
            # Without a context read there is no NOT_FOUND signal to create from.
            # Preserve the previous guarantee that recall receives a valid session.
            await self._aensure_session(client, session_id)
            return {}
        try:
            return await acall_openviking(
                client,
                "get_session_context",
                session_id=session_id,
                token_budget=self.token_budget,
            )
        except Exception as exc:
            if is_not_found_error(exc):
                # First use: materialize the empty session, but do not issue a second
                # context read because the newly created session has no context yet.
                await self._aensure_session(client, session_id)
                return {}
            logger.debug("OpenViking session context assembly failed", exc_info=True)
            return {}

    def _get_recall_documents(
        self,
        session_id: str,
        query: str,
    ) -> list[Any]:
        if not self.include_recall or not query:
            return []
        try:
            return list(
                _retriever_for_session(
                    self.retriever,
                    session_id,
                ).invoke(query)
            )
        except Exception:
            logger.debug("OpenViking recall retrieval failed", exc_info=True)
            return []

    async def _aget_recall_documents(
        self,
        session_id: str,
        query: str,
    ) -> list[Any]:
        if not self.include_recall or not query:
            return []
        try:
            return list(
                await _retriever_for_session(
                    self.retriever,
                    session_id,
                ).ainvoke(query)
            )
        except Exception:
            logger.debug("OpenViking recall retrieval failed", exc_info=True)
            return []

    def _format_context_block(
        self,
        session_context: dict[str, Any],
        recall_documents: Sequence[Any],
    ) -> str:
        sections: list[str] = []
        latest_archive = str(session_context.get("latest_archive_overview") or "").strip()
        if latest_archive:
            sections.append("Session archive overview:\n" + latest_archive)

        abstracts = []
        for archive in session_context.get("pre_archive_abstracts") or []:
            archive_id = archive.get("archive_id") or "archive"
            abstract = str(archive.get("abstract") or "").strip()
            if abstract:
                abstracts.append(f"[{archive_id}] {abstract}")
        if abstracts:
            sections.append("Older archive abstracts:\n" + "\n".join(abstracts))

        if self.include_active_messages:
            active_messages = [
                _format_session_message(message)
                for message in session_context.get("messages") or []
            ]
            active_messages = [message for message in active_messages if message]
            if active_messages:
                sections.append("Active session messages:\n" + "\n".join(active_messages))

        if recall_documents:
            recall_lines = []
            for index, doc in enumerate(recall_documents, start=1):
                metadata = getattr(doc, "metadata", {}) or {}
                uri = metadata.get("openviking_uri") or metadata.get("source") or ""
                recall_lines.append(f"[{index}] {uri}\n{doc.page_content}".strip())
            sections.append(self.recall_header + "\n\n" + "\n\n".join(recall_lines))

        if not sections:
            return ""
        return f"{OPENVIKING_CONTEXT_MARKER}\n" + "\n\n".join(sections) + "\n</openviking_context>"


class _OpenVikingContextResources:
    """Share one lifecycle owner across derived runnable copies."""

    def __init__(
        self,
        assembler: OpenVikingSessionContextAssembler,
        recorder: OpenVikingSessionRecorder,
    ) -> None:
        self._assembler = assembler
        self._recorder = recorder
        self._closed = False
        self._close_lock = threading.Lock()

    def __copy__(self) -> _OpenVikingContextResources:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> _OpenVikingContextResources:
        memo[id(self)] = self
        return self

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError(
            "OpenVikingContextRunnable.close() cannot run inside an active event loop; "
            "use `await runnable.aclose()`"
        )

    async def aclose(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        first_error: BaseException | None = None
        for component in (self._assembler, self._recorder):
            try:
                await component.aclose()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def raise_if_closed(self) -> None:
        with self._close_lock:
            if self._closed:
                raise RuntimeError("OpenVikingContextRunnable is closed")


class OpenVikingContextRunnable(RunnableWithMessageHistory):
    """LangChain history wrapper with deterministic OpenViking cleanup."""

    _resources: _OpenVikingContextResources = PrivateAttr()

    def __init__(
        self,
        *args: Any,
        _resources: _OpenVikingContextResources,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._resources = _resources

    def close(self) -> None:
        """Release internally created resources outside a running event loop."""

        self._resources.close()

    async def aclose(self) -> None:
        """Release internally created context and recording resources."""

        await self._resources.aclose()

    def __enter__(self) -> OpenVikingContextRunnable:
        """Enter a synchronous managed lifecycle."""

        self._resources.raise_if_closed()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: Any,
    ) -> None:
        self.close()

    async def __aenter__(self) -> OpenVikingContextRunnable:
        """Enter an asynchronous managed lifecycle."""

        self._resources.raise_if_closed()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: Any,
    ) -> None:
        await self.aclose()


def with_openviking_context(
    runnable: Any,
    *,
    client: Any = None,
    async_client: Any = None,
    url: str | None = None,
    api_key: str | None = None,
    account: str | None = None,
    user: str | None = None,
    user_id: str | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
    auto_initialize: bool = True,
    session_id: str | None = None,
    peer_id: str | None = None,
    actor_peer_id: str | None = None,
    target_uri: str | list[str] = "",
    limit: int = 5,
    score_threshold: float | None = None,
    token_budget: int = 128_000,
    input_messages_key: str | None = None,
    output_messages_key: str | None = None,
    history_messages_key: str | None = None,
    commit_policy: OpenVikingCommitPolicy | None = None,
    session_id_config_key: str = "session_id",
    peer_id_config_key: str = "peer_id",
    inject_context: bool = True,
) -> OpenVikingContextRunnable:
    """Wrap a runnable with invocation-scoped history and managed resources.

    Use the returned runnable as a context manager, or call ``close()`` /
    ``await aclose()`` when it is no longer needed.
    """

    assembler = OpenVikingSessionContextAssembler(
        client=client,
        async_client=async_client,
        url=url,
        api_key=api_key,
        account=account,
        user=user,
        user_id=user_id,
        actor_peer_id=actor_peer_id,
        timeout=timeout,
        extra_headers=extra_headers,
        auto_initialize=auto_initialize,
        target_uri=target_uri,
        limit=limit,
        score_threshold=score_threshold,
        token_budget=token_budget,
        include_active_messages=False,
        include_recall=inject_context,
    )
    recorder = OpenVikingSessionRecorder(
        client=client,
        async_client=async_client,
        url=url,
        api_key=api_key,
        account=account,
        user=user,
        user_id=user_id,
        actor_peer_id=actor_peer_id,
        timeout=timeout,
        extra_headers=extra_headers,
        auto_initialize=auto_initialize,
        commit_policy=commit_policy,
    )
    resources = _OpenVikingContextResources(assembler, recorder)
    sync_write_lock_pool = _SyncSessionWriteLockPool()
    async_write_lock_pools = LoopScopedAsyncClientCache()

    def make_history(active_session_id: str) -> OpenVikingChatMessageHistory:
        resources.raise_if_closed()
        return _InvocationOpenVikingChatMessageHistory(
            session_id=active_session_id,
            recorder=recorder,
            sync_write_lock_pool=sync_write_lock_pool,
            async_write_lock_pools=async_write_lock_pools,
            peer_id=peer_id,
            token_budget=token_budget,
        )

    if session_id is None:

        def dynamic_session_history_factory(
            resolved_session_id: str,
        ) -> OpenVikingChatMessageHistory:
            return make_history(
                _validate_session_id(resolved_session_id, key=session_id_config_key)
            )

        session_history_factory: Callable[..., OpenVikingChatMessageHistory] = (
            dynamic_session_history_factory
        )
        history_factory_config = [
            ConfigurableFieldSpec(
                id=session_id_config_key,
                annotation=str,
                name="Session ID",
                description=("Required OpenViking session ID for dynamic chat history and recall."),
                default="",
                is_shared=True,
            )
        ]
    else:

        def fixed_session_history_factory() -> OpenVikingChatMessageHistory:
            return make_history(session_id)

        session_history_factory = fixed_session_history_factory
        history_factory_config = None

    def prepare_injection(
        input_value: Any,
        config: dict[str, Any] | None,
    ) -> tuple[str, _InvocationOpenVikingChatMessageHistory, str] | None:
        resolved_session_id = session_id or _session_id_from_config(
            config,
            key=session_id_config_key,
        )
        resolved_peer_id = _peer_id_from_config(
            config,
            key=peer_id_config_key,
            default=peer_id,
        )
        history = _invocation_history_from_config(config)
        history._prepare_invocation(peer_id=resolved_peer_id)
        if not inject_context:
            return None
        query = _latest_user_text_from_input(input_value, input_messages_key)
        return resolved_session_id, history, query

    def apply_injection(
        input_value: Any,
        history: _InvocationOpenVikingChatMessageHistory,
        assembled: OpenVikingAssembledContext,
    ) -> Any:
        if not assembled.block:
            return input_value
        if assembled.context_parts:
            history._prepare_invocation(
                peer_id=history.peer_id,
                context_parts=assembled.context_parts,
            )
        return _inject_system_context(input_value, assembled.block, input_messages_key)

    def inject(input_value: Any, config: dict[str, Any] | None = None) -> Any:
        prepared = prepare_injection(input_value, config)
        if prepared is None:
            return input_value
        resolved_session_id, history, query = prepared
        assembled = assembler.assemble(
            session_id=resolved_session_id,
            query=query,
        )
        return apply_injection(input_value, history, assembled)

    async def ainject(input_value: Any, config: dict[str, Any] | None = None) -> Any:
        prepared = prepare_injection(input_value, config)
        if prepared is None:
            return input_value
        resolved_session_id, history, query = prepared
        assembled = await assembler.aassemble(
            session_id=resolved_session_id,
            query=query,
        )
        return apply_injection(input_value, history, assembled)

    def clear_pending_on_error(_run: Any, config: dict[str, Any] | None = None) -> None:
        try:
            history = _invocation_history_from_config(config)
        except Exception:
            logger.debug(
                "OpenViking pending context cleanup could not resolve invocation history",
                exc_info=True,
            )
            return
        history._discard_invocation_context()

    bound = (RunnableLambda(inject, afunc=ainject) | runnable).with_listeners(
        on_error=clear_pending_on_error
    )
    wrapped = OpenVikingContextRunnable(
        bound,
        session_history_factory,
        input_messages_key=input_messages_key,
        output_messages_key=output_messages_key,
        history_messages_key=history_messages_key,
        history_factory_config=history_factory_config,
        _resources=resources,
    )
    return wrapped


def _session_id_from_config(config: dict[str, Any] | None, *, key: str) -> str:
    configurable = (config or {}).get("configurable") or {}
    return _validate_session_id(configurable.get(key), key=key)


def _peer_id_from_config(
    config: dict[str, Any] | None,
    *,
    key: str,
    default: str | None,
) -> str | None:
    configurable = (config or {}).get("configurable") or {}
    value = configurable.get(key, default)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_session_id(value: Any, *, key: str) -> str:
    session_id = str(value or "").strip()
    if not session_id:
        raise ValueError(
            "OpenViking dynamic sessions require "
            f"config={{'configurable': {{'{key}': '<session-id>'}}}}. "
            "Pass session_id='...' to with_openviking_context for no-config usage."
        )
    return session_id


def _invocation_history_from_config(
    config: dict[str, Any] | None,
) -> _InvocationOpenVikingChatMessageHistory:
    configurable = (config or {}).get("configurable") or {}
    history = configurable.get("message_history")
    if not isinstance(history, _InvocationOpenVikingChatMessageHistory):
        raise RuntimeError("OpenViking runnable invocation history is unavailable")
    return history


def _retriever_for_session(
    retriever: Any,
    session_id: str,
) -> Any:
    if hasattr(retriever, "model_copy"):
        update = {
            "session_id": session_id,
            "search_mode": "search",
        }
        return retriever.model_copy(update=update)
    return retriever


def _latest_user_text_from_input(input_value: Any, input_messages_key: str | None) -> str:
    messages = _input_messages(input_value, input_messages_key)
    if messages:
        return get_latest_user_text(messages)
    if isinstance(input_value, str):
        return input_value
    if isinstance(input_value, dict):
        for value in input_value.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _input_messages(input_value: Any, input_messages_key: str | None) -> list[Any]:
    if isinstance(input_value, list):
        return list(input_value)
    if isinstance(input_value, dict):
        key = input_messages_key or "messages"
        value = input_value.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def _inject_system_context(
    input_value: Any,
    context_block: str,
    input_messages_key: str | None,
) -> Any:
    if isinstance(input_value, list):
        return _merge_system_message(input_value, context_block)
    if isinstance(input_value, dict):
        key = input_messages_key or "messages"
        if isinstance(input_value.get(key), list):
            updated = dict(input_value)
            updated[key] = _merge_system_message(input_value[key], context_block)
            return updated
        updated = dict(input_value)
        updated["openviking_context"] = context_block
        return updated
    return input_value


def _merge_system_message(messages: Sequence[Any], context_block: str) -> list[Any]:
    updated = list(messages)
    for index, message in enumerate(updated):
        if isinstance(message, SystemMessage):
            content = extract_message_text(message.content)
            updated[index] = SystemMessage(content=f"{content}\n\n{context_block}".strip())
            return updated
    return [SystemMessage(content=context_block), *updated]


def _format_session_message(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "assistant")
    parts = message.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        part_type = part.get("type")
        if part_type == "text" and part.get("text"):
            chunks.append(str(part["text"]))
        elif part_type == "context" and part.get("abstract"):
            chunks.append(f"[context] {part['abstract']}")
        elif part_type == "tool":
            tool_name = part.get("tool_name") or "tool"
            status = part.get("tool_status") or "completed"
            output = part.get("tool_output") or ""
            chunks.append(f"[tool:{tool_name} ({status})] {output}".strip())
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    return f"[{role}] {text}" if text else ""
