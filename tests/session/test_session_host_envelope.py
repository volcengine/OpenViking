# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Host-envelope normalization on the session write path."""

from types import SimpleNamespace

import pytest

from openviking.message import Message, TextPart
from openviking.server.config import HostEnvelopeConfig
from openviking.session import Session
from openviking.utils.host_envelope import BUILTIN_ENVELOPES

BRIDGE_TURN = (
    "<bridge_reminder>reply with the send command</bridge_reminder>\n"
    "<user_message>\nwhy is recall slow?\n</user_message>\n"
    '<sender name="someone" />'
)


@pytest.fixture
def envelopes(service):
    config = HostEnvelopeConfig(
        envelopes=["bridge", "claude-code"],
        custom=[
            {
                "name": "bridge",
                "detect_any": ["bridge_reminder"],
                "extract": "user_message",
            }
        ],
    )
    service.sessions.set_host_envelopes(config.for_messages())
    yield
    service.sessions.set_host_envelopes(())


async def test_user_text_is_normalized_and_original_kept(envelopes, service, request_context):
    session = await service.sessions.create(request_context)
    msg = await session.add_message_async("user", [TextPart(BRIDGE_TURN)])

    assert msg.parts[0].text == "why is recall slow?"
    assert msg.parts[0].original_text == BRIDGE_TURN

    reloaded = service.sessions.session(request_context, session_id=session.session_id)
    await reloaded.load()
    part = reloaded.messages[-1].parts[0]
    assert (part.text, part.original_text) == ("why is recall slow?", BRIDGE_TURN)


async def test_host_only_turn_is_kept_with_empty_text(envelopes, service, request_context):
    notice = "<task-notification>\n<status>completed</status>\n</task-notification>"
    session = await service.sessions.create(request_context)
    msg = await session.add_message_async("user", [TextPart(notice)])

    assert len(session.messages) == 1
    assert (msg.parts[0].text, msg.parts[0].original_text) == ("", notice)


async def test_other_roles_and_plain_turns_are_untouched(envelopes, service, request_context):
    session = await service.sessions.create(request_context)
    reply = await session.add_message_async("assistant", [TextPart(BRIDGE_TURN)])
    plain = await session.add_message_async("user", [TextPart("plain question")])

    assert (reply.parts[0].text, reply.parts[0].original_text) == (BRIDGE_TURN, None)
    assert (plain.parts[0].text, plain.parts[0].original_text) == ("plain question", None)
    assert "original_text" not in plain.to_dict()["parts"][0]


async def test_off_by_default(service, request_context):
    session = await service.sessions.create(request_context)
    msg = await session.add_message_async("user", [TextPart(BRIDGE_TURN)])

    assert (msg.parts[0].text, msg.parts[0].original_text) == (BRIDGE_TURN, None)


def test_config_resolves_builtins_and_custom_in_order():
    config = HostEnvelopeConfig(
        envelopes=["claude-code", "bridge"],
        custom=[{"name": "bridge", "detect_any": ["x"], "extract": "user_message"}],
        search=False,
    )
    resolved = config.for_messages()
    assert resolved[0] is BUILTIN_ENVELOPES["claude-code"]
    assert resolved[1].name == "bridge"
    assert config.for_search() == []
    assert HostEnvelopeConfig().for_messages() == []


def test_config_rejects_unknown_envelope():
    with pytest.raises(ValueError, match="unknown envelope"):
        HostEnvelopeConfig(envelopes=["nope"])


def test_normalize_specs_runs_on_user_text_parts_only():
    envelopes = (BUILTIN_ENVELOPES["claude-code"],)
    wrapped = "<system-reminder>\nnote\n</system-reminder>\nfix it"
    specs = [
        {"role": "user", "parts": [TextPart(wrapped), TextPart("plain")]},
        {"role": "assistant", "parts": [TextPart(wrapped)]},
    ]
    Session._normalize_host_envelopes(SimpleNamespace(_host_envelopes=envelopes), specs)

    user, assistant = specs[0]["parts"], specs[1]["parts"][0]
    assert [(p.text, p.original_text) for p in user] == [("fix it", wrapped), ("plain", None)]
    assert (assistant.text, assistant.original_text) == (wrapped, None)


def test_original_text_round_trips_through_storage():
    msg = Message(id="m1", role="user", parts=[TextPart("fix it", original_text="<x/>fix it")])
    part = Message.from_dict(msg.to_dict()).parts[0]
    assert (part.text, part.original_text) == ("fix it", "<x/>fix it")
