# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.message import Message, ToolPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.experience_lineage import (
    collect_read_experience_uris,
    experience_source_tag,
)
from openviking.session.train.components.trajectory_analyzer import (
    _trajectory_search_tags_by_uri,
)
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("account", "alice"), role=Role.USER)


def test_collect_read_experience_uris_only_keeps_completed_current_user_reads():
    uri = "viking://user/alice/memories/experiences/order-exchange.md"
    messages = [
        Message(
            id="call",
            role="assistant",
            parts=[
                ToolPart(
                    tool_id="read-1",
                    tool_name="read_experience",
                    tool_input={"uri": uri},
                    tool_status="pending",
                ),
                ToolPart(
                    tool_id="search-1",
                    tool_name="search_experience",
                    tool_input={"query": "exchange"},
                    tool_status="completed",
                    tool_output='{"results":[{"uri":"%s"}]}' % uri,
                ),
            ],
        ),
        Message(
            id="result",
            role="user",
            parts=[
                ToolPart(
                    tool_id="read-1",
                    tool_name="read_experience",
                    tool_status="completed",
                    tool_output='{"uri":"%s"}' % uri,
                ),
                ToolPart(
                    tool_id="read-2",
                    tool_name="read_experience",
                    tool_input={"uri": "viking://user/bob/memories/experiences/other.md"},
                    tool_status="completed",
                ),
                ToolPart(
                    tool_id="read-3",
                    tool_name="read_experience",
                    tool_input={"uri": uri},
                    tool_status="error",
                ),
            ],
        ),
    ]

    assert collect_read_experience_uris(messages, ctx=_ctx()) == [uri]


def test_experience_source_tag_uses_experience_uri_as_key():
    uri = "viking://user/alice/memories/experiences/无订单号换货处理.md"

    tag = experience_source_tag(uri)

    assert tag == f"{uri}=1"
    assert tag.count("=") == 1


def test_experience_source_tag_preserves_case_and_escapes_equals_without_collisions():
    uppercase_uri = "viking://user/Alice/memories/experiences/Exchange=Flow.md"
    lowercase_uri = "viking://user/alice/memories/experiences/exchange=flow.md"

    uppercase_tag = experience_source_tag(uppercase_uri)
    lowercase_tag = experience_source_tag(lowercase_uri)

    assert uppercase_tag == (
        "viking://user/%41lice/memories/experiences/%45xchange%3d%46low.md=1"
    )
    assert lowercase_tag == "viking://user/alice/memories/experiences/exchange%3dflow.md=1"
    assert uppercase_tag != lowercase_tag
    assert uppercase_tag.count("=") == 1
    assert lowercase_tag.count("=") == 1


def test_source_experiences_create_transient_tags_for_every_generated_trajectory():
    first_uri = "viking://user/alice/memories/experiences/exchange.md"
    second_uri = "viking://user/alice/memories/experiences/refund.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_fields={"trajectory_name": "exchange"},
                memory_type="trajectories",
                uris=["viking://user/alice/memories/trajectories/exchange.md"],
            ),
            ResolvedOperation(
                memory_fields={"trajectory_name": "refund"},
                memory_type="trajectories",
                uris=["viking://user/alice/memories/trajectories/refund.md"],
            ),
        ],
        delete_file_contents=[],
        errors=[],
    )

    tags_by_uri = _trajectory_search_tags_by_uri(
        operations,
        [first_uri, second_uri, first_uri],
    )

    expected_tags = [experience_source_tag(first_uri), experience_source_tag(second_uri)]
    assert tags_by_uri == {
        "viking://user/alice/memories/trajectories/exchange.md": expected_tags,
        "viking://user/alice/memories/trajectories/refund.md": expected_tags,
    }
    for operation in operations.upsert_operations:
        assert "source_experience_uris" not in operation.memory_fields
