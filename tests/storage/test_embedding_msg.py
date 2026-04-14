# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from uuid import uuid4

from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.telemetry.request_wait_tracker import RequestWaitTracker


def test_embedding_msg_roundtrip_preserves_id_for_request_wait_tracker():
    telemetry_id = f"tm_{uuid4().hex}"
    tracker = RequestWaitTracker.get_instance()
    tracker.register_request(telemetry_id)

    try:
        msg = EmbeddingMsg(
            "hello",
            {"uri": "viking://agent/skills/demo"},
            telemetry_id=telemetry_id,
        )
        tracker.register_embedding_root(telemetry_id, msg.id)

        restored = EmbeddingMsg.from_dict(msg.to_dict())

        assert restored.id == msg.id
        tracker.mark_embedding_done(telemetry_id, restored.id)
        assert tracker.is_complete(telemetry_id)
    finally:
        tracker.cleanup(telemetry_id)
