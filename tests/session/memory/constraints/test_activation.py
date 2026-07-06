from __future__ import annotations

from types import SimpleNamespace

from openviking.session.memory.constraints import (
    ConstraintActivationInput,
    apply_experience_constraint_reminder,
    select_triggered_experiences,
)
from openviking.session.memory.constraints.schema import ConstraintExperience, build_trigger_context

TOOL_TRIGGER = """
def should_trigger(ctx):
    return ctx.get("candidate_tool") == "refund_order"
"""

MESSAGE_TRIGGER = """
def should_trigger(ctx):
    for message in ctx.get("messages", []):
        if "cancel" in message.get("content", "").lower():
            return True
    return False
"""


def _exp(uri: str, name: str, code: str = TOOL_TRIGGER):
    return ConstraintExperience(
        uri=uri,
        name=name,
        constraint=f"constraint {name}",
        trigger_code=code,
        metadata={"experience_name": name},
    )


def test_select_triggered_experiences_keeps_retrieval_order():
    first = _exp("viking://user/u/memories/experiences/first.md", "first")
    second = _exp("viking://user/u/memories/experiences/second.md", "second")
    third = _exp("viking://user/u/memories/experiences/third.md", "third")
    ctx = build_trigger_context(messages=[], candidate_tool="refund_order", candidate_tool_args={})

    triggered = select_triggered_experiences(
        experiences=[first, second, third],
        ctx=ctx,
        reminded_exp_uris=set(),
    )

    assert [exp.uri for exp in triggered] == [first.uri, second.uri, third.uri]


def test_apply_reminder_appends_all_triggered_messages_and_marks_uris():
    reminded: set[str] = set()
    first = _exp("viking://user/u/memories/experiences/first.md", "first")
    second = _exp("viking://user/u/memories/experiences/second.md", "second")

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[],
            candidate_tool="refund_order",
            candidate_tool_args={},
            experiences=[first, second],
            reminded_exp_uris=reminded,
        )
    )

    assert result.reminded is True
    assert result.experience_uris == [first.uri, second.uri]
    assert reminded == {first.uri, second.uri}
    assert len(result.reminder_messages) == 2
    assert len(result.messages) == 2
    assert "constraint first" in result.messages[0]["content"]
    assert "constraint second" in result.messages[1]["content"]


def test_apply_reminder_appends_user_message_and_marks_uri():
    reminded: set[str] = set()
    messages = [{"role": "user", "content": "refund this"}]
    exp = _exp("viking://user/u/memories/experiences/refund.md", "refund_check")

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=messages,
            candidate_tool="refund_order",
            candidate_tool_args={"order_id": "1"},
            experiences=[exp],
            reminded_exp_uris=reminded,
        )
    )

    assert result.reminded is True
    assert result.experience_uri == exp.uri
    assert exp.uri in reminded
    assert result.messages[:-1] == messages
    assert result.messages[-1]["role"] == "user"
    assert "下面是一条经验 reminder" in result.messages[-1]["content"]
    assert (
        "<triggered_before_tool>refund_order</triggered_before_tool>"
        in result.messages[-1]["content"]
    )
    assert "constraint refund_check" in result.messages[-1]["content"]


def test_reminded_uri_is_ignored_even_if_triggered_again():
    exp = _exp("viking://user/u/memories/experiences/refund.md", "refund_check")

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[],
            candidate_tool="refund_order",
            candidate_tool_args={},
            experiences=[exp],
            reminded_exp_uris={exp.uri},
        )
    )

    assert result.reminded is False
    assert result.messages == []
    assert result.triggered_uris == []


def test_old_experience_without_trigger_code_does_not_participate():
    old_policy = SimpleNamespace(
        uri="viking://user/u/memories/experiences/old.md",
        name="old",
        content="plain old exp",
        metadata={"experience_name": "old"},
    )

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[],
            candidate_tool="refund_order",
            candidate_tool_args={},
            experiences=[old_policy],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is False


def test_policy_like_experience_can_be_used():
    policy = SimpleNamespace(
        uri="viking://user/u/memories/experiences/cancel.md",
        name="cancel",
        content="Check cancellation policy before cancelling.",
        metadata={
            "experience_name": "cancel",
            "trigger_code": MESSAGE_TRIGGER,
        },
    )

    result = apply_experience_constraint_reminder(
        ConstraintActivationInput(
            messages=[{"role": "user", "content": "please cancel it"}],
            candidate_tool="lookup_order",
            candidate_tool_args={},
            experiences=[policy],
            reminded_exp_uris=set(),
        )
    )

    assert result.reminded is True
    assert result.experience_name == "cancel"


def test_memory_file_ignores_template_rendered_metadata_without_structured_fields():
    memory_file = SimpleNamespace(
        uri="viking://user/u/memories/experiences/refund.md",
        extra_fields={},
        plain_content=lambda: (
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
    )

    assert ConstraintExperience.from_memory_file(memory_file) is None


def test_memory_file_uses_structured_content_and_trigger_metadata_for_reminder():
    memory_file = SimpleNamespace(
        uri="viking://user/u/memories/experiences/refund.md",
        extra_fields={
            "experience_name": "refund_check",
            "content": "## Situation\n- Refund request\n",
            "trigger_code": (
                'def should_trigger(ctx):\n    return ctx.get("candidate_tool") == "refund_order"\n'
            ),
        },
        plain_content=lambda: "Rendered body should not be used.",
    )

    exp = ConstraintExperience.from_memory_file(memory_file)

    assert exp is not None
    assert exp.name == "refund_check"
    assert exp.constraint == "## Situation\n- Refund request"
    assert "should_trigger" in exp.trigger_code

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
    assert "## Situation" in result.messages[-1]["content"]
    assert "should_trigger" not in result.messages[-1]["content"]
