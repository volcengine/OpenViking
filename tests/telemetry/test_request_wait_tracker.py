# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.telemetry.request_wait_tracker import RequestWaitTracker


def test_request_wait_tracker_cleanup_prevents_state_recreation():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_cleanup"

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.cleanup(telemetry_id)

    tracker.mark_semantic_done(telemetry_id, "semantic-1")
    tracker.mark_embedding_done(telemetry_id, "embedding-1")

    assert tracker.build_queue_status(telemetry_id) == {
        "Semantic": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
        "Embedding": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
    }


def test_request_wait_tracker_cleanup_prevents_root_recreation():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_late_root"

    tracker.register_request(telemetry_id)
    tracker.cleanup(telemetry_id)

    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.register_embedding_root(telemetry_id, "embedding-1")

    assert tracker.is_complete(telemetry_id) is True
    assert tracker.build_queue_status(telemetry_id) == {
        "Semantic": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
        "Embedding": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
    }


def test_request_wait_tracker_records_requeues():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_requeue"

    tracker.register_request(telemetry_id)
    tracker.record_semantic_requeue(telemetry_id)
    tracker.record_embedding_requeue(telemetry_id, delta=2)

    assert tracker.build_queue_status(telemetry_id) == {
        "Semantic": {"processed": 0, "requeue_count": 1, "error_count": 0, "errors": []},
        "Embedding": {"processed": 0, "requeue_count": 2, "error_count": 0, "errors": []},
    }


def test_request_wait_tracker_counts_contexts_indexed_by_request():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_vectors"

    tracker.register_request(telemetry_id)
    for root_id in ("embedding-1", "embedding-2", "embedding-3"):
        tracker.register_embedding_root(telemetry_id, root_id)
        tracker.mark_embedding_done(telemetry_id, root_id, vector_written=True)
    tracker.mark_embedding_done(telemetry_id, "embedding-3", vector_written=True)

    assert tracker.get_embedding_context_count(telemetry_id) == 3

    tracker.cleanup(telemetry_id)

    assert tracker.get_embedding_context_count(telemetry_id) == 0


def test_request_wait_tracker_accumulates_queue_wait_and_execution_durations():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_queue_timing"

    tracker.register_request(telemetry_id)
    tracker.record_semantic_timing(telemetry_id, queue_wait_ms=12.5, execute_ms=30.0)
    tracker.record_semantic_timing(telemetry_id, queue_wait_ms=7.5, execute_ms=10.0)
    tracker.record_embedding_timing(telemetry_id, queue_wait_ms=5.0, execute_ms=8.0)

    assert tracker.get_queue_timing(telemetry_id) == {
        "semantic": {"queue_wait_ms": 20.0, "execute_ms": 40.0},
        "embedding": {"queue_wait_ms": 5.0, "execute_ms": 8.0},
    }


async def test_wait_for_request_timeout_keeps_existing_error():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_wait_timeout"

    tracker.register_request(telemetry_id)
    tracker.register_embedding_root(telemetry_id, "embedding-1")

    with pytest.raises(TimeoutError, match="Request processing not complete after 0.01s"):
        await tracker.wait_for_request(telemetry_id, timeout=0.01, poll_interval=0.001)
