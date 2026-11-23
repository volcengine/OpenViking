# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.index_action import FieldPatch, IndexAction
from openviking.storage.queuefs.embedding_msg import (
    DeletePayload,
    EmbeddingMsg,
    EmbedPayload,
    NoopPayload,
    UpdateFieldsPayload,
)


def test_legacy_embedding_msg_without_account_id_is_rejected():
    with pytest.raises(ValueError, match="account_id"):
        EmbeddingMsg.from_dict(
            {
                "message": "hello",
                "context_data": {"uri": "viking://resources/demo"},
            }
        )


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
        values={"search_tags": ["scope=new", "memory_type=preferences"], "md5": "new-md5"},
        modes={"search_tags": "append"},
        seed_fields={
            "uri": "viking://resources/demo/a.py",
            "account_id": "account-a",
            "level": 2,
            "vector": [0.1, 0.2],
        },
    )

    restored = FieldPatch.from_dict(patch.to_dict())

    existing = {
        "context_type": "memory",
        "search_tags": ["env=old", "memory_type=events", "c++=cpp"],
        "md5": "old-md5",
    }
    assert restored.resolve(existing) == {
        "search_tags": ["memory_type=events", "env=old", "c++=cpp", "scope=new"],
        "md5": "new-md5",
    }
    assert restored.seed_fields["vector"] == [0.1, 0.2]
    # Ordinary replacement can clear user tags, but cannot create or overwrite a type.
    restored.modes["search_tags"] = "replace"
    restored.values["search_tags"] = ["memory_type=preferences"]
    assert restored.resolve(existing)["search_tags"] == ["memory_type=events"]
    assert restored.resolve({"context_type": "memory", "search_tags": []})["search_tags"] == []


_CONTEXT = {
    "uri": "viking://resources/demo/a.py",
    "account_id": "account-a",
    "level": 2,
}
_PATCH = FieldPatch(
    values={"md5": "new-md5", "search_tags": ["scope=new"]},
    modes={"search_tags": "append"},
    seed_fields={"vector": [0.1, 0.2]},
)


@pytest.mark.parametrize(
    ("message", "payload_type", "expected_payload"),
    [
        pytest.param(
            EmbeddingMsg.for_embed(message="body", context_data=_CONTEXT, source_type="repository"),
            EmbedPayload,
            {"type": "embed", "context_data": _CONTEXT, "message": "body"},
            id="upsert-with-content-and-source",
        ),
        pytest.param(
            EmbeddingMsg.for_update_fields(
                record_id="record-1", field_patch=_PATCH, context_data=_CONTEXT
            ),
            UpdateFieldsPayload,
            {
                "type": "update_fields",
                "context_data": _CONTEXT,
                "record_id": "record-1",
                "field_patch": {
                    "values": {"md5": "new-md5", "search_tags": ["scope=new"]},
                    "modes": {"search_tags": "append"},
                    "seed_fields": {"vector": [0.1, 0.2]},
                },
            },
            id="partial-update-with-modes-and-seed",
        ),
        pytest.param(
            EmbeddingMsg.for_delete(record_ids=["record-1", "record-2"], context_data=_CONTEXT),
            DeletePayload,
            {"type": "delete", "context_data": _CONTEXT, "record_ids": ["record-1", "record-2"]},
            id="batch-delete-without-model-input",
        ),
        pytest.param(
            EmbeddingMsg.noop(context_data=_CONTEXT),
            NoopPayload,
            {"type": "noop", "context_data": _CONTEXT},
            id="noop-without-model-input",
        ),
    ],
)
def test_embedding_msg_roundtrip_uses_action_specific_payload(
    message, payload_type, expected_payload
):
    message.telemetry_id = "telemetry-1"
    message.task_id = "task-1"
    message._task_work_id = "work-1"
    message.queue_enqueued_at = 123.456
    message.failure_requeue_count = 1

    restored = EmbeddingMsg.from_json(message.to_json())
    serialized = restored.to_dict()

    assert (restored.id, restored.action) == (message.id, message.action)
    assert (restored.telemetry_id, restored.task_id, restored._task_work_id) == (
        "telemetry-1",
        "task-1",
        "work-1",
    )
    assert restored.queue_enqueued_at == 123.456
    assert restored.failure_requeue_count == 1
    assert isinstance(restored.payload, payload_type)
    assert serialized["payload"] == expected_payload
    assert restored.source_type == ("repository" if payload_type is EmbedPayload else "")
    assert "message" not in serialized
    assert "record_ids" not in serialized


def test_embed_payload_rejects_patch_for_upsert():
    with pytest.raises(ValueError, match="upsert.*field patch"):
        EmbeddingMsg.for_embed(
            message="body",
            context_data={"uri": "viking://resources/demo/a.py", "account_id": "default"},
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
            context_data={"uri": "viking://resources/demo/a.py", "account_id": "default"},
            field_patch=FieldPatch({"md5": "new-md5"}),
            fields={"md5": "ignored-md5"},
        )


def test_legacy_upsert_discards_historically_ignored_fields():
    restored = EmbeddingMsg.from_dict(
        {
            "action": "upsert",
            "message": "body",
            "context_data": {
                "uri": "viking://resources/demo/a.py",
                "account_id": "default",
            },
            "record_ids": ["ignored-record"],
            "update_fields": {"md5": "ignored-md5"},
            "initial_fields": {"vector": [0.1, 0.2]},
        }
    )

    assert restored.action is IndexAction.UPSERT
    assert restored.record_ids == []
    assert restored.field_patch is None
    assert restored.failure_requeue_count == 0
    serialized = restored.to_dict()
    assert "record_ids" not in serialized
    assert "update_fields" not in serialized
    assert "initial_fields" not in serialized
    assert set(restored.to_dict()) == {
        "id",
        "telemetry_id",
        "task_id",
        "_task_work_id",
        "queue_enqueued_at",
        "failure_requeue_count",
        "action",
        "payload",
    }


def test_legacy_update_fields_message_normalizes_to_field_patch_payload():
    restored = EmbeddingMsg.from_dict(
        {
            "message": None,
            "context_data": {
                "uri": "viking://resources/demo/a.py",
                "account_id": "default",
            },
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
