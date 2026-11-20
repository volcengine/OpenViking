# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.acl import AclSpec, AclUpdate
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.utils.ingest_options import IngestOptions


def test_semantic_msg_serializes_ingest_options():
    update = AclUpdate(
        uri="viking://resources/demo", acl=AclSpec(acl_mode="restricted", entries=[])
    )
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        ingest_options=IngestOptions(
            search_tags=["team=search"], search_tag_mode="append", acl_update=update
        ),
        source={"kind": "git", "uri": "https://example.com/acme/demo.git"},
        generation_trigger="resource_ingest",
    )

    data = msg.to_dict()

    assert data["ingest_options"] == {
        "search_tags": ["team=search"],
        "search_tag_mode": "append",
        "acl_update": update.model_dump(mode="json"),
    }
    restored = SemanticMsg.from_json(msg.to_json())
    assert restored.ingest_options == IngestOptions(
        search_tags=["team=search"],
        search_tag_mode="append",
        acl_update=update,
    )
    assert restored.source == {
        "kind": "git",
        "uri": "https://example.com/acme/demo.git",
    }
    assert restored.generation_trigger == "resource_ingest"
    assert restored.aggregate_directory is True
    assert restored.use_hierarchical_aggregation is False
    assert restored.propagate_to_parent is True


def test_semantic_msg_reads_legacy_search_tag_fields():
    msg = SemanticMsg.from_dict(
        {
            "uri": "viking://resources/demo",
            "context_type": "resource",
            "account_id": "default",
            "search_tags": ["team=search"],
            "search_tag_mode": "append",
        }
    )

    assert msg.ingest_options == IngestOptions(
        search_tags=["team=search"],
        search_tag_mode="append",
    )
    assert msg.aggregate_directory is True
    assert msg.file_md5s == {}
    assert msg.file_vector_actions == {}
    assert msg.failure_requeue_count == 0


def test_semantic_msg_round_trips_deferred_aggregation_flag():
    msg = SemanticMsg(
        uri="viking://resources/wide",
        context_type="resource",
        aggregate_directory=False,
    )

    assert SemanticMsg.from_json(msg.to_json()).aggregate_directory is False


def test_semantic_msg_round_trips_hierarchical_aggregation_policy():
    msg = SemanticMsg(
        uri="viking://user/alice/memories",
        context_type="memory",
        use_hierarchical_aggregation=True,
        propagate_to_parent=False,
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.use_hierarchical_aggregation is True
    assert restored.propagate_to_parent is False


def test_semantic_msg_rejects_invalid_file_vector_action():
    with pytest.raises(ValueError, match="unsupported file vector action"):
        SemanticMsg(
            uri="viking://resources/x",
            context_type="resource",
            file_vector_actions={"viking://resources/x/a.md": "delete"},
        )


def test_semantic_msg_roundtrip_preserves_pending_artifact_work():
    file_uri = "viking://resources/x/a.py"
    msg = SemanticMsg(
        uri="viking://resources/x",
        context_type="resource",
        artifact_ref={
            "backend": "local",
            "root": "/tmp/artifact-1",
            "resource_rel": "repository",
            "root_type": "dir",
        },
        artifact_files=["a.py", "src/b.py"],
        file_abstracts={file_uri: "summary a"},
        file_md5s={file_uri: "md5a"},
        file_vector_actions={file_uri: "upsert"},
        queue_enqueued_at=123.456,
        failure_requeue_count=1,
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.artifact_ref == msg.artifact_ref
    assert restored.artifact_files == ["a.py", "src/b.py"]
    assert restored.file_abstracts == {file_uri: "summary a"}
    assert restored.file_md5s == {file_uri: "md5a"}
    assert restored.file_vector_actions == {file_uri: "upsert"}
    assert restored.queue_enqueued_at == 123.456
    assert restored.failure_requeue_count == 1


def test_clear_ingest_options_survive_semantic_message_round_trip():
    update = AclUpdate(
        uri="viking://resources/demo", acl=AclSpec(acl_mode="restricted", entries=[])
    )
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        ingest_options=IngestOptions(search_tag_mode="clear", acl_update=update),
    )

    restored = SemanticMsg.from_dict(msg.to_dict())

    assert restored.ingest_options == IngestOptions(
        search_tags=[],
        search_tag_mode="clear",
        acl_update=update,
    )


def test_semantic_msg_round_trips_per_file_ingest_options():
    uri = "viking://resources/demo/a.md"
    update = AclUpdate(
        uri=uri,
        acl=AclSpec(
            acl_mode="restricted",
            entries=[{"principal": "group:engineering", "level": "write"}],
        ),
    )
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        file_ingest_options={
            uri: IngestOptions(
                search_tags=["team=search"],
                search_tag_mode="append",
                acl_update=update,
            )
        },
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.file_ingest_options == {
        uri: IngestOptions(
            search_tags=["team=search"],
            search_tag_mode="append",
            acl_update=update,
        )
    }
