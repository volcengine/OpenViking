# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from uuid import uuid4

import pytest

from openviking.storage.index_action import FieldPatch, IndexAction
from openviking.storage.queuefs.embedding_msg import (
    DeletePayload,
    EmbeddingMsg,
    EmbedPayload,
    NoopPayload,
    UpdateFieldsPayload,
)
from openviking.telemetry.request_wait_tracker import RequestWaitTracker


def test_embedding_msg_roundtrip_preserves_id_for_request_wait_tracker():
    telemetry_id = f"tm_{uuid4().hex}"
    tracker = RequestWaitTracker.get_instance()
    tracker.register_request(telemetry_id)

    try:
        msg = EmbeddingMsg(
            "hello",
            {"uri": "viking://user/default/skills/demo"},
            telemetry_id=telemetry_id,
        )
        tracker.register_embedding_root(telemetry_id, msg.id)

        restored = EmbeddingMsg.from_dict(msg.to_dict())

        assert restored.id == msg.id
        tracker.mark_embedding_done(telemetry_id, restored.id)
        assert tracker.is_complete(telemetry_id)
    finally:
        tracker.cleanup(telemetry_id)


def test_embedding_msg_roundtrip_preserves_queue_enqueue_time():
    msg = EmbeddingMsg(
        "hello",
        {"uri": "viking://resources/demo"},
        queue_enqueued_at=123.456,
    )

    assert EmbeddingMsg.from_dict(msg.to_dict()).queue_enqueued_at == 123.456


def test_legacy_embedding_msg_defaults_to_embed_and_upsert():
    restored = EmbeddingMsg.from_dict(
        {
            "message": "hello",
            "context_data": {"uri": "viking://resources/demo"},
        }
    )

    assert restored.action is IndexAction.UPSERT


def test_embedding_msg_accepts_noop_index_action_roundtrip():
    msg = EmbeddingMsg(None, {"uri": "viking://resources/demo"}, action=IndexAction.NONE)

    restored = EmbeddingMsg.from_json(msg.to_json())

    assert restored.action is IndexAction.NONE
    assert restored.message is None


def test_embedding_update_fields_roundtrip():
    msg = EmbeddingMsg.for_update_fields(
        record_id="record-1",
        fields={
            "md5": "new-md5",
            "updated_at": "2026-09-13T00:00:00Z",
            "search_tags": ["scope=new"],
        },
        context_data={
            "uri": "viking://resources/demo/a.py",
            "account_id": "account-a",
            "level": 2,
        },
        field_modes={"search_tags": "append"},
        initial_fields={
            "uri": "viking://resources/demo/a.py",
            "account_id": "account-a",
            "level": 2,
            "vector": [0.1, 0.2],
        },
    )

    restored = EmbeddingMsg.from_json(msg.to_json())

    assert restored.action is IndexAction.UPDATE_FIELDS
    assert restored.record_ids == ["record-1"]
    assert restored.update_fields == {
        "md5": "new-md5",
        "updated_at": "2026-09-13T00:00:00Z",
        "search_tags": ["scope=new"],
    }
    assert restored.message is None
    assert restored.field_modes == {"search_tags": "append"}
    assert restored.initial_fields["vector"] == [0.1, 0.2]


def test_embedding_delete_roundtrip():
    msg = EmbeddingMsg.for_delete(
        record_ids=["record-1", "record-2"],
        context_data={
            "uri": "viking://resources/demo",
            "account_id": "account-a",
        },
    )

    restored = EmbeddingMsg.from_json(msg.to_json())

    assert restored.action is IndexAction.DELETE
    assert restored.record_ids == ["record-1", "record-2"]
    assert restored.message is None


def test_embedding_update_fields_rejects_vector_fields():
    with pytest.raises(ValueError, match="vector"):
        EmbeddingMsg.for_update_fields(
            record_id="record-1",
            fields={"vector": [0.1, 0.2]},
            context_data={
                "uri": "viking://resources/demo/a.py",
                "account_id": "account-a",
                "level": 2,
            },
        )


def test_field_patch_roundtrip_and_resolution():
    patch = FieldPatch(
        values={"search_tags": ["scope=new"], "md5": "new-md5"},
        modes={"search_tags": "append"},
        seed_fields={
            "uri": "viking://resources/demo/a.py",
            "account_id": "account-a",
            "level": 2,
            "vector": [0.1, 0.2],
        },
    )

    restored = FieldPatch.from_dict(patch.to_dict())

    assert restored.resolve({"search_tags": ["env=old"], "md5": "old-md5"}) == {
        "search_tags": ["env=old", "scope=new"],
        "md5": "new-md5",
    }
    assert restored.seed_fields["vector"] == [0.1, 0.2]


@pytest.mark.parametrize(
    ("message", "payload_type"),
    [
        (
            EmbeddingMsg.for_embed(
                message="body",
                context_data={"uri": "viking://resources/demo/a.py"},
            ),
            EmbedPayload,
        ),
        (
            EmbeddingMsg.for_update_fields(
                record_id="record-1",
                field_patch=FieldPatch(values={"md5": "new-md5"}),
                context_data={"uri": "viking://resources/demo/a.py"},
            ),
            UpdateFieldsPayload,
        ),
        (
            EmbeddingMsg.for_delete(
                record_ids=["record-1"],
                context_data={"uri": "viking://resources/demo/a.py"},
            ),
            DeletePayload,
        ),
        (EmbeddingMsg.noop(context_data={"uri": "viking://resources/demo"}), NoopPayload),
    ],
)
def test_embedding_msg_roundtrip_uses_action_specific_payload(message, payload_type):
    serialized = message.to_dict()

    assert "payload" in serialized
    assert "message" not in serialized
    restored = EmbeddingMsg.from_dict(serialized)
    assert isinstance(restored.payload, payload_type)
    assert restored.to_dict() == serialized


def test_embed_payload_rejects_patch_for_upsert():
    with pytest.raises(ValueError, match="upsert.*field patch"):
        EmbeddingMsg.for_embed(
            message="body",
            context_data={"uri": "viking://resources/demo/a.py"},
            action=IndexAction.UPSERT,
            field_patch=FieldPatch(values={"search_tags": ["scope=new"]}),
        )


def test_action_specific_payload_rejects_mismatched_action():
    with pytest.raises(ValueError, match="delete requires DeletePayload"):
        EmbeddingMsg(
            action=IndexAction.DELETE,
            payload=EmbedPayload(
                "body",
                {"uri": "viking://resources/demo/a.py"},
            ),
        )


def test_payload_rejects_legacy_fields_in_same_message():
    with pytest.raises(ValueError, match="payload cannot be combined"):
        EmbeddingMsg.from_dict(
            {
                "action": "upsert",
                "payload": {
                    "type": "embed",
                    "message": "body",
                    "context_data": {"uri": "viking://resources/demo/a.py"},
                },
                "record_ids": ["silently-ignored-before"],
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "delete",
            "record_ids": ["record-1"],
            "context_data": {},
            "message": "must-not-be-ignored",
        },
        {
            "type": "embed",
            "message": "body",
            "context_data": {},
            "record_ids": ["must-not-be-ignored"],
        },
    ],
)
def test_action_specific_payload_rejects_cross_action_fields(payload):
    with pytest.raises(ValueError, match="payload contains unsupported fields"):
        EmbeddingMsg.from_dict(
            {
                "action": "delete" if payload["type"] == "delete" else "merge",
                "payload": payload,
            }
        )


def test_update_fields_factory_rejects_duplicate_patch_representations():
    with pytest.raises(ValueError, match="field_patch cannot be combined"):
        EmbeddingMsg.for_update_fields(
            record_id="record-1",
            context_data={"uri": "viking://resources/demo/a.py"},
            field_patch=FieldPatch({"md5": "new-md5"}),
            fields={"md5": "ignored-md5"},
        )


def test_legacy_upsert_discards_historically_ignored_fields():
    restored = EmbeddingMsg.from_dict(
        {
            "action": "upsert",
            "message": "body",
            "context_data": {"uri": "viking://resources/demo/a.py"},
            "record_ids": ["ignored-record"],
            "update_fields": {"md5": "ignored-md5"},
            "initial_fields": {"vector": [0.1, 0.2]},
        }
    )

    assert restored.action is IndexAction.UPSERT
    assert restored.record_ids == []
    assert restored.field_patch is None
    assert set(restored.to_dict()) == {
        "id",
        "telemetry_id",
        "queue_enqueued_at",
        "action",
        "payload",
    }


def test_legacy_update_fields_message_normalizes_to_field_patch_payload():
    restored = EmbeddingMsg.from_dict(
        {
            "message": None,
            "context_data": {"uri": "viking://resources/demo/a.py"},
            "action": "update_fields",
            "record_ids": ["record-1"],
            "update_fields": {"search_tags": ["scope=new"]},
            "field_modes": {"search_tags": "append"},
            "initial_fields": {"vector": [0.1, 0.2]},
        }
    )

    assert isinstance(restored.payload, UpdateFieldsPayload)
    assert restored.payload.field_patch.values == {"search_tags": ["scope=new"]}
    assert restored.payload.field_patch.modes == {"search_tags": "append"}
    assert restored.payload.field_patch.seed_fields == {"vector": [0.1, 0.2]}
