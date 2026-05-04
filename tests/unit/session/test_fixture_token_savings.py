# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local fixture validation for ExtractionPreprocessor token savings.

Run: python3 -m pytest tests/unit/session/test_fixture_token_savings.py -v -s
"""

from __future__ import annotations

import pytest
from openviking.message import Message
from openviking.message.part import TextPart, ToolPart
from openviking.session.extraction_preprocessor import (
    PreprocessorOptions,
    build_wm_compact_packet,
    estimate_tokens,
)


def _msg(idx: int, role: str, text: str) -> Message:
    return Message(id=f"m{idx}", role=role, parts=[TextPart(text=text)])


def _tool_msg(
    idx: int,
    role: str,
    tool_name: str,
    tool_input: dict | None = None,
    tool_output: str = "",
    tool_status: str = "completed",
) -> Message:
    return Message(
        id=f"m{idx}",
        role=role,
        parts=[
            ToolPart(
                tool_id=f"tool-{idx}",
                tool_name=tool_name,
                tool_input=tool_input or {},
                tool_output=tool_output,
                tool_status=tool_status,
                duration_ms=150.0,
                prompt_tokens=500,
                completion_tokens=200,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Fixture generators
# ---------------------------------------------------------------------------

SHORT_CHAT_MESSAGES = [
    _msg(1, "user", "Hey! Good to see you! How have you been?"),
    _msg(2, "assistant", "I'm good! Busy with work. What's up?"),
    _msg(3, "user", "I went to a support group yesterday. It was so powerful."),
    _msg(4, "assistant", "That's great! Tell me more about it."),
    _msg(5, "user", "The stories were inspiring. I felt so accepted and supported."),
    _msg(6, "assistant", "I'm really happy for you. What's next?"),
    _msg(7, "user", "I think I want to pursue counseling as a career."),
    _msg(8, "assistant", "That's a wonderful goal. You'd be great at it."),
    _msg(9, "user", "Thanks! I've been thinking about adoption agencies too."),
    _msg(10, "assistant", "That's a big step. Have you done any research yet?"),
    _msg(11, "user", "A bit. I found one that's LGBTQ+ inclusive."),
    _msg(12, "assistant", "That's important. Keep me updated!"),
    _msg(13, "user", "I will! Also I painted a lake sunrise last year."),
    _msg(14, "assistant", "Oh nice! I'd love to see it sometime."),
    _msg(15, "user", "Definitely. It was a 2022 piece, very peaceful scene."),
    _msg(16, "assistant", "You're so talented. What medium did you use?"),
    _msg(17, "user", "Acrylics on canvas. Took about three weeks."),
    _msg(18, "assistant", "Worth every minute I'm sure!"),
    _msg(19, "user", "For sure. Anyway, I should get going."),
    _msg(20, "assistant", "Talk soon! Take care."),
]

LONG_DEBUG_MESSAGES = []
for i in range(80):
    role = "user" if i % 3 == 0 else "assistant"
    if role == "user":
        texts = [
            f"The error in openviking/session/session.py line {1400+i} says KeyError: 'missing_field'. "
            f"I checked the config at /etc/openviking/ov.conf and the memory section looks correct. "
            f"Can you look at the traceback from /var/log/openviking/error.log?",
            f"I also found a problem in openviking_cli/utils/config/memory_config.py. "
            f"The wm_v2_preprocess_enabled field isn't being picked up. "
            f"See https://github.com/volcengine/OpenViking/issues/1800 for context.",
            f"Actually, the root cause is in the Docker setup. "
            f"The binding at openviking/lib/ragfs_python.abi3.so is missing. "
            f"I added it to the Dockerfile at docker/Dockerfile line 45. "
            f"Also need to update requirements.txt to include pathspec>=0.12.",
            f"I prefer to keep the default conservative. Don't enable preprocessing by default. "
            f"The fallback ratio should be 0.9, not any lower. "
            f"And we must not change the creation path until update is validated.",
            f"Deadline: we need this done by 2026-06-15. "
            f"The plan is to validate on small fixtures first, then staging, then prod. "
            f"Don't skip any validation step.",
        ]
        LONG_DEBUG_MESSAGES.append(_msg(i, "user", texts[i % len(texts)]))
    else:
        LONG_DEBUG_MESSAGES.append(
            _msg(
                i,
                "assistant",
                f"I've identified the issue. The traceback at /var/log/openviking/error.log "
                f"shows a missing key in the session commit phase. The fix involves updating "
                f"openviking/session/session.py to handle the edge case. I also verified "
                f"openviking_cli/utils/config/memory_config.py picks up the new fields correctly "
                f"after the Dockerfile change. Running pytest tests/unit/session/ now to confirm.",
            )
        )

TOOL_HEAVY_MESSAGES = []
tool_names = [
    "read_file", "write_file", "execute_command", "search_code",
    "run_tests", "git_diff", "git_log", "find_references",
]
for i in range(60):
    if i % 5 == 0:
        TOOL_HEAVY_MESSAGES.append(
            _msg(i, "user", f"Task step {i//5 + 1}: continue the refactoring.")
        )
    elif i % 3 == 1:
        name = tool_names[i % len(tool_names)]
        status = "error" if i % 7 == 0 else "completed"
        output = (
            f"FAILED tests/unit/session/test_{name}.py::test_case_{i} - AssertionError"
            if status == "error"
            else f"OK: {name} completed successfully in {150 + i * 10}ms"
        )
        TOOL_HEAVY_MESSAGES.append(
            _tool_msg(
                i,
                "assistant",
                name,
                tool_input={"cmd": f"pytest tests/unit/session/test_{name}.py -k test_case_{i}"},
                tool_output=output,
                tool_status=status,
            )
        )
    else:
        TOOL_HEAVY_MESSAGES.append(
            _msg(
                i,
                "assistant",
                f"Ran {tool_names[i % len(tool_names)]}: "
                f"the module at openviking/session/extraction_preprocessor.py "
                f"needs an update to handle the edge case at line {100 + i}.",
            )
        )

PATH_CONFIG_MESSAGES = []
paths_and_configs = [
    "openviking/session/session.py",
    "openviking/session/memory_extractor.py",
    "openviking_cli/utils/config/memory_config.py",
    "openviking/prompts/templates/compression/ov_wm_v2.yaml",
    "tests/unit/session/test_wm_v2_guards.py",
    "examples/openclaw-plugin/context-engine.ts",
    "examples/openclaw-plugin/auto-recall.ts",
    "docker/Dockerfile",
    "/root/.openviking/ov.conf",
    "/etc/openviking/config.toml",
    "https://github.com/volcengine/OpenViking/pull/1782",
    "https://arxiv.org/abs/2501.12345",
]
for i in range(50):
    role = "user" if i % 2 == 0 else "assistant"
    ref = paths_and_configs[i % len(paths_and_configs)]
    PATH_CONFIG_MESSAGES.append(
        _msg(
            i,
            role,
            f"Need to update {ref}. The current implementation at line {100 + i} "
            f"uses the old API. See also {paths_and_configs[(i + 1) % len(paths_and_configs)]} "
            f"for the corresponding test. The config key should be memory.wm_v2_preprocess_enabled "
            f"with default False and max_span_tokens set to 1200.",
        )
    )

MIXED_MESSAGES = (
    list(SHORT_CHAT_MESSAGES[:10])
    + LONG_DEBUG_MESSAGES[20:50]
    + TOOL_HEAVY_MESSAGES[10:30]
    + PATH_CONFIG_MESSAGES[5:25]
    + LONG_DEBUG_MESSAGES[50:80]
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _run_and_report(messages, options=None, label=""):
    packet = build_wm_compact_packet(
        messages,
        latest_overview="# Working Memory\n\n## Session Title\nTest\n\n## Current State\nActive\n",
        options=options or PreprocessorOptions(fallback_if_compact_ratio_above=0.9),
    )
    te = packet.token_estimates
    saved_pct = te.saved_tokens_est / max(te.full_messages_tokens_est, 1)
    return {
        "label": label,
        "messages": len(messages),
        "full_tokens": te.full_messages_tokens_est,
        "compact_tokens": te.compact_packet_tokens_est,
        "saved_tokens": te.saved_tokens_est,
        "saved_pct": saved_pct,
        "fallback": packet.should_fallback,
        "fallback_reason": packet.fallback_reason,
        "spans": len(packet.selected_spans),
        "facts": len(packet.structured_facts),
        "risk_flags": len(packet.risk_flags),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

FIXTURES = [
    (SHORT_CHAT_MESSAGES, "短对话 (20 msgs)"),
    (LONG_DEBUG_MESSAGES, "长调试 (80 msgs)"),
    (TOOL_HEAVY_MESSAGES, "工具密集型 (60 msgs)"),
    (PATH_CONFIG_MESSAGES, "路径/配置型 (50 msgs)"),
    (MIXED_MESSAGES, "混合型 (~85 msgs)"),
]


class TestFixtureTokenSavings:
    """Verify preprocessor token savings across different message types."""

    @pytest.mark.parametrize("messages,label", FIXTURES)
    def test_default_options(self, messages, label):
        """Default options (fallback_ratio=0.9, max_span_tokens=1200)."""
        r = _run_and_report(messages, label=label)
        full = r["full_tokens"]
        compact = r["compact_tokens"]
        print(
            f"\n{label}: full={full}, compact={compact}, "
            f"saved={r['saved_tokens']} ({r['saved_pct']:.0%}), "
            f"spans={r['spans']}, facts={r['facts']}, "
            f"fallback={r['fallback']} ({r['fallback_reason']})"
        )
        # Short chat should fallback or save very little
        # Long sessions should show meaningful savings
        if "短对话" in label:
            assert r["fallback"] or r["saved_pct"] < 0.15, (
                f"Short chat should have minimal savings, got {r['saved_pct']:.0%}"
            )

    @pytest.mark.parametrize("messages,label", FIXTURES)
    def test_tight_options(self, messages, label):
        """Tight options (fallback_ratio=0.6, max_span_tokens=800)."""
        opts = PreprocessorOptions(
            max_span_tokens=800,
            fallback_if_compact_ratio_above=0.6,
        )
        r = _run_and_report(messages, options=opts, label=label)
        full = r["full_tokens"]
        compact = r["compact_tokens"]
        print(
            f"\n{label} [tight]: full={full}, compact={compact}, "
            f"saved={r['saved_tokens']} ({r['saved_pct']:.0%}), "
            f"spans={r['spans']}, facts={r['facts']}, "
            f"fallback={r['fallback']} ({r['fallback_reason']})"
        )

    @pytest.mark.parametrize("messages,label", FIXTURES)
    def test_aggressive_options(self, messages, label):
        """Aggressive options (fallback_ratio=0.4, max_span_tokens=400)."""
        opts = PreprocessorOptions(
            max_span_tokens=400,
            fallback_if_compact_ratio_above=0.4,
        )
        r = _run_and_report(messages, options=opts, label=label)
        full = r["full_tokens"]
        compact = r["compact_tokens"]
        print(
            f"\n{label} [aggressive]: full={full}, compact={compact}, "
            f"saved={r['saved_tokens']} ({r['saved_pct']:.0%}), "
            f"spans={r['spans']}, facts={r['facts']}, "
            f"fallback={r['fallback']} ({r['fallback_reason']})"
        )


class TestStructuredExtraction:
    """Verify regex-based signal extraction quality."""

    def test_extracts_paths_and_urls(self):
        msgs = [
            _msg(1, "user", "Fix openviking/session/session.py and check /etc/config.toml"),
        ]
        packet = build_wm_compact_packet(
            msgs, options=PreprocessorOptions(fallback_if_compact_ratio_above=10.0)
        )
        files = packet.section_signals["Files & Context"]
        found = [s.text for s in files]
        assert any("session.py" in t for t in found), f"No path found in {found}"

    def test_extracts_errors(self):
        msgs = [
            _msg(1, "user", "The build failed with error: KeyError in session.py"),
        ]
        packet = build_wm_compact_packet(
            msgs, options=PreprocessorOptions(fallback_if_compact_ratio_above=10.0)
        )
        errors = packet.section_signals["Errors & Corrections"]
        assert len(errors) > 0, "Should detect error signal"

    def test_extracts_preferences(self):
        msgs = [
            _msg(1, "user", "I prefer conservative defaults. Don't enable by default."),
        ]
        packet = build_wm_compact_packet(
            msgs, options=PreprocessorOptions(fallback_if_compact_ratio_above=10.0)
        )
        facts = packet.section_signals["Key Facts & Decisions"]
        preferences = [s for s in facts if s.kind == "preference"]
        assert len(preferences) > 0, "Should detect preference signal"

    def test_tool_atomicity(self):
        msgs = [
            _tool_msg(
                1, "assistant", "pytest",
                tool_input={"cmd": "pytest tests/"},
                tool_output="FAILED test_x.py::test_case",
                tool_status="error",
            )
        ]
        packet = build_wm_compact_packet(
            msgs, options=PreprocessorOptions(fallback_if_compact_ratio_above=10.0)
        )
        assert len(packet.selected_spans) == 1
        span = packet.selected_spans[0].text
        assert "[tool:pytest (error)]" in span
        assert "FAILED test_x.py::test_case" in span
