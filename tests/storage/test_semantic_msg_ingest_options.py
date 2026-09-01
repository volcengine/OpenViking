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
