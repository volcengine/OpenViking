# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.acl import AclSpec, AclUpdate
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.utils.ingest_options import IngestOptions


def test_refresh_factory_preserves_legacy_wire_format_and_effective_retry_state():
    from openviking.storage.context_update_plan import SemanticRefreshIntent

    uri = "viking://resources/repo"
    file_uri = f"{uri}/a.py"
    intent = SemanticRefreshIntent(
        uri=uri,
        context_type="resource",
        recursive=False,
        changes={"modified": [file_uri]},
        generation_trigger="content_write",
        file_md5s={file_uri: "fingerprint"},
        file_ingest_options={file_uri: IngestOptions(search_tags=["team=one"])},
    )
    msg = SemanticMsg.from_refresh(intent, account_id="account", task_id="task")
    payload = msg.to_dict()
    assert "intent" not in payload
    assert "execution_plan" not in payload
    assert payload["changes"] == {"modified": [file_uri]}
    assert payload["task_id"] == "task"
    restored = SemanticMsg.from_json(msg.to_json())
    assert restored.preparation_input() == intent

    # A stale delivery's replacement must carry its effective file-only work.
    restored.aggregate_directory = False
    restored.coalesce_key = ""
    restored.coalesce_version = 0
    retry = SemanticMsg.from_json(restored.to_json())
    assert retry.preparation_input().aggregate_directory is False
    assert retry.file_md5s == {file_uri: "fingerprint"}
    assert retry.file_ingest_options[file_uri].search_tags == ["team=one"]


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


def test_semantic_msg_roundtrip_preserves_file_md5s():
    msg = SemanticMsg(
        uri="viking://resources/x",
        context_type="resource",
        file_md5s={"viking://resources/x/a.py": "md5a"},
    )

    assert SemanticMsg.from_dict(msg.to_dict()).file_md5s == {"viking://resources/x/a.py": "md5a"}


def test_semantic_msg_defaults_file_md5s_to_empty():
    msg = SemanticMsg(uri="viking://resources/x", context_type="resource")

    assert msg.file_md5s == {}
    assert SemanticMsg.from_dict(msg.to_dict()).file_md5s == {}


def test_semantic_msg_roundtrip_preserves_file_vector_actions():
    uri = "viking://resources/x/new.md"
    msg = SemanticMsg(
        uri="viking://resources/x",
        context_type="resource",
        file_vector_actions={uri: "upsert"},
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.file_vector_actions == {uri: "upsert"}


def test_semantic_msg_defaults_file_vector_actions_to_empty():
    msg = SemanticMsg(uri="viking://resources/x", context_type="resource")

    assert msg.file_vector_actions == {}
    assert SemanticMsg.from_dict(msg.to_dict()).file_vector_actions == {}


def test_semantic_msg_rejects_invalid_file_vector_action():
    with pytest.raises(ValueError, match="unsupported file vector action"):
        SemanticMsg(
            uri="viking://resources/x",
            context_type="resource",
            file_vector_actions={"viking://resources/x/a.md": "delete"},
        )


def test_semantic_msg_roundtrip_preserves_queue_enqueue_time():
    msg = SemanticMsg(
        uri="viking://resources/x",
        context_type="resource",
        queue_enqueued_at=123.456,
    )

    assert SemanticMsg.from_dict(msg.to_dict()).queue_enqueued_at == 123.456


def test_semantic_msg_roundtrip_preserves_failure_requeue_count():
    msg = SemanticMsg(
        uri="viking://resources/x",
        context_type="resource",
        failure_requeue_count=1,
    )

    restored = SemanticMsg.from_dict(msg.to_dict())
    legacy = msg.to_dict()
    legacy.pop("failure_requeue_count")

    assert restored.failure_requeue_count == 1
    assert SemanticMsg.from_dict(legacy).failure_requeue_count == 0


def test_semantic_msg_roundtrip_preserves_local_artifact_snapshot():
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
        file_abstracts={"viking://resources/x/a.py": "summary a"},
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.artifact_ref == msg.artifact_ref
    assert restored.artifact_ref["resource_rel"] == "repository"
    assert restored.artifact_files == ["a.py", "src/b.py"]
    assert restored.file_abstracts == {"viking://resources/x/a.py": "summary a"}


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
