# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

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
