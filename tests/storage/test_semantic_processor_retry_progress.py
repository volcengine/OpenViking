# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs import semantic_processor as module
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_msg import SemanticMsg


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("temporary failure"), LockAcquisitionError("busy")])
async def test_dequeue_requeues_serialized_progress_with_new_process_result(monkeypatch, error):
    processor = module.SemanticProcessor()
    processor._circuit_breaker = MagicMock()
    progress = {
        "viking://resources/demo/a.txt": {
            "version": 1,
            "content_hash": "hash-a",
            "summary": "summary-a",
            "summary_status": "succeeded",
            "vector_status": "succeeded",
        }
    }
    executor = SimpleNamespace(run=AsyncMock(side_effect=error), retry_progress=progress)
    factory = MagicMock(return_value=executor)
    monkeypatch.setattr(module, "SemanticDagExecutor", factory)
    monkeypatch.setattr(
        module, "get_viking_fs", lambda: SimpleNamespace(exists=AsyncMock(return_value=True))
    )
    lock = SimpleNamespace(lock=None, close=AsyncMock())
    monkeypatch.setattr(module.SemanticLockScope, "resolve", AsyncMock(return_value=lock))
    queued = []

    async def enqueue(msg):
        queued.append(SemanticMsg.from_dict(msg.to_dict()))

    monkeypatch.setattr(processor, "_reenqueue_semantic_msg", enqueue)
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        changes={"modified": list(progress)},
    )
    result = await processor.on_dequeue(msg.to_dict())

    assert result.outcome is ProcessOutcome.REQUEUED
    assert len(queued) == 1
    assert queued[0].retry_progress == progress
    lock.close.assert_awaited_once()

    await processor.on_dequeue(queued[0].to_dict())
    assert factory.call_args.kwargs["retry_progress"] == progress
