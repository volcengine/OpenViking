# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.utils.ingest_options import IngestOptions


def test_semantic_msg_serializes_ingest_options():
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        ingest_options=IngestOptions(search_tags=["team=search"], search_tag_mode="append"),
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


def test_semantic_msg_round_trips_source_and_generation_trigger():
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        source={"kind": "git", "uri": "https://example.com/acme/demo.git"},
        generation_trigger="resource_ingest",
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.source == {
        "kind": "git",
        "uri": "https://example.com/acme/demo.git",
    }
    assert restored.generation_trigger == "resource_ingest"
