# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Service lifecycle coverage for model clients."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.config.embedding import AccountEmbeddingProvider
from openviking.config.vector import VectorRuntimeSettings
from openviking.service.core import OpenVikingService
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.mark.asyncio
async def test_service_waits_for_account_embedder_before_closing_storage(monkeypatch) -> None:
    events = []
    started, storage_closing = asyncio.Event(), asyncio.Event()

    class _ResourceService:
        async def close_background_tasks(self) -> None:
            events.append("background")

    class _QueueManager:
        def stop(self) -> None:
            events.append("queue")

    class _VikingDBManager:
        def mark_closing(self) -> None:
            events.append("storage-mark")
            storage_closing.set()

        async def close(self) -> None:
            events.append("storage-close")

    class _Embedder:
        def prepare_embedding_input(self, content):
            return content

        async def embed_async(self, content, *, is_query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("request-end")

        def close(self) -> None:
            events.append("embedder")

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda _: _Embedder())
    settings = VectorRuntimeSettings(
        embedding=EmbeddingConfig(
            dense={"provider": "openai", "model": "test", "api_key": "key", "dimension": 1}
        ),
        vectordb=VectorDBBackendConfig(dimension=1),
        embedding_profile="test",
        dedicated_vectordb=False,
    )
    provider = AccountEmbeddingProvider(
        SimpleNamespace(resolve=AsyncMock(return_value=settings)),
        SimpleNamespace(add_update_consumer=lambda **_: None),
    )
    service = OpenVikingService.__new__(OpenVikingService)
    service._config = SimpleNamespace(vlm=SimpleNamespace(close=lambda: events.append("vlm")))
    service._runtime_config_manager = None
    service._vlm_resolver = SimpleNamespace(close=lambda: events.append("resolver"))
    service._resource_service = _ResourceService()
    service._watch_scheduler = None
    service._session_auto_commit_scheduler = None
    service._queue_manager = _QueueManager()
    service._vikingdb_manager = _VikingDBManager()
    service._agfs_client = None
    service._embedding_provider = provider
    service._embedder = None
    service._initialized = True
    monkeypatch.setattr(service, "_release_data_dir_lock", lambda: None)

    pending = asyncio.create_task(provider.embed("account-a", "query", is_query=True))
    await started.wait()
    closing = asyncio.create_task(service.close())
    try:
        await storage_closing.wait()
        assert not closing.done()
        assert "embedder" not in events
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await closing
    finally:
        pending.cancel()
        await asyncio.gather(pending, closing, return_exceptions=True)

    assert events == [
        "background",
        "queue",
        "resolver",
        "vlm",
        "storage-mark",
        "request-end",
        "embedder",
        "storage-close",
    ]
    assert service._embedding_provider is None
