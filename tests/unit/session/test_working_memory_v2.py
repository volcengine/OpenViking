# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for Working Memory v2 merge guardrails."""

from openviking.message import Message, TextPart
from openviking.session import working_memory as wm


def _wm(
    *,
    current_state: str = "Actively updating Working Memory v2 prompts.",
    key_facts: str = "- Decision 1: keep existing WM v2 schema.",
    files_context: str = "- openviking/session/session.py - WM merge logic.",
    errors: str = "",
    open_issues: str = "",
) -> str:
    sections = {
        "Session Title": "Working Memory v2 Guardrails",
        "Current State": current_state,
        "Task & Goals": "Improve WM v2 prompt and merge behavior.",
        "Key Facts & Decisions": key_facts,
        "Files & Context": files_context,
        "Errors & Corrections": errors,
        "Open Issues": open_issues,
    }
    parts = ["# Working Memory", ""]
    for header, body in sections.items():
        parts.extend([f"## {header}", body, ""])
    return "\n".join(parts).rstrip() + "\n"


def _keep_all() -> dict:
    return {
        "Session Title": {"op": "KEEP"},
        "Current State": {"op": "KEEP"},
        "Task & Goals": {"op": "KEEP"},
        "Key Facts & Decisions": {"op": "KEEP"},
        "Files & Context": {"op": "KEEP"},
        "Errors & Corrections": {"op": "KEEP"},
        "Open Issues": {"op": "KEEP"},
    }


def _message(
    message_id: str,
    role: str,
    text: str,
    *,
    created_at: str,
    message_kind: str | None = None,
) -> Message:
    return Message(
        id=message_id,
        role=role,
        parts=[TextPart(text)],
        created_at=created_at,
        message_kind=message_kind,
    )


def test_format_messages_for_wm_marks_each_turn_with_its_own_created_at():
    messages = [
        _message(
            "u1",
            "user",
            "Yesterday I met Alice at the cafe.",
            created_at="2026-09-12T08:00:00Z",
            message_kind="user_query",
        ),
        _message(
            "a1",
            "assistant",
            "Noted.",
            created_at="2026-09-12T08:01:00Z",
            message_kind="assistant_step",
        ),
        _message(
            "u2",
            "user",
            "Yesterday, who did I meet?",
            created_at="2026-09-13T09:00:00Z",
            message_kind="user_query",
        ),
        _message(
            "a2",
            "assistant",
            "You met Alice.",
            created_at="2026-09-13T09:01:00Z",
            message_kind="assistant_step",
        ),
    ]

    formatted = wm.format_messages_for_wm(messages, [])

    assert '<turn created_at="2026-09-12">' in formatted
    assert '<turn created_at="2026-09-13">' in formatted
    assert formatted.index('<turn created_at="2026-09-12">') < formatted.index(
        "Yesterday I met Alice at the cafe."
    )
    assert formatted.index('<turn created_at="2026-09-13">') < formatted.index(
        "Yesterday, who did I meet?"
    )


def test_dynamic_reminders_flag_large_key_facts_and_bulk_urls():
    key_facts = "\n".join(
        f"- Decision {i}: keep module_{i}.py because API contract {i} is stable."
        for i in range(1, 42)
    )
    files_context = "\n".join(
        f"- https://example.com/images/{i}.png - raw parser image URL." for i in range(1, 23)
    )

    reminders = wm.build_wm_section_reminders(
        _wm(key_facts=key_facts, files_context=files_context)
    )

    assert "<section_size_warnings>" in reminders
    assert '"Key Facts & Decisions" has 41 bullets' in reminders
    assert "consolidated via UPDATE" in reminders


def test_key_facts_allows_safe_consolidation_when_oversized():
    """Consolidation UPDATE with too few bullets (1/41 < 15%) is rejected;
    the new consolidated summary is salvaged as APPEND while old items are kept."""
    key_facts = "\n".join(
        f"- Decision {i}: keep module_{i}.py because API contract {i} is stable."
        for i in range(1, 42)
    )
    old_wm = _wm(key_facts=key_facts)
    consolidated = (
        "- Decisions 1-41: keep the stable API contracts for "
        + ", ".join(f"module_{i}.py" for i in range(1, 42))
        + "."
    )
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {"op": "UPDATE", "content": consolidated}

    merged = wm.merge_wm_sections(old_wm, ops)

    assert consolidated in merged
    assert "- Decision 1: keep module_1.py because API contract 1 is stable." in merged
    assert "module_41.py" in merged


def test_key_facts_rejects_unsafe_consolidation_that_drops_anchors():
    key_facts = "\n".join(
        f"- Decision {i}: keep module_{i}.py because API contract {i} is stable."
        for i in range(1, 42)
    )
    old_wm = _wm(key_facts=key_facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "UPDATE",
        "content": "- New decision: only module_1.py remains relevant.",
    }

    merged = wm.merge_wm_sections(old_wm, ops)

    assert "- Decision 41: keep module_41.py because API contract 41 is stable." in merged
    assert "- New decision: only module_1.py remains relevant." in merged


def test_files_context_update_blocked_when_dropping_path_like_tokens():
    """Files & Context guard rejects UPDATE that drops path-like tokens from
    old content; result falls back to KEEP so both items are preserved."""
    old_wm = _wm(
        files_context=(
            "- openviking/session/session.py - WM merge logic.\n"
            "- https://example.com/images/unused.png - raw parser image URL."
        )
    )
    ops = _keep_all()
    ops["Files & Context"] = {
        "op": "UPDATE",
        "content": "- openviking/session/session.py - WM merge logic.",
    }

    merged = wm.merge_wm_sections(old_wm, ops)

    assert "openviking/session/session.py" in merged
    assert "unused.png" in merged
