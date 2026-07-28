# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

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


async def test_wait_for_request_timeout_keeps_existing_error():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_wait_timeout"

    tracker.register_request(telemetry_id)
    tracker.register_embedding_root(telemetry_id, "embedding-1")

    with pytest.raises(TimeoutError, match="Request processing not complete after 0.01s"):
        await tracker.wait_for_request(telemetry_id, timeout=0.01, poll_interval=0.001)


def test_leaf_indexed_requires_registered_roots_and_all_leaf_callbacks():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_leaf_indexed"

    tracker.register_request(telemetry_id)
    assert tracker.is_leaf_indexed(telemetry_id) is False

    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.register_semantic_root(telemetry_id, "semantic-2")
    assert tracker.is_leaf_indexed(telemetry_id) is False

    tracker.mark_semantic_leaf_done(telemetry_id, "semantic-1")
    assert tracker.is_leaf_indexed(telemetry_id) is False

    tracker.mark_semantic_leaf_done(telemetry_id, "semantic-2")
    assert tracker.is_leaf_indexed(telemetry_id) is True


def test_leaf_embedding_error_blocks_leaf_indexed():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_leaf_error"

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.record_embedding_error(telemetry_id, "leaf failed", is_leaf=True)
    tracker.mark_semantic_leaf_done(telemetry_id, "semantic-1")

    assert tracker.is_leaf_indexed(telemetry_id) is False
    assert tracker.build_queue_status(telemetry_id)["Embedding"]["error_count"] == 1


def test_directory_embedding_error_does_not_block_leaf_indexed():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_directory_error"

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.record_embedding_error(telemetry_id, "directory failed")
    tracker.mark_semantic_leaf_done(telemetry_id, "semantic-1")

    assert tracker.is_leaf_indexed(telemetry_id) is True
    assert tracker.build_queue_status(telemetry_id)["Embedding"]["error_count"] == 1


@pytest.mark.asyncio
async def test_wait_for_request_reports_leaf_indexed_once_before_full_completion():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_leaf_callback"
    callback_calls = []

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")

    async def on_leaf_indexed():
        callback_calls.append("leaf")

    wait_task = asyncio.create_task(
        tracker.wait_for_request(
            telemetry_id,
            timeout=1,
            poll_interval=0.001,
            on_leaf_indexed=on_leaf_indexed,
        )
    )

    tracker.mark_semantic_leaf_done(telemetry_id, "semantic-1")
    for _ in range(100):
        if callback_calls:
            break
        await asyncio.sleep(0.001)

    assert callback_calls == ["leaf"]
    assert wait_task.done() is False

    tracker.mark_semantic_done(telemetry_id, "semantic-1")
    await wait_task
    assert callback_calls == ["leaf"]
