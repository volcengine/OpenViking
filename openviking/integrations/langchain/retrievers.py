# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain retriever backed by OpenViking retrieval."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import ConfigDict, Field, PrivateAttr

try:
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from openviking.integrations.langchain.client import (
    OpenVikingConnection,
    call_openviking,
    ensure_client,
    item_value,
    iter_result_items,
    stringify,
)


class OpenVikingRetriever(BaseRetriever):
    """Retrieve LangChain ``Document`` objects from OpenViking contexts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

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

    _client_cache: Any = PrivateAttr(default=None)

    def _get_client(self) -> Any:
        if self._client_cache is None:
            self._client_cache = ensure_client(
                OpenVikingConnection(
                    client=self.client,
                    url=self.url,
                    api_key=self.api_key,
                    account=self.account,
                    user=self.user,
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                    path=self.path,
                    timeout=self.timeout,
                    extra_headers=self.extra_headers,
                    auto_initialize=self.auto_initialize,
                )
            )
        return self._client_cache

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
        )
        documents: list[Document] = []
        for context_type, item in iter_result_items(result, self.context_types):
            uri = item_value(item, "uri", "")
            content = self._content_for_item(client, item)
            metadata = {
                "source": uri,
                f"{self.metadata_prefix}_uri": uri,
                f"{self.metadata_prefix}_context_type": context_type,
                f"{self.metadata_prefix}_level": item_value(item, "level"),
                f"{self.metadata_prefix}_category": item_value(item, "category"),
                f"{self.metadata_prefix}_score": item_value(item, "score"),
                f"{self.metadata_prefix}_match_reason": item_value(item, "match_reason"),
                f"{self.metadata_prefix}_abstract": item_value(item, "abstract"),
                f"{self.metadata_prefix}_overview": item_value(item, "overview"),
            }
            documents.append(Document(page_content=content, metadata=metadata))
        return documents

    async def _aget_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        return await asyncio.to_thread(
            lambda: self._get_relevant_documents(query, run_manager=run_manager)
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

    def _read_or_fallback(self, client: Any, uri: str, fallback: Any) -> str:
        if uri:
            try:
                content = call_openviking(client, "read", uri=uri)
                return stringify(content, max_chars=self.max_content_chars)
            except Exception:
                pass
        return stringify(fallback, max_chars=self.max_content_chars)
