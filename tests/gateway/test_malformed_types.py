"""Histories whose ``type`` is not a string must not abort request processing."""

from openviking_gateway.capture import capture_messages
from openviking_gateway.protocols import normalize, prefix_chain, strip_thinking, text_content
from openviking_gateway.tool_protocols import tool_protocol

ODD = {"type": "string"}


def malformed():
    return {
        "role": "user",
        "type": ODD,
        "content": [{"type": ODD, "text": "hello"}, {"type": "text", "text": ODD}],
    }


def test_protocol_helpers_ignore_object_types():
    message = malformed()
    assert normalize([message])[0]["type"] == ODD
    assert len(prefix_chain([message])) == 1
    assert text_content(message) == ""
    assert strip_thinking([message])[0]["content"][0]["type"] == ODD


def test_capture_and_responses_ignore_object_types():
    message = malformed()
    assert capture_messages([message], ["a"]) == capture_messages([message], ["a"])
    protocol = tool_protocol("responses")
    assert protocol.accepts_context(message)
    assert not protocol.is_reply(message)
