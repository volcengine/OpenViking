# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.ingest.sources import workbuddy
from openviking.utils.host_envelope import (
    BUILTIN_ENVELOPES,
    HostEnvelope,
    Normalized,
    normalize,
    resolve_envelopes,
)

CLAUDE_CODE = BUILTIN_ENVELOPES["claude-code"]
WORKBUDDY = BUILTIN_ENVELOPES["workbuddy"]
# A bridge that wraps the turn in <user_message>; its routing note quotes that tag.
BRIDGE = HostEnvelope(
    name="bridge",
    detect_any=("bridge_routing", "bridge_reminder"),
    strip=("bridge_routing",),
    extract="user_message",
)

FIRST_TURN = """<bridge_routing>
Only answer what is inside <user_message>...</user_message>.
</bridge_routing>
<whiteboard id="wb_1">project notes</whiteboard>
<user_message>
why is recall slow?
</user_message>
<sender name="someone" />"""

LATER_TURN = """<bridge_reminder>reply with the send command</bridge_reminder>
<user_message>
and the indexing queue?
</user_message>"""


def test_extract_on_first_and_later_turns():
    assert normalize(FIRST_TURN, [BRIDGE]) == Normalized("why is recall slow?", "bridge")
    assert normalize(LATER_TURN, [BRIDGE]).text == "and the indexing queue?"


def test_extracted_body_is_kept_verbatim():
    body = (
        "a turn that is only `<system-reminder>...</system-reminder>`?\n"
        "<system-reminder>\npasted log\n</system-reminder>\n"
        "and it ends with </user_message> quoted"
    )
    turn = f"<bridge_reminder>r</bridge_reminder>\n<user_message>\n{body}\n</user_message>"
    assert normalize(turn, [BRIDGE, CLAUDE_CODE]) == Normalized(body, "bridge")


def test_strip_only_removes_blocks_at_the_edges():
    turn = (
        "<system-reminder>\nTodo list is empty\n</system-reminder>\n"
        "fix the flaky test\n"
        "<system-reminder>\nfile was modified\n</system-reminder>\n"
    )
    assert normalize(turn, [CLAUDE_CODE]) == Normalized("fix the flaky test", "claude-code")


@pytest.mark.parametrize(
    "turn",
    [
        "what if a message is only `<system-reminder>...</system-reminder>`?",
        "a reminder <system-reminder>\nx\n</system-reminder> in the middle of a line",
        "my log:\n```\n<system-reminder>\nTodo list is empty\n</system-reminder>\n```\nwhy?",
        "before\n<system-reminder>\nquoted between two paragraphs\n</system-reminder>\nafter",
    ],
)
def test_quoted_tags_are_left_alone(turn):
    assert normalize(turn, [CLAUDE_CODE]) == Normalized(turn)


def test_host_only_turn_becomes_empty():
    notice = (
        "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
        "</task-notification>"
    )
    assert normalize(notice, [CLAUDE_CODE]) == Normalized("", "claude-code")


@pytest.mark.parametrize(
    "turn",
    [
        "plain question about embeddings",
        "<bridge_reminder>r</bridge_reminder>\nno body tag here",
        "<bridge_reminder>r</bridge_reminder>\n<user_message>\nnever closed",
        "<bridge_routing>\nnever closed <user_message>\nhi\n</user_message>",
        "<system-reminder>\nnever closed\nfix it",
        "",
    ],
)
def test_unmatched_or_broken_turn_passes_through(turn):
    assert normalize(turn, [BRIDGE, CLAUDE_CODE]) == Normalized(turn)


def test_strip_block_that_quotes_the_body_tag_is_skipped():
    turn = (
        "<previous_user_message><user_query>old ask</user_query></previous_user_message>\n"
        "<system-reminder>be brief</system-reminder>\n"
        "<user_query>new ask</user_query>"
    )
    assert normalize(turn, [WORKBUDDY]) == Normalized("new ask", "workbuddy")
    assert workbuddy.user_turn_text(turn) == "new ask"


def test_only_the_first_matching_envelope_runs():
    turn = "<system-reminder>\nnote\n</system-reminder>\n" + LATER_TURN
    assert normalize(turn, [BRIDGE, CLAUDE_CODE]).envelope == "bridge"
    assert normalize(turn, [CLAUDE_CODE, BRIDGE]).envelope == "claude-code"


def test_workbuddy_tags_match_the_ingest_adapter():
    assert WORKBUDDY.strip == workbuddy._HOST_BLOCK_TAGS


def test_resolve_envelopes():
    found, unknown = resolve_envelopes([" WorkBuddy ", "nope", "", "claude-code"])
    assert [e.name for e in found] == ["workbuddy", "claude-code"]
    assert unknown == ["nope"]


def test_envelope_needs_a_detect_tag():
    with pytest.raises(ValueError):
        HostEnvelope(name="empty", detect_any=())
