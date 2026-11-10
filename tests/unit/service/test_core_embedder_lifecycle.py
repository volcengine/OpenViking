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
    service._compile_scheduler = None
    service._ttl_adapter = None
    service._expiry_runtime = None
    service._vlm_resolver = SimpleNamespace(close=lambda: events.append("resolver"))
    service._resource_service = _ResourceService()
    service._watch_runtime = None
    service._watch_manager = None
    service._session_auto_commit_scheduler = None
    service._queue_manager = _QueueManager()
    service._vikingdb_manager = _VikingDBManager()
    service._agfs_client = None
    service._embedding_provider = provider
    service._embedder = None
    service._initialized = True
    monkeypatch.setattr(service, "_release_data_dir_lock", lambda: None)
    monkeypatch.setattr(
        "openviking.service.core.get_task_tracker", lambda: SimpleNamespace(close=AsyncMock())
    )

    pending = asyncio.create_task(provider.embed("account-a", "query", is_query=True))
    await started.wait()
    closing = asyncio.create_task(service.close())
    try:
        waiting_for_storage = asyncio.create_task(storage_closing.wait())
        try:
            await asyncio.wait((closing, waiting_for_storage), return_when=asyncio.FIRST_COMPLETED)
            if closing.done():
                await closing
        finally:
            waiting_for_storage.cancel()
            await asyncio.gather(waiting_for_storage, return_exceptions=True)
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


@pytest.mark.asyncio
async def test_abandoned_drain_failure_continues_cleanup_and_can_retry(monkeypatch, caplog):
    from unittest.mock import Mock

    events = []
    service = OpenVikingService.__new__(OpenVikingService)
    for name in (
        "_runtime_config_manager",
        "_compile_scheduler",
        "_ttl_adapter",
        "_expiry_runtime",
        "_watch_runtime",
        "_watch_manager",
        "_session_auto_commit_scheduler",
        "_queue_manager",
        "_vikingdb_manager",
        "_embedding_provider",
    ):
        setattr(service, name, None)
    service._config = SimpleNamespace(vlm=SimpleNamespace(close=lambda: None))
    service._resource_service = SimpleNamespace(close_background_tasks=AsyncMock())
    service._agfs_client = SimpleNamespace(close=lambda: events.append("ragfs"))
    service._embedder = SimpleNamespace(close=lambda: events.append("embedder"))
    service._initialized = True
    monkeypatch.setattr(service, "_release_data_dir_lock", lambda: events.append("unlock"))
    monkeypatch.setattr(service, "_stop_expiry", AsyncMock())
    monkeypatch.setattr("openviking.ttl.wiring.get_ttl_adapter", lambda: None)
    monkeypatch.setattr(
        "openviking.service.core.get_task_tracker", lambda: SimpleNamespace(close=AsyncMock())
    )
    drain = Mock(side_effect=[RuntimeError("drain failed"), None])
    monkeypatch.setattr("openviking.service.core.close_unclosed_collections", drain)
    await service.close()
    assert events == ["ragfs", "embedder"]
    assert not service._initialized
    assert service._agfs_client is None and service._embedder is None
    assert "continuing service cleanup" in caplog.text
    # Failed backend still owns storage: retry, then allow workspace reuse.
    await service.close()
    assert events == ["ragfs", "embedder", "unlock"]
    assert drain.call_count == 2
