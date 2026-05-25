from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.transaction import NO_LOCK


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        return True


class _FakeDagExecutor:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stale = False
        _FakeDagExecutor.calls.append(kwargs)

    async def run(self, root_uri):
        self.root_uri = root_uri

    def get_stats(self):
        from openviking.storage.queuefs.semantic_dag import DagStats

        return DagStats()


@pytest.mark.asyncio
async def test_target_preexisting_controls_incremental_detection(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=NO_LOCK, close=AsyncMock())),
    )

    for target_preexisting, expected_incremental in [(False, False), (True, True)]:
        _FakeDagExecutor.calls = []
        processor = SemanticProcessor()
        processor._enqueue_parent_refresh = AsyncMock()
        msg = SemanticMsg(
            uri="viking://temp/import_root/repository",
            target_uri="viking://resources/org/repo",
            context_type="resource",
            target_preexisting=target_preexisting,
        )

        await processor.on_dequeue(msg.to_dict())

        assert _FakeDagExecutor.calls[0]["incremental_update"] is expected_incremental
        assert _FakeDagExecutor.calls[0]["sync_to_target"] is True
