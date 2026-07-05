# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
    extract_injected_experience_reminders,
)


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
