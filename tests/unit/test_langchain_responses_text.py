# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Record supported Responses text blocks without treating media as text."""

import asyncio

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openviking")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openviking import OpenVikingSessionRecorder
from langchain_openviking.client import get_latest_user_text
from langchain_openviking.testing import InMemoryOpenVikingClient


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "message,expected",
    [
        (HumanMessage([{"type": "input_text", "text": "deployment west"}]), "deployment west"),
        (AIMessage([{"type": "output_text", "text": "remembered"}]), "remembered"),
        (
            HumanMessage(
                [
                    {"type": "input_image", "image_url": "https://example.invalid/chart.png"},
                    {"type": "input_text", "text": "chart caption"},
                    {"type": "text", "text": "second line"},
                ]
            ),
            "chart caption\nsecond line",
        ),
        (HumanMessage([{"type": "text", "text": "standard"}]), "standard"),
        (AIMessage("plain"), "plain"),
        (HumanMessage([{"type": ["text"], "text": "ignored"}]), ""),
        (HumanMessage([{"type": {}, "text": "ignored"}]), ""),
    ],
)
def test_recorder_preserves_responses_text(message, expected, asynchronous):
    client = InMemoryOpenVikingClient()
    client.create_session("responses")
    recorder = OpenVikingSessionRecorder(client=client)
    if asynchronous:
        result = asyncio.run(recorder.arecord("responses", [message]))
    else:
        result = recorder.record("responses", [message])
    assert result.messages_written == 1
    assert client.sessions["responses"][0]["parts"] == [{"type": "text", "text": expected}]


def test_responses_text_used_for_recall_and_tool_output():
    user = HumanMessage([{"type": "input_text", "text": "new question"}])
    assert get_latest_user_text([HumanMessage("old question"), user]) == "new question"
    tool = ToolMessage([{"type": "output_text", "text": "tool result"}], tool_call_id="call-1")
    client = InMemoryOpenVikingClient()
    client.create_session("tools")
    recorder = OpenVikingSessionRecorder(client=client)
    recorder.record("tools", [tool])
    assert client.sessions["tools"][0]["parts"][0]["tool_output"] == "tool result"
