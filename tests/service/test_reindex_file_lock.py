# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for reindexing single-file URIs under tree locks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.service import reindex_executor as reindex_module
from openviking.service.reindex_executor import ReindexExecutor
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx


async def test_reindex_single_file_uri_acquires_tree_lock_without_not_a_directory(
    binding_fs, monkeypatch, caplog
):
    uri = "viking://resources/profile.md"
    viking_fs = binding_fs
    ctx = root_ctx()
    await viking_fs.write_file(uri, "# Profile\nSingle file reindex source.\n", ctx=ctx)

    service = SimpleNamespace(
        viking_fs=viking_fs,
        vikingdb_manager=SimpleNamespace(has_queue_manager=True),
    )
    monkeypatch.setattr(reindex_module, "get_service", lambda: service)
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: viking_fs)

    from openviking_cli.utils.config.embedding_config import EmbeddingConfig

    executor = ReindexExecutor(
        vector_config_resolver=SimpleNamespace(
            resolve=AsyncMock(return_value=SimpleNamespace(embedding=EmbeddingConfig()))
        )
    )
    executor._fetch_existing_record = AsyncMock(return_value=None)
    executor._upsert_context = AsyncMock()
    result = await executor._run(
        uri=uri,
        object_type="resource",
        mode="vectors_only",
        ctx=ctx,
    )

    assert result["status"] == "completed"
    assert result["uri"] == uri
    assert result["scanned_records"] == 1
    assert result["rebuilt_records"] == 1
    assert "Not a directory" not in caplog.text
    assert "Failed to create lock file" not in caplog.text
