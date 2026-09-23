# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory semantic queue stall fix (issue #864).

Ensures that _process_memory_directory() error paths propagate exceptions
so that on_dequeue() returns an explicit processing outcome.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.circuit_breaker import CircuitBreakerOpen


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
    processor = SemanticProcessor()
    processor._circuit_breaker = SimpleNamespace(
        check=MagicMock(side_effect=CircuitBreakerOpen), retry_after=0
    )
    entered, release = asyncio.Event(), asyncio.Event()
    written = []

    async def enqueue(msg):
        entered.set()
        await release.wait()
        written.append(msg.id)

    queue = SimpleNamespace(enqueue=enqueue)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda _: queue),
    )
    msg = _make_msg(context_type=context_type, telemetry_id=str(uuid4()))
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
        get_request_wait_tracker().cleanup(msg.telemetry_id)


@pytest.mark.asyncio
async def test_root_semantic_message_is_acknowledged_without_processing():
    processor = SemanticProcessor()

    result = await processor.on_dequeue(
        _build_data(_make_msg(uri="viking://", context_type="resource"))
    )

    assert result.outcome is ProcessOutcome.SUCCESS
    assert result.value is None
    assert result.error is None


@pytest.mark.asyncio
async def test_memory_empty_dir_still_returns_success():
    """An empty memory directory must return a successful processing result."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(return_value=[])

    msg = _make_msg()
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.SUCCESS
    assert result.value is None
    assert result.error is None


@pytest.mark.asyncio
async def test_memory_ls_error_returns_failed():
    """A permanent filesystem error must return a failed processing result.

    Uses a real classify_api_error (no mock): FileNotFoundError is classified
    as permanent by the real classifier, so the processor returns FAILED.
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(side_effect=FileNotFoundError("/memories not found"))

    msg = _make_msg()
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.value is None
    assert result.error == (f"Failed to list memory directory {msg.uri}: /memories not found")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context_type", "failure_stage", "error_message", "outcome"),
    [
        ("memory", "listing", "500 Internal Server Error", ProcessOutcome.REQUEUED),
        ("resource", "summary", "500 model unavailable", ProcessOutcome.REQUEUED),
        ("memory", "summary", "400 invalid model", ProcessOutcome.FAILED),
        ("resource", "overview", "400 invalid model", ProcessOutcome.FAILED),
        ("memory", "overview", "500 model unavailable", ProcessOutcome.REQUEUED),
        ("skill", "summary", "400 invalid model", ProcessOutcome.FAILED),
    ],
)
async def test_semantic_errors_reach_queue_outcome(
    monkeypatch, context_type, failure_stage, error_message, outcome
):
    """Real generation errors reach retry/failure accounting without publishing placeholders."""
    processor = SemanticProcessor()
    error = RuntimeError(error_message)

    async def read_file(uri, ctx=None):
        if uri.endswith(("/.overview.md", "/.abstract.md")):
            raise FileNotFoundError(uri)
        return "Document content"

    fake_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        ls=AsyncMock(return_value=[{"name": "file1.md", "isDir": False}]),
        read_file=AsyncMock(side_effect=read_file),
        write_file=AsyncMock(),
        _uri_to_path=lambda uri, ctx=None: uri.replace("viking://", "/local/acc1/"),
        _async_agfs=SimpleNamespace(
            pathlock_acquire_exact_batch=AsyncMock(return_value={"lease_ref": "test"}),
            pathlock_release=AsyncMock(),
        ),
    )
    if failure_stage == "listing":
        fake_fs.ls.side_effect = error
    if context_type == "skill" or failure_stage == "summary" and context_type == "memory":
        fake_fs.ls.return_value.append({"name": "file2.md", "isDir": False})
    if context_type == "skill":
        monkeypatch.setattr(
            processor,
            "_resolve_skill_semantic_lock",
            AsyncMock(return_value=SimpleNamespace(lock={}, close=AsyncMock())),
        )
        monkeypatch.setattr(
            processor,
            "_skill_root_semantics",
            AsyncMock(return_value=("# Skill definition", "Skill definition")),
        )

    sibling_started, sibling_stopped = asyncio.Event(), asyncio.Event()

    async def complete(prompt):
        name, filename = prompt
        if failure_stage == "summary" and context_type == "memory":
            if filename == "file2.md":
                sibling_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    sibling_stopped.set()
            await sibling_started.wait()
        if (failure_stage == "summary" and name == "semantic.document_summary") or (
            failure_stage == "overview" and name == "semantic.overview_generation"
        ):
            raise error
        return "# Document\n\nUseful summary."

    config = SimpleNamespace(
        vlm=SimpleNamespace(is_available=lambda: True, get_completion_async=complete),
        semantic=SimpleNamespace(
            max_file_content_chars=10000,
            max_overview_prompt_chars=10000,
            overview_batch_size=32,
            overview_sample_limit=32,
            overview_max_chars=10000,
            abstract_max_chars=256,
        ),
        output_language_override="en",
    )
    for module in ("semantic_processor", "semantic_executor"):
        monkeypatch.setattr(f"openviking.storage.queuefs.{module}.get_viking_fs", lambda: fake_fs)
        monkeypatch.setattr(
            f"openviking.storage.queuefs.{module}.get_openviking_config", lambda: config
        )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.render_prompt",
        lambda name, values: (name, values.get("file_name")),
    )
    reenqueue = AsyncMock()
    monkeypatch.setattr(processor, "_reenqueue_semantic_msg", reenqueue)
    msg = _make_msg(
        uri={
            "resource": "viking://resources/docs",
            "memory": "viking://user/usr1/memories",
            "skill": "viking://user/usr1/skills/demo",
        }[context_type],
        context_type=context_type,
        telemetry_id=str(uuid4()),
        skip_vectorization=True,
        propagate_to_parent=False,
    )
    tracker = get_request_wait_tracker()
    tracker.register_request(msg.telemetry_id)
    tracker.register_semantic_root(msg.telemetry_id, msg.id)
    try:
        result = await asyncio.wait_for(processor.on_dequeue(msg.to_dict()), timeout=2)
        assert result.outcome is outcome
        if failure_stage == "summary" and context_type == "memory":
            assert sibling_stopped.is_set()
        status = tracker.build_queue_status(msg.telemetry_id)["Semantic"]
        assert status["processed"] == 0
        expected_errors = 2 if context_type == "skill" else int(outcome is ProcessOutcome.FAILED)
        assert status["error_count"] == expected_errors
        assert status["requeue_count"] == int(outcome is ProcessOutcome.REQUEUED)
        if outcome is ProcessOutcome.FAILED:
            if context_type == "skill":
                for filename in ("file1.md", "file2.md"):
                    assert f"{msg.uri}/{filename}: {error_message}" in result.error
            else:
                assert result.error == error_message
            assert tracker.is_complete(msg.telemetry_id)
            reenqueue.assert_not_awaited()
        else:
            assert result.error is None
            assert not tracker.is_complete(msg.telemetry_id)
            reenqueue.assert_awaited_once()
        fake_fs.write_file.assert_not_awaited()
    finally:
        tracker.cleanup(msg.telemetry_id)


@pytest.mark.asyncio
async def test_memory_write_error_returns_failed():
    """An abstract/overview PermissionError must return a failed processing result.

    Exercises the write failure path with real classify_api_error: PermissionError
    is classified as permanent, so the processor returns FAILED.
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(return_value=[{"name": "file1.md", "isDir": False}])
    fake_fs.read_file = AsyncMock(return_value="some content")
    fake_fs.write_file = AsyncMock(side_effect=PermissionError("Permission denied"))
    fake_fs._async_agfs.pathlock_acquire_exact_batch = AsyncMock(return_value={"lease_ref": "test"})
    fake_fs._async_agfs.pathlock_release = AsyncMock()
    fake_fs._uri_to_path = MagicMock(
        side_effect=lambda uri, ctx=None: f"/local/acc1/{uri.removeprefix('viking://')}"
    )

    msg = _make_msg(skip_vectorization=True)
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=SimpleNamespace(
                semantic=SimpleNamespace(
                    overview_max_chars=100_000,
                    abstract_max_chars=10_000,
                )
            ),
        ),
        patch.object(
            processor,
            "_generate_single_file_summary",
            new=AsyncMock(return_value={"name": "file1.md", "summary": "test summary"}),
        ),
        patch.object(
            processor,
            "_generate_overview",
            new=AsyncMock(return_value="# Overview\ntest overview"),
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.value is None
    assert result.error == (f"Failed to write abstract/overview for {msg.uri}: Permission denied")
