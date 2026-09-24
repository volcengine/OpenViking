# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for Working Memory v2 merge guardrails."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.session import Session, _CheckpointRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["tool", "text", "checkpoint_text"])
async def test_wm_update_accepts_auto_only_provider(monkeypatch, result_kind):
    session = Session(viking_fs=None)
    prior = _wm()
    fallback = AsyncMock(return_value="fallback working memory")
    monkeypatch.setattr(session, "_fallback_generate_wm_creation", fallback)
    ops = _keep_all()
    ops["Current State"] = {"op": "UPDATE", "content": "Compatibility verified."}

    async def complete(**kwargs):
        if kwargs["tool_choice"] != "auto":
            raise ValueError("Thinking mode does not support this tool_choice")
        assert kwargs["tools"][0]["function"]["name"] == "update_working_memory"
        return SimpleNamespace(
            has_tool_calls=result_kind == "tool",
            tool_calls=[SimpleNamespace(arguments={"sections": ops})]
            if result_kind == "tool"
            else [],
        )

    vlm = SimpleNamespace(is_available=lambda: True, get_completion_async=complete)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(vlm=vlm),
    )
    monkeypatch.setattr(
        "openviking.session.session.resolve_output_language_from_conversation",
        lambda *args, **kwargs: "English",
    )
    messages = [Message(id="u1", role="user", parts=[TextPart("Continue the task")])]
    if result_kind == "checkpoint_text":
        with pytest.raises(ValueError, match="no tool call for checkpoints"):
            await session._generate_archive_summary_async(
                messages, prior, [_CheckpointRequest("u1", ("u1",), 100, 200)]
            )
        fallback.assert_not_awaited()
    else:
        result = await session._generate_archive_summary_async(messages, prior)
        if result_kind == "tool":
            assert "Compatibility verified." in result
            assert "Decision 1" in result
            fallback.assert_not_awaited()
        else:
            assert result == "fallback working memory"
            fallback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["", "Legacy session summary"])
@pytest.mark.parametrize("result_kind", ["tool", "text", "missing_checkpoint"])
async def test_wm_creation_accepts_auto_only_provider(monkeypatch, prior, result_kind):
    session = Session(viking_fs=None)
    working_memory = _wm()

    async def complete(**kwargs):
        if kwargs["tool_choice"] != "auto":
            raise ValueError("Thinking mode does not support this tool_choice")
        assert kwargs["tools"][0]["function"]["name"] == "create_working_memory"
        return SimpleNamespace(
            has_tool_calls=result_kind != "text",
            tool_calls=[
                SimpleNamespace(
                    arguments={
                        "working_memory": working_memory,
                        "checkpoint_summaries": []
                        if result_kind == "missing_checkpoint"
                        else ["Compatibility verified."],
                    }
                )
            ]
            if result_kind != "text"
            else [],
        )

    vlm = SimpleNamespace(is_available=lambda: True, get_completion_async=complete)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(vlm=vlm),
    )
    monkeypatch.setattr(
        "openviking.session.session.resolve_output_language_from_conversation",
        lambda *args, **kwargs: "English",
    )
    messages = [Message(id="u1", role="user", parts=[TextPart("Continue the task")])]
    requests = [_CheckpointRequest("u1", ("u1",), 100, 200)]
    if result_kind == "tool":
        result = await session._generate_archive_summary_async(messages, prior, requests)
        assert result.overview == working_memory
        assert result.checkpoint_summaries == ("Compatibility verified.",)
    else:
        error = (
            "no create_working_memory tool call" if result_kind == "text" else "exactly 1 strings"
        )
        with pytest.raises(ValueError, match=error):
            await session._generate_archive_summary_async(messages, prior, requests)


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


def test_dynamic_reminders_flag_large_key_facts_and_bulk_urls():
    key_facts = "\n".join(
        f"- Decision {i}: keep module_{i}.py because API contract {i} is stable."
        for i in range(1, 42)
    )
    files_context = "\n".join(
        f"- https://example.com/images/{i}.png - raw parser image URL." for i in range(1, 23)
    )

    reminders = Session._build_wm_section_reminders(
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

    merged = Session._merge_wm_sections(old_wm, ops)

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

    merged = Session._merge_wm_sections(old_wm, ops)

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

    merged = Session._merge_wm_sections(old_wm, ops)

    assert "openviking/session/session.py" in merged
    assert "unused.png" in merged
