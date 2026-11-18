# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory semantic queue stall fix (issue #864).

Ensures that directory generation error paths propagate exceptions
so that on_dequeue() returns an explicit processing outcome.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_ops.directory_generation import DirectoryGeneration
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.utils.circuit_breaker import CircuitBreakerOpen


def _processor():
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver)


def _make_msg(uri="viking://user/usr1/memories", context_type="memory", **kwargs):
    """Build a minimal SemanticMsg for testing."""
    defaults = {
        "id": "test-msg-1",
        "uri": uri,
        "context_type": context_type,
        "recursive": False,
        "role": "root",
        "account_id": "acc1",
        "user_id": "usr1",
        "peer_id": "test-peer",
        "telemetry_id": "",
        "target_uri": "",
        "changes": None,
        "is_code_repo": False,
    }
    defaults.update(kwargs)
    return SemanticMsg.from_dict(defaults)


def _build_data(msg: SemanticMsg) -> dict:
    """Wrap a SemanticMsg into the dict format on_dequeue expects."""
    return msg.to_dict()


@pytest.mark.parametrize("context_type", ["resource", "memory", "skill"])
async def test_retry_cancellation_keeps_skill_wait_isolated(monkeypatch, context_type):
    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskTracker
    from tests.test_task_tracker import _FakeAgfs

    monkeypatch.setattr(
        "openviking.service.task_tracker._instance", TaskTracker(PersistentTaskStore(_FakeAgfs()))
    )
    processor = _processor()
    processor._circuit_breakers["acc1"] = SimpleNamespace(
        check=MagicMock(side_effect=CircuitBreakerOpen), retry_after=0
    )
    entered, release = asyncio.Event(), asyncio.Event()
    written = []

    async def enqueue(msg, *, deduplicate_memory=True):
        entered.set()
        await release.wait()
        written.append(msg.id)

    queue = SimpleNamespace(enqueue=enqueue)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda _: queue),
    )
    msg = _make_msg(context_type=context_type, telemetry_id=str(uuid4()))
    processor._circuit_breakers[msg.account_id] = SimpleNamespace(
        check=MagicMock(side_effect=CircuitBreakerOpen), retry_after=0
    )
    worker = asyncio.create_task(processor.on_dequeue(msg.to_dict()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        worker.cancel()
        if context_type == "skill":
            await asyncio.sleep(0)
            assert not worker.done()
            assert not written
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 1)
        assert written == ([msg.id] if context_type == "skill" else [])
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_root_semantic_message_is_acknowledged_without_processing():
    processor = _processor()

    result = await processor.on_dequeue(
        _build_data(_make_msg(uri="viking://", context_type="resource"))
    )

    assert result.outcome is ProcessOutcome.SUCCESS
    assert result.value is None
    assert result.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        FileNotFoundError("missing directory"),
        RuntimeError("storage failure"),
        PermissionError("write denied"),
    ],
)
async def test_memory_directory_completion_reflects_storage_failures(monkeypatch, failure):
    from tests.storage.test_semantic_executor_incremental import _FakeVikingFS

    processor = SemanticProcessor()
    processor._circuit_breakers["acc1"] = SimpleNamespace(
        check=MagicMock(side_effect=CircuitBreakerOpen), retry_after=0
    )
    root = "viking://user/usr1/memories/events"
    fs = _FakeVikingFS({root: []}, {})
    fs.exists = AsyncMock(return_value=True)
    if isinstance(failure, PermissionError):
        fs.write_file = AsyncMock(side_effect=failure)
    elif failure is not None:
        fs.ls = AsyncMock(side_effect=failure)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_work.resolve_telemetry", lambda *_: None
    )
    processor._generate_directory_semantics = AsyncMock(
        return_value=DirectoryGeneration(overview="Empty directory.", abstract="")
    )
    result = await processor.on_dequeue(
        _build_data(
            _make_msg(uri=root, skip_vectorization=True, generation_trigger="memory_update")
        )
    )
    assert result.outcome is (ProcessOutcome.FAILED if failure else ProcessOutcome.SUCCESS)
    if failure:
        assert str(failure) in result.error
    else:
        assert f"{root}/.overview.md" in fs._file_contents
        assert f"{root}/.abstract.md" not in fs._file_contents
