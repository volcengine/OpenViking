# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.utils.ingest_options import IngestOptions


def test_semantic_msg_serializes_ingest_options():
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        ingest_options=IngestOptions(search_tags=["team=search"], search_tag_mode="append"),
        source={"kind": "git", "uri": "https://example.com/acme/demo.git"},
        generation_trigger="resource_ingest",
    )

    data = msg.to_dict()

    assert data["ingest_options"] == {
        "search_tags": ["team=search"],
        "search_tag_mode": "append",
    }
    restored = SemanticMsg.from_dict(data)
    assert restored.ingest_options == IngestOptions(
        search_tags=["team=search"],
        search_tag_mode="append",
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


def test_semantic_msg_roundtrip_preserves_queue_enqueue_time():
    msg = SemanticMsg(
        uri="viking://resources/x",
        context_type="resource",
        queue_enqueued_at=123.456,
    )

    assert SemanticMsg.from_dict(msg.to_dict()).queue_enqueued_at == 123.456


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
