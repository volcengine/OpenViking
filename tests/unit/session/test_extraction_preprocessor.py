# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for WM input distillation preprocessor."""

from openviking.message import Message
from openviking.message.part import TextPart, ToolPart
from openviking.session.extraction_preprocessor import (
    PreprocessorOptions,
    build_wm_compact_packet,
)
from openviking.session.session import WM_SEVEN_SECTIONS, Session


def _msg(idx: int, role: str, text: str) -> Message:
    return Message(id=f"m{idx}", role=role, parts=[TextPart(text=text)])


def test_extracts_section_signals_with_source_ids():
    messages = [
        _msg(
            1,
            "user",
            (
                "目标是先只接入 WM v2 update。请修改 "
                "openviking/session/session.py，并记住我要求默认保守。"
            ),
        ),
        _msg(
            2,
            "assistant",
            "遇到 error: tool_call failed，根因是 JSON 截断，后续需要跟进。",
        ),
    ]

    packet = build_wm_compact_packet(
        messages,
        latest_overview="# Working Memory\n\n## Session Title\nToken work",
        options=PreprocessorOptions(fallback_if_compact_ratio_above=10.0),
    )

    files = packet.section_signals["Files & Context"]
    errors = packet.section_signals["Errors & Corrections"]
    issues = packet.section_signals["Open Issues"]
    facts = packet.section_signals["Key Facts & Decisions"]

    assert any("openviking/session/session.py" in signal.text for signal in files)
    assert any("tool_call failed" in signal.text for signal in errors)
    assert any(signal.kind == "open_issue" for signal in issues)
    assert any(signal.kind == "preference" for signal in facts)
    assert all(signal.source_id for signal in packet.structured_facts)


def test_span_selection_deduplicates_repeated_messages_and_keeps_latest_user():
    repeated = "We should update the same implementation note and repeat details."
    messages = [
        _msg(1, "assistant", repeated),
        _msg(2, "assistant", repeated),
        _msg(3, "assistant", repeated),
        _msg(4, "user", "最新要求：先不要接入 creation，只验证 update path。"),
    ]

    packet = build_wm_compact_packet(
        messages,
        options=PreprocessorOptions(
            max_span_tokens=120,
            fallback_if_compact_ratio_above=10.0,
        ),
    )

    selected_ids = [span.source_id for span in packet.selected_spans]

    assert "m4" in selected_ids
    assert selected_ids.count("m1") + selected_ids.count("m2") + selected_ids.count("m3") <= 1


def test_failed_tool_is_kept_as_atomic_span_and_sets_risk():
    messages = [
        Message(
            id="m1",
            role="assistant",
            parts=[
                ToolPart(
                    tool_id="tool-1",
                    tool_name="pytest",
                    tool_input={"cmd": "pytest tests/unit/session"},
                    tool_output="FAILED tests/unit/session/test_x.py::test_case",
                    tool_status="error",
                    duration_ms=123.0,
                    prompt_tokens=10,
                    completion_tokens=5,
                )
            ],
        )
    ]

    packet = build_wm_compact_packet(
        messages,
        options=PreprocessorOptions(fallback_if_compact_ratio_above=10.0),
    )

    assert "failed_tool" in packet.risk_flags
    assert len(packet.selected_spans) == 1
    span_text = packet.selected_spans[0].text
    assert "[tool:pytest (error)]" in span_text
    assert 'input: {"cmd": "pytest tests/unit/session"}' in span_text
    assert "FAILED tests/unit/session/test_x.py::test_case" in span_text
    assert "duration_ms=123.0" in span_text


def test_compact_packet_falls_back_when_not_smaller_enough():
    # Need enough content to pass min_full_tokens_for_compact (default 600)
    # but compact should still be too large relative to full.
    long_line = "token work " * 80  # ~1200 chars, ~300 tokens
    messages = [
        _msg(1, "user", long_line),
        _msg(2, "assistant", long_line),
        _msg(3, "user", long_line),
    ]

    packet = build_wm_compact_packet(
        messages,
        options=PreprocessorOptions(
            fallback_if_compact_ratio_above=0.5,
            min_full_tokens_for_compact=100,
        ),
    )

    assert packet.should_fallback
    assert "compact_not_smaller_enough" in (packet.fallback_reason or "")


def test_rendered_packet_contains_all_sections_and_source_ids():
    messages = [
        _msg(1, "user", "目标：减少 token。文件 openviking/session/session.py。"),
        _msg(2, "assistant", "当前状态：已经完成基线确认，下一步接入 update path。"),
        _msg(3, "user", "还有一个错误需要修复：KeyError in config loader。"),
        _msg(4, "assistant", "我偏好保持保守默认值，不要激进改动。"),
    ]

    packet = build_wm_compact_packet(
        messages,
        latest_overview="# Working Memory\n\n## Session Title\nToken distillation",
        options=PreprocessorOptions(
            fallback_if_compact_ratio_above=10.0,
            min_full_tokens_for_compact=10,
        ),
    )

    # Non-empty sections should have headers
    assert "### Current State" in packet.wm_update_view
    assert "### Task & Goals" in packet.wm_update_view
    assert "source: #0 m1" in packet.wm_update_view
    # Empty sections should be collapsed into summary line
    assert "no signals in:" in packet.wm_update_view
    assert packet.token_estimates.compact_packet_tokens_est > 0


def _prior_wm() -> str:
    parts = ["# Working Memory", ""]
    for section in WM_SEVEN_SECTIONS:
        parts.extend([f"## {section}", "Existing content.", ""])
    return "\n".join(parts)


class _FakeToolCall:
    arguments = {"sections": {section: {"op": "KEEP"} for section in WM_SEVEN_SECTIONS}}


class _FakeResponse:
    has_tool_calls = True
    tool_calls = [_FakeToolCall()]
    finish_reason = "tool_calls"
    usage = {}


class _FakeVLM:
    def is_available(self):
        return True

    async def get_completion_async(self, prompt, **kwargs):
        return _FakeResponse()


class _MemoryConfig:
    def __init__(self, enabled: bool, fallback_ratio: float = 10.0):
        self.wm_v2_preprocess_enabled = enabled
        self.wm_v2_preprocess_max_span_tokens = 1200
        self.wm_v2_preprocess_fallback_ratio = fallback_ratio


class _Config:
    def __init__(self, memory):
        self.memory = memory
        self.vlm = _FakeVLM()


async def test_wm_update_wiring_uses_compact_messages_when_enabled(monkeypatch):
    import openviking.prompts
    import openviking.session.session as session_module

    captured = {}

    def fake_render_prompt(template_id, variables):
        captured["template_id"] = template_id
        captured["messages"] = variables["messages"]
        return "rendered prompt"

    monkeypatch.setattr(openviking.prompts, "render_prompt", fake_render_prompt)
    monkeypatch.setattr(
        session_module,
        "get_openviking_config",
        lambda: _Config(_MemoryConfig(enabled=True, fallback_ratio=10.0)),
    )

    session = object.__new__(Session)
    long_chatter = " ".join(f"routine implementation detail {idx}" for idx in range(240))
    messages = [
        _msg(1, "assistant", long_chatter),
        _msg(2, "assistant", long_chatter),
        _msg(3, "user", "目标：减少 token。文件 openviking/session/session.py。"),
        _msg(4, "assistant", "当前状态：已经完成基线确认，下一步接入 update path。"),
    ]

    await session._generate_archive_summary_async(messages, latest_archive_overview=_prior_wm())

    assert captured["template_id"] == "compression.ov_wm_v2_update"
    assert captured["messages"].startswith("# Compact Working Memory Update Packet")


async def test_wm_update_wiring_uses_full_messages_when_disabled(monkeypatch):
    import openviking.prompts
    import openviking.session.session as session_module

    captured = {}

    def fake_render_prompt(template_id, variables):
        captured["template_id"] = template_id
        captured["messages"] = variables["messages"]
        return "rendered prompt"

    monkeypatch.setattr(openviking.prompts, "render_prompt", fake_render_prompt)
    monkeypatch.setattr(
        session_module,
        "get_openviking_config",
        lambda: _Config(_MemoryConfig(enabled=False)),
    )

    session = object.__new__(Session)
    messages = [_msg(1, "user", "short")]

    await session._generate_archive_summary_async(messages, latest_archive_overview=_prior_wm())

    assert captured["template_id"] == "compression.ov_wm_v2_update"
    assert captured["messages"] == "[user]: short"
