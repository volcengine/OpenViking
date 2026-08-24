# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.queuefs.embedding_msg import EmbeddingMsg


def test_embedding_msg_roundtrip_preserves_id_for_queue_work_identity():
    msg = EmbeddingMsg(
        "hello",
        {"uri": "viking://user/default/skills/demo"},
        telemetry_id="tm_roundtrip",
    )

    restored = EmbeddingMsg.from_dict(msg.to_dict())

    assert restored.id == msg.id
