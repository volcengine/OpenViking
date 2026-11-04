# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""The semantic queue must stop requeueing a message that keeps failing.

A persistently failing model endpoint (for example a local model that cannot
finish a summary within the VLM timeout) must not loop the queue forever:
after ``max_failure_requeues`` consecutive failures the message is
dead-lettered with a terminal ``ProcessResult.failed`` instead of being
re-enqueued again.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import (
    SemanticProcessor,
    HandoffRecoveryBlocked,
)


def _processor(**kwargs):
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver, **kwargs)


def _msg(**kwargs):
    return SemanticMsg(
        uri=f"viking://resources/example/{uuid4().hex[:8]}.md",
        context_type="resource",
        account_id="acc",
        user_id="u1",
        peer_id="p1",
        **kwargs,
    )


def _processor_with_mocks(**kwargs):
    processor = _processor(**kwargs)
    processor._merge_request_stats = MagicMock()
    processor._cleanup_local_artifact = AsyncMock()
    return processor


def _work():
    return SimpleNamespace(reenqueue_after_error=AsyncMock())


@pytest.mark.asyncio
async def test_requeue_after_error_increments_failure_count():
    processor = _processor_with_mocks()
    msg = _msg()
    work = _work()

    result = await processor._requeue_semantic_msg_after_error(
        msg, RuntimeError("transient model error"), work=work
    )

    assert result.outcome is ProcessOutcome.REQUEUED
    work.reenqueue_after_error.assert_awaited_once()
    assert msg.failure_requeue_count == 1


@pytest.mark.asyncio
async def test_requeue_dead_letters_at_failure_limit():
    processor = _processor_with_mocks(max_failure_requeues=3)
    msg = _msg(failure_requeue_count=3)
    work = _work()

    result = await processor._requeue_semantic_msg_after_error(
        msg, RuntimeError("persistently failing model"), work=work
    )

    assert result.outcome is ProcessOutcome.FAILED
    work.reenqueue_after_error.assert_not_awaited()
    # Counter is not consumed by the dead-letter decision itself.
    assert msg.failure_requeue_count == 3
    assert "requeue limit" in result.error
    processor._cleanup_local_artifact.assert_awaited_once_with(msg)
    processor._merge_request_stats.assert_called_once_with(
        msg.telemetry_id, error_count=1
    )


@pytest.mark.asyncio
async def test_failure_limit_helper_increments_below_limit():
    processor = _processor_with_mocks(max_failure_requeues=5)
    msg = _msg()

    assert await processor._requeue_within_failure_limit(msg, reason="boom") is None
    assert msg.failure_requeue_count == 1
    assert await processor._requeue_within_failure_limit(msg, reason="boom") is None
    assert msg.failure_requeue_count == 2


@pytest.mark.asyncio
async def test_failure_limit_helper_dead_letters_at_limit():
    processor = _processor_with_mocks(max_failure_requeues=2)
    msg = _msg(failure_requeue_count=2)

    result = await processor._requeue_within_failure_limit(msg, reason="circuit breaker open")

    assert result is not None
    assert result.outcome is ProcessOutcome.FAILED
    assert "circuit breaker open" in result.error
    assert msg.failure_requeue_count == 2
    processor._cleanup_local_artifact.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_handoff_retry_does_not_consume_failure_limit():
    processor = _processor_with_mocks(max_failure_requeues=3)
    msg = _msg(failure_requeue_count=3)
    work = _work()
    error = HandoffRecoveryBlocked("lock handoff lost")

    with patch(
        "openviking.storage.queuefs.semantic_processor.prepare_handoff_retry",
        new_callable=AsyncMock,
        return_value={"lock_handoff_retry": "payload"},
    ):
        result = await processor._requeue_semantic_msg_after_error(
            msg, error, work=work
        )

    # The bounded path-lock recovery path requeues without touching the
    # consecutive-failure counter.
    assert result.outcome is ProcessOutcome.REQUEUED
    work.reenqueue_after_error.assert_awaited_once()
    assert msg.failure_requeue_count == 3
