# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.service.core import OpenVikingService


@pytest.mark.asyncio
async def test_service_uses_query_embedder_for_vikingfs(monkeypatch):
    query_embedder = SimpleNamespace(is_sparse=False)
    document_embedder = SimpleNamespace(is_sparse=False)
    vikingdb_manager = MagicMock()
    directory_initializer = MagicMock()
    directory_initializer.initialize_account_directories = AsyncMock(return_value=1)
    directory_initializer.initialize_user_directories = AsyncMock(return_value=1)
    resource_processor = MagicMock()
    skill_processor = MagicMock()
    session_compressor = MagicMock()
    viking_fs = object()

    config = SimpleNamespace(
        default_account="default",
        default_user="default",
        default_agent="default",
        storage=SimpleNamespace(workspace="/tmp/openviking-test-workspace"),
        embedding=SimpleNamespace(
            max_concurrent=2,
            dimension=1024,
            get_query_embedder=MagicMock(return_value=query_embedder),
            get_document_embedder=MagicMock(return_value=document_embedder),
        ),
        vlm=SimpleNamespace(max_concurrent=3),
        rerank=None,
    )

    def fake_init_storage(
        self,
        storage_config,
        max_concurrent_embedding=10,
        max_concurrent_semantic=100,
    ):
        self._agfs_client = object()
        self._vikingdb_manager = vikingdb_manager
        self._queue_manager = None
        self._transaction_manager = None

    monkeypatch.setattr(
        "openviking.service.core.initialize_openviking_config",
        lambda user=None, path=None: config,
    )
    monkeypatch.setattr("openviking.service.core.OpenVikingService._init_storage", fake_init_storage)
    monkeypatch.setattr("openviking.service.core.get_openviking_config", lambda: config)
    monkeypatch.setattr(
        "openviking.utils.process_lock.acquire_data_dir_lock",
        MagicMock(),
    )
    monkeypatch.setattr(
        "openviking.service.core.init_context_collection",
        AsyncMock(return_value=True),
    )

    init_viking_fs = MagicMock(return_value=viking_fs)
    monkeypatch.setattr("openviking.service.core.init_viking_fs", init_viking_fs)
    monkeypatch.setattr(
        "openviking.service.core.DirectoryInitializer",
        MagicMock(return_value=directory_initializer),
    )
    monkeypatch.setattr(
        "openviking.service.core.ResourceProcessor",
        MagicMock(return_value=resource_processor),
    )
    monkeypatch.setattr(
        "openviking.service.core.SkillProcessor",
        MagicMock(return_value=skill_processor),
    )
    monkeypatch.setattr(
        "openviking.service.core.SessionCompressor",
        MagicMock(return_value=session_compressor),
    )

    service = OpenVikingService(path="/tmp/openviking-test-workspace")

    assert service._query_embedder is query_embedder
    assert service._document_embedder is document_embedder

    await service.initialize()

    config.embedding.get_query_embedder.assert_called_once()
    config.embedding.get_document_embedder.assert_called_once()
    assert init_viking_fs.call_args.kwargs["query_embedder"] is query_embedder
    assert service.viking_fs is viking_fs
