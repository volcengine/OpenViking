from unittest.mock import AsyncMock

import pytest

from openviking.service.compile_graph import ArtifactGraphTooLargeError
from openviking.storage.queuefs.artifact_relations_processor import (
    ArtifactRelationsProcessor,
)
from openviking.storage.queuefs.process_result import ProcessOutcome


@pytest.mark.asyncio
async def test_permanent_graph_error_is_persisted_and_settled() -> None:
    graph = AsyncMock()
    error = ArtifactGraphTooLargeError("relations", 2, 3)
    graph.reconcile_target.side_effect = error
    processor = ArtifactRelationsProcessor(graph)

    result = await processor.on_dequeue(
        {"account_id": "default", "to": "viking://resources/wiki", "user_id": "alice"}
    )

    assert result.outcome is ProcessOutcome.FAILED
    graph.mark_failed.assert_awaited_once_with(
        "default",
        "viking://resources/wiki",
        error,
        attempts=1,
    )


@pytest.mark.asyncio
async def test_transient_graph_error_requeues_with_bounded_attempt() -> None:
    graph = AsyncMock()
    graph.reconcile_target.side_effect = RuntimeError("temporary")
    graph.enqueue_wakeup.return_value = True
    processor = ArtifactRelationsProcessor(graph)

    result = await processor.on_dequeue(
        {
            "account_id": "default",
            "to": "viking://resources/wiki",
            "user_id": "alice",
            "attempt": 1,
        }
    )

    assert result.outcome is ProcessOutcome.REQUEUED
    graph.enqueue_wakeup.assert_awaited_once_with(
        "default",
        "viking://resources/wiki",
        "alice",
        attempt=2,
    )
    graph.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_graph_error_exhaustion_is_persisted_and_settled() -> None:
    graph = AsyncMock()
    error = RuntimeError("still failing")
    graph.reconcile_target.side_effect = error
    processor = ArtifactRelationsProcessor(graph)

    result = await processor.on_dequeue(
        {
            "account_id": "default",
            "to": "viking://resources/wiki",
            "user_id": "alice",
            "attempt": 2,
        }
    )

    assert result.outcome is ProcessOutcome.FAILED
    graph.mark_failed.assert_awaited_once_with(
        "default",
        "viking://resources/wiki",
        error,
        attempts=3,
    )
