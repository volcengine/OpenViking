# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg


def test_embedding_msg_roundtrip_preserves_id():
    """EmbeddingMsg.id must survive to_dict() → from_dict() round-trip."""
    msg = EmbeddingMsg(
        message="hello",
        context_data={"collection_id": "c1", "uri": "u1"},
    )
    assert msg.id, "id should be non-empty"
    restored = EmbeddingMsg.from_dict(msg.to_dict())
    assert msg.id == restored.id, f"ID lost in round-trip: {msg.id!r} → {restored.id!r}"


def test_embedding_msg_id_in_to_dict():
    """to_dict() must include the id field."""
    msg = EmbeddingMsg(message="test", context_data={"uri": "u2"})
    d = msg.to_dict()
    assert "id" in d, "id key missing from to_dict() output"
    assert d["id"] == msg.id


def test_embedding_msg_backward_compat_missing_id():
    """from_dict() must generate a fresh id when the data has no 'id' key (backward compat)."""
    data = {
        "message": "hello",
        "context_data": {"uri": "u3"},
        "media_uri": None,
        "media_mime_type": None,
    }
    obj = EmbeddingMsg.from_dict(data)
    assert obj.id, "id should be non-empty even when missing from source dict"
