# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
    extract_injected_experience_reminders,
)


def test_trajectory_provider_separates_direct_evidence_from_advisory_signals():
    provider = AgentTrajectoryContextProvider(
        messages=[Message(id="1", role="user", parts=[TextPart(text="complete task")])],
        evidence_sources={
            "direct_available": True,
            "items": [{"direct": True, "source": "independent_probe", "value": "timeout"}],
        },
        advisory_signals={"available": True, "items": [{"label": "possible timeout"}]},
    )

    rendered = provider._build_conversation_message()["content"]

    assert "## Evidence Source Contract" in rendered
    assert "Evidence Sources with `direct=true`" in rendered
    assert "authoritative for outcome and requirement compliance" in rendered
    assert "does not independently prove an unobserved internal cause" in rendered
    assert "## Advisory Signals" in rendered
    assert "independent_probe" in rendered
    assert "possible timeout" in rendered
    assert "spreadsheet" not in rendered


def test_trajectory_provider_safely_renders_non_json_evidence_values():
    marker = object()
    provider = AgentTrajectoryContextProvider(
        messages=[Message(id="1", role="user", parts=[TextPart(text="complete task")])],
        evidence_sources={
            "direct_available": True,
            "items": [{"direct": True, "source": "external_observer", "value": marker}],
        },
    )

    rendered = provider._build_conversation_message()["content"]

    assert "external_observer" in rendered
    assert str(marker) in rendered


def test_trajectory_provider_extracts_injected_experience_aliases():
    reminder = """<experience_reminder>
<experience_name>预订前确认支付金额一致</experience_name>
<experience_uri>viking://user/default/memories/experiences/payment.md</experience_uri>
<triggered_before_tool>communicate_with_user</triggered_before_tool>
<experience>body</experience>
</experience_reminder>"""
    messages = [Message(id="1", role="user", parts=[TextPart(text=reminder)])]

    extracted = extract_injected_experience_reminders(messages)

    assert extracted == [
        {
            "id": "E1",
            "experience_name": "预订前确认支付金额一致",
            "experience_uri": "viking://user/default/memories/experiences/payment.md",
            "triggered_before_tool": "communicate_with_user",
        }
    ]

    provider = AgentTrajectoryContextProvider(messages=messages)
    conversation_message = provider._build_conversation_message()

    assert "## Deterministic Injected Experience Reminders" in conversation_message["content"]
    assert "- E1: 预订前确认支付金额一致;" in conversation_message["content"]
    assert "Use only these IDs" in conversation_message["content"]
    assert "## Conversation History" in conversation_message["content"]
