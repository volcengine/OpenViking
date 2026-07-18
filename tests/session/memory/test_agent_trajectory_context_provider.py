# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.prompts.manager import PromptManager
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
    extract_injected_experience_reminders,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry


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
    assert "observable rollout behavior" in conversation_message["content"]
    assert "Use only these IDs" not in conversation_message["content"]
    assert "positive_ids" not in conversation_message["content"]
    assert "negative_ids" not in conversation_message["content"]
    assert "weak_ids" not in conversation_message["content"]
    assert "## Conversation History" in conversation_message["content"]


def test_trajectory_schema_is_factual_and_has_no_experience_effect_labels():
    memory_dir = PromptManager._get_bundled_templates_dir() / "memory"
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.load_from_yaml(str(memory_dir / "trajectories.yaml"))
    schema = registry.get("trajectories")
    field_names = {field.name for field in schema.fields}
    content_description = next(
        field.description for field in schema.fields if field.name == "content"
    )

    assert "experience_effects" not in field_names
    for required in (
        "User Evidence",
        "Runtime Evidence",
        "Execution",
        "Injected Experience Evidence",
        "Uncertainty",
    ):
        assert required in content_description
    assert "exactly one trajectory operation" in content_description.lower()
    assert "- Observed behavior after injection:" in content_description
    assert (
        "- Communication: <user-visible information actually communicated, or none>"
        in content_description
    )
    assert "followed, ignored, contradicted, helpful, misleading" in content_description
    assert "遵循、忽略、违反、有帮助、误导" in content_description
    assert "- Observable use:" not in content_description
    for forbidden in (
        "Correct Work To Preserve",
        "Observed Problem",
        "Candidate bad behavior",
        "What was missing/wrong",
        "Outcome Checks",
        "Value/Scope Evidence",
        "Source Field Evidence",
        "Raw Evidence",
    ):
        assert forbidden not in content_description
