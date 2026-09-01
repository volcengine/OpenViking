# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.storage.errors import SemanticSourceMissingError
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor

SOURCE_URI = "viking://temp/default/0001/repository"
TARGET_URI = "viking://resources/demo-app"


class _MissingSourceFS:
    """Viking FS where the source tree is gone."""

    async def exists(self, uri, ctx=None):
        del ctx
        return uri != SOURCE_URI

    async def sync_tree(self, *args, **kwargs):
        raise AssertionError("sync_tree must not run when the source is gone")


class _RaceSourceFS:
    """Source exists at the pre-check but is removed inside sync_tree."""

    async def exists(self, uri, ctx=None):
        del uri, ctx
        return True

    async def sync_tree(self, *args, **kwargs):
        raise FileNotFoundError(
            f"Sync source no longer exists; refusing to sync into {TARGET_URI}: {SOURCE_URI}"
        )


def _stale_msg() -> SemanticMsg:
    return SemanticMsg(
        uri=SOURCE_URI,
        context_type="resource",
        target_uri=TARGET_URI,
        recursive=True,
    )


@pytest.mark.asyncio
async def test_sync_topdown_missing_source_raises_semantic_source_missing_error(monkeypatch):
    processor = SemanticProcessor()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _MissingSourceFS(),
    )
    with pytest.raises(SemanticSourceMissingError):
        await processor._sync_topdown_recursive(SOURCE_URI, TARGET_URI)


@pytest.mark.asyncio
async def test_sync_topdown_source_removed_during_sync_raises_semantic_source_missing_error(
    monkeypatch,
):
    processor = SemanticProcessor()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _RaceSourceFS(),
    )
    with pytest.raises(SemanticSourceMissingError):
        await processor._sync_topdown_recursive(SOURCE_URI, TARGET_URI)


@pytest.mark.asyncio
async def test_on_dequeue_missing_source_drops_message_without_tripping_circuit_breaker(
    monkeypatch,
):
    processor = SemanticProcessor()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _MissingSourceFS(),
    )
    # Stale messages must drain without pinning the queue-global breaker open,
    # even past the failure threshold that a real API outage would trip.
    for _ in range(10):
        assert await processor.on_dequeue(_stale_msg().to_dict()) is None
    processor._circuit_breaker.check()


@pytest.mark.asyncio
async def test_on_dequeue_permanent_api_error_still_trips_circuit_breaker(monkeypatch):
    processor = SemanticProcessor()
    fs = _RaceSourceFS()

    async def _sync_tree_boom(*args, **kwargs):
        raise RuntimeError('400 {"error": {"code": 400, "message": "bad request"}}')

    fs.sync_tree = _sync_tree_boom
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fs,
    )
    await processor.on_dequeue(_stale_msg().to_dict())
    with pytest.raises(Exception) as exc_info:
        processor._circuit_breaker.check()
    assert "OPEN" in str(exc_info.value)
