from __future__ import annotations

import pytest
from vikingbot.agent.experience_constraints import (
    ConstraintActivationInput,
    ConstraintExperience,
    apply_experience_constraint_reminder,
    validate_trigger_code,
)
from vikingbot.agent.memory import MemoryStore


def test_constraint_experience_uses_structured_metadata():
    exp = ConstraintExperience.from_content_and_fields(
        "Rendered content is not parsed for trigger metadata.",
        uri="viking://user/u/memories/experiences/refund.md",
        metadata={
            "experience_name": "refund_check",
            "constraint": "## Situation\n- Refund request.",
            "trigger_code": (
                'def should_trigger(ctx):\n    return ctx.get("candidate_tool") == "refund_order"\n'
            ),
        },
    )

    assert exp is not None
    assert exp.name == "refund_check"
    assert "should_trigger" in exp.trigger_code
    assert exp.constraint == "## Situation\n- Refund request."

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[],
            candidate_tool="refund_order",
            candidate_tool_args={},
            experiences=[exp],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is True
    assert "Refund request" in result.messages[-1]["content"]
    assert "should_trigger" not in result.messages[-1]["content"]


def test_constraint_experience_parses_template_rendered_metadata_from_body():
    exp = ConstraintExperience.from_content_and_fields(
        (
            "## Situation\n"
            "- Refund request\n\n"
            "# Experience Trigger\n"
            "- experience_name: refund_check\n"
            "- trigger_code:\n"
            "```python\n"
            "def should_trigger(ctx):\n"
            '    return ctx.get("candidate_tool") == "refund_order"\n'
            "```\n"
        ),
        uri="viking://user/u/memories/experiences/refund.md",
    )

    assert exp is not None
    assert exp.name == "refund_check"
    assert "should_trigger" in exp.trigger_code
    assert exp.constraint == "## Situation\n- Refund request"


def test_vikingbot_trigger_supports_regex_helpers_after_tool_gate():
    exp = ConstraintExperience(
        uri="viking://user/u/memories/experiences/flight.md",
        name="flight_booking",
        constraint="Check booking details.",
        trigger_code=(
            "def should_trigger(ctx):\n"
            '    if ctx.get("candidate_tool") != "book_reservation":\n'
            "        return False\n"
            '    for message in ctx.get("messages", []):\n'
            '        pattern = r"(book|预订).*(flight|航班)|(flight|航班).*(book|预订)"\n'
            '        if regex_search(pattern, message.get("content", "")):\n'
            "            return True\n"
            "    return False\n"
        ),
    )

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[{"role": "user", "content": "please book a flight"}],
            candidate_tool="book_reservation",
            candidate_tool_args={},
            experiences=[exp],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is True


def test_trigger_context_messages_remain_dict_like_and_read_only():
    exp = ConstraintExperience(
        uri="viking://user/u/memories/experiences/upgrade_cancel.md",
        name="upgrade_cancel",
        constraint="Check upgrade-then-cancel requests.",
        trigger_code=(
            "def should_trigger(ctx):\n"
            "    messages = ctx.get('messages', [])\n"
            "    if not isinstance(messages, list):\n"
            "        return False\n"
            "    for msg in messages:\n"
            "        if isinstance(msg, dict) and 'content' in msg:\n"
            "            content = str(msg['content']).lower()\n"
            "            if 'upgrade' in content and 'cancel' in content:\n"
            "                return True\n"
            "    return False\n"
        ),
    )

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[{"role": "user", "content": "upgrade then cancel it"}],
            candidate_tool="update_reservation_flights",
            candidate_tool_args={"cabin": "business"},
            experiences=[exp],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is True


def test_vikingbot_trigger_runtime_allows_negative_slice_for_recent_messages():
    trigger_code = (
        "def should_trigger(ctx):\n"
        "    messages = ctx.get('messages', [])\n"
        "    return bool(messages[-10:]) and ctx.get('candidate_tool') == 'communicate_with_user'\n"
    )
    validate_trigger_code(trigger_code)

    exp = ConstraintExperience(
        uri="viking://user/u/memories/experiences/final_info.md",
        name="final_info",
        constraint="Check final required info.",
        trigger_code=trigger_code,
    )

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[{"role": "user", "content": "cancel reservation"}],
            candidate_tool="communicate_with_user",
            candidate_tool_args={"content": "completed"},
            experiences=[exp],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is True


def test_trigger_context_rejects_mutation_even_though_dict_like():
    exp = ConstraintExperience(
        uri="viking://user/u/memories/experiences/mutate.md",
        name="mutate",
        constraint="Mutation should not be possible.",
        trigger_code=(
            "def should_trigger(ctx):\n"
            "    messages = ctx.get('messages', [])\n"
            "    messages[0]['content'] = 'mutated'\n"
            "    return True\n"
        ),
    )

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[{"role": "user", "content": "original"}],
            candidate_tool="update_reservation_flights",
            candidate_tool_args={},
            experiences=[exp],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is False


@pytest.mark.asyncio
async def test_memory_store_reads_constraint_experience_from_structured_metadata(
    temp_dir, monkeypatch
):
    uri = "viking://user/u/memories/experiences/refund.md"

    class FakeClient:
        async def read_content(self, read_uri, level="read"):
            assert read_uri == uri
            assert level == "read"
            return "Rendered content is not parsed for trigger metadata."

        async def close(self):
            pass

    async def fake_create(**_kwargs):
        return FakeClient()

    async def fake_search(self, client, query, memory_type, limit):
        assert memory_type == "experiences"
        return [
            {
                "uri": uri,
                "experience_name": "refund_check",
                "constraint": "## Situation\n- Refund request.",
                "trigger_code": (
                    "def should_trigger(ctx):\n"
                    '    return ctx.get("candidate_tool") == "refund_order"\n'
                ),
            }
        ]

    def fail_memory_file_read(*_args, **_kwargs):
        raise AssertionError("VikingBot constraint path must not parse raw MemoryFile content")

    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", fake_create)
    monkeypatch.setattr(MemoryStore, "_search_memory_type", fake_search)
    monkeypatch.setattr(
        "openviking.session.memory.utils.memory_file_utils.MemoryFileUtils.read",
        fail_memory_file_read,
    )

    store = MemoryStore(temp_dir)
    constraints = await store.get_viking_constraint_experiences(
        query="refund",
        workspace_id="workspace",
    )

    assert len(constraints) == 1
    assert constraints[0].name == "refund_check"
    assert "should_trigger" in constraints[0].trigger_code
    assert constraints[0].constraint == "## Situation\n- Refund request."
