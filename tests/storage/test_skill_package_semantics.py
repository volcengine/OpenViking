import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_dag import DagStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry import get_current_telemetry, register_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker


@pytest.mark.asyncio
async def test_skill_worker_keeps_package_locked_until_embeddings_finish(monkeypatch):
    root = "viking://agent/skills/demo"
    emitted = asyncio.Event()
    locked = True
    events = []
    tracker = get_request_wait_tracker()
    telemetry = get_current_telemetry()
    register_telemetry(telemetry)
    tracker.register_request(telemetry.telemetry_id)
    msg = SemanticMsg(
        uri=root,
        context_type="skill",
        telemetry_id=telemetry.telemetry_id,
        generation_trigger="skill_ingest",
        propagate_to_parent=False,
    )
    tracker.register_semantic_root(msg.telemetry_id, msg.id)

    class Lease:
        lock = {"lease_ref": "package"}

        async def close(self):
            nonlocal locked
            locked = False
            events.append("released")

    class Executor:
        def __init__(self, **kwargs):
            self.stale = False

        async def run(self, uri):
            tracker.register_embedding_root(msg.telemetry_id, "file-embedding")
            emitted.set()

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor", Executor
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=Lease()),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: SimpleNamespace(exists=AsyncMock(return_value=True)),
    )
    worker = asyncio.create_task(SemanticProcessor().on_dequeue({"data": msg.to_json()}))
    await asyncio.wait_for(emitted.wait(), 1)
    assert locked and not worker.done() and not tracker.is_complete(msg.telemetry_id)
    # The HTTP timeout cleanup must not release the package while embedding runs.
    tracker.cleanup(msg.telemetry_id)
    await asyncio.sleep(0.06)
    assert locked and not worker.done() and not tracker.is_complete(msg.telemetry_id)
    # Deletion cannot pass the package lease while a file vector can still write.
    events.append("embedding-written")
    tracker.mark_embedding_done(msg.telemetry_id, "file-embedding", vector_written=True)
    await asyncio.wait_for(worker, 1)
    assert not locked and tracker.is_complete(msg.telemetry_id)
    assert events == ["embedding-written", "released"]
    tracker.cleanup(msg.telemetry_id)
