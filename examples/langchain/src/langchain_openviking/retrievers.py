# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain retriever backed by OpenViking retrieval."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal

from pydantic import ConfigDict, Field, PrivateAttr

try:
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from langchain_openviking.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from langchain_openviking._async_client_cache import LoopScopedAsyncClientCache
from langchain_openviking.client import (
    OpenVikingConnection,
    acall_openviking,
    aclose_openviking_clients,
    call_openviking,
    ensure_async_client,
    ensure_client,
    item_value,
    iter_result_items,
    stringify,
)


class _SharedSyncClientCache:
    """Share one lazily created sync client across shallow retriever copies."""

    def __init__(self) -> None:
        self._client: Any = None
        self._owned = False
        self._lock = threading.Lock()

    def __deepcopy__(self, memo: dict[int, Any]) -> _SharedSyncClientCache:
        copied = type(self)()
        memo[id(self)] = copied
        return copied

    def get(self, factory: Any, *, owned: bool) -> Any:
        with self._lock:
            if self._client is None:
                self._client = factory()
                self._owned = owned
            return self._client

    def pop_owned(self) -> Any:
        with self._lock:
            client = self._client
            owned = self._owned
            self._client = None
            self._owned = False
        return client if owned else None


class OpenVikingRetriever(BaseRetriever):
    """Retrieve LangChain ``Document`` objects from OpenViking contexts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Any = None
    async_client: Any = None
    url: str | None = None
    api_key: str | None = None
    account: str | None = None
    user: str | None = None
    user_id: str | None = None
    actor_peer_id: str | None = None
    timeout: float = 60.0
    extra_headers: dict[str, str] | None = None
    auto_initialize: bool = True

    target_uri: str | list[str] = ""
    search_mode: Literal["find", "search"] = "find"
    session_id: str | None = None
    limit: int = 10
    score_threshold: float | None = None
    filter: dict[str, Any] | None = None
    context_types: tuple[str, ...] = ("memory", "resource", "skill")
    content_mode: Literal["auto", "abstract", "overview", "read"] = "auto"
    max_content_chars: int = 12_000
    metadata_prefix: str = "openviking"
    tags: list[str] | None = Field(default=None, exclude=True)

    _sync_client_cache: _SharedSyncClientCache = PrivateAttr(default_factory=_SharedSyncClientCache)
    _async_clients: LoopScopedAsyncClientCache = PrivateAttr(
        default_factory=LoopScopedAsyncClientCache
    )
    _closed: bool = PrivateAttr(default=False)

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None = None,
    ) -> OpenVikingRetriever:
        """Copy configuration without cloning clients or cached runtime state."""

        memo = {} if memo is None else memo
        for client in (self.client, self.async_client):
            if client is not None:
                memo[id(client)] = client

        sync_client_cache = getattr(self, "_client_cache", None)
        if getattr(self, "_owns_client_cache", False) and sync_client_cache is not None:
            memo[id(sync_client_cache)] = None

        copied = super().__deepcopy__(memo)
        private_attributes = getattr(type(copied), "__private_attributes__", {})
        private_state = copied.__pydantic_private__
        if private_state is not None and "_client_cache" in private_attributes:
            private_state["_client_cache"] = None
            private_state["_owns_client_cache"] = False
        return copied

    def _get_client(self) -> Any:
        self._raise_if_closed()
        return self._sync_client_cache.get(
            lambda: ensure_client(
                OpenVikingConnection(
                    client=self.client,
                    url=self.url,
                    api_key=self.api_key,
                    account=self.account,
                    user=self.user,
                    user_id=self.user_id,
                    actor_peer_id=self.actor_peer_id,
                    timeout=self.timeout,
                    extra_headers=self.extra_headers,
                    auto_initialize=self.auto_initialize,
                )
            ),
            owned=self.client is None,
        )

    async def get_async_client(self) -> Any:
        """Return the async client interface used by this retriever.

        Injected clients are returned unchanged and remain caller-owned.
        HTTP-backed connections return an internally managed, loop-local handle
        whose method calls support recovery. Direct attributes on that handle
        are best-effort during recovery; use ``await handle.get()`` only to read
        raw properties immediately because recovery may replace that snapshot.
        """

        self._raise_if_closed()
        client = await ensure_async_client(
            OpenVikingConnection(
                client=self.client,
                async_client=self.async_client,
                url=self.url,
                api_key=self.api_key,
                account=self.account,
                user=self.user,
                user_id=self.user_id,
                actor_peer_id=self.actor_peer_id,
                timeout=self.timeout,
                extra_headers=self.extra_headers,
                auto_initialize=self.auto_initialize,
            ),
            client_cache=self._async_clients,
        )
        self._raise_if_closed()
        return client

    async def _get_async_client(self) -> Any:
        return await self.get_async_client()

    async def aclose(self) -> None:
        """Release clients created internally by this retriever."""

        if self._closed:
            return
        self._closed = True
        sync_client = self._sync_client_cache.pop_owned()
        async_clients = await asyncio.to_thread(self._async_clients.pop_all)

        await aclose_openviking_clients(
            sync_client,
            *async_clients,
        )

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError("OpenVikingRetriever is closed")

    def _get_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        client = self._get_client()
        method = "search" if self.search_mode == "search" else "find"
        result = call_openviking(
            client,
            method,
            query=query,
            target_uri=self.target_uri,
            session_id=self.session_id,
            limit=self.limit,
            score_threshold=self.score_threshold,
            filter=self.filter,
            tags=self.tags,
        )
        documents: list[Document] = []
        for context_type, item in iter_result_items(result, self.context_types):
            content = self._content_for_item(client, item)
            documents.append(self._document_from_item(context_type, item, content))
        return documents

    async def _aget_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        client = await self._get_async_client()
        method = "search" if self.search_mode == "search" else "find"
        result = await acall_openviking(
            client,
            method,
            query=query,
            target_uri=self.target_uri,
            session_id=self.session_id,
            limit=self.limit,
            score_threshold=self.score_threshold,
            filter=self.filter,
            tags=self.tags,
        )
        documents: list[Document] = []
        for context_type, item in iter_result_items(result, self.context_types):
            content = await self._acontent_for_item(client, item)
            documents.append(self._document_from_item(context_type, item, content))
        return documents

    def _document_from_item(
        self,
        context_type: str,
        item: Any,
        content: str,
    ) -> Document:
        uri = item_value(item, "uri", "")
        return Document(
            page_content=content,
            metadata={
                "source": uri,
                f"{self.metadata_prefix}_uri": uri,
                f"{self.metadata_prefix}_context_type": context_type,
                f"{self.metadata_prefix}_level": item_value(item, "level"),
                f"{self.metadata_prefix}_category": item_value(item, "category"),
                f"{self.metadata_prefix}_score": item_value(item, "score"),
                f"{self.metadata_prefix}_match_reason": item_value(item, "match_reason"),
                f"{self.metadata_prefix}_abstract": item_value(item, "abstract"),
                f"{self.metadata_prefix}_overview": item_value(item, "overview"),
            },
        )

    def _content_for_item(self, client: Any, item: Any) -> str:
        uri = item_value(item, "uri", "")
        abstract = item_value(item, "abstract", "")
        overview = item_value(item, "overview", "")
        level = item_value(item, "level")

        if self.content_mode == "abstract":
            return stringify(abstract or overview, max_chars=self.max_content_chars)
        if self.content_mode == "overview":
            return stringify(overview or abstract, max_chars=self.max_content_chars)
        if self.content_mode == "read":
            return self._read_or_fallback(client, uri, overview or abstract)
        if level == 2 and uri:
            return self._read_or_fallback(client, uri, overview or abstract)
        return stringify(overview or abstract, max_chars=self.max_content_chars)

    async def _acontent_for_item(self, client: Any, item: Any) -> str:
        uri = item_value(item, "uri", "")
        abstract = item_value(item, "abstract", "")
        overview = item_value(item, "overview", "")
        level = item_value(item, "level")

        if self.content_mode == "abstract":
            return stringify(abstract or overview, max_chars=self.max_content_chars)
        if self.content_mode == "overview":
            return stringify(overview or abstract, max_chars=self.max_content_chars)
        if self.content_mode == "read":
            return await self._aread_or_fallback(client, uri, overview or abstract)
        if level == 2 and uri:
            return await self._aread_or_fallback(client, uri, overview or abstract)
        return stringify(overview or abstract, max_chars=self.max_content_chars)

    def _read_or_fallback(self, client: Any, uri: str, fallback: Any) -> str:
        if uri:
            try:
                content = call_openviking(client, "read", uri=uri)
                return stringify(content, max_chars=self.max_content_chars)
            except Exception:
                pass
        return stringify(fallback, max_chars=self.max_content_chars)

    async def _aread_or_fallback(self, client: Any, uri: str, fallback: Any) -> str:
        if uri:
            try:
                content = await acall_openviking(client, "read", uri=uri)
                return stringify(content, max_chars=self.max_content_chars)
            except Exception:
                pass
        return stringify(fallback, max_chars=self.max_content_chars)
