# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""WM v2 archive prompts must carry the resolved output language.

Prompt-capture tests for the three render paths of the archive pipeline
(create / incremental update / update-failure fallback) plus the
output_language_override precedence.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import openviking.prompts.manager as prompt_manager_module
from openviking.message import Message
from openviking.message.part import TextPart
from openviking.models.vlm.base import VLMResponse
from openviking.session.session import Session, WM_SEVEN_SECTIONS


@pytest.fixture(autouse=True)
def _use_bundled_templates(monkeypatch):
    """Force the repo's bundled templates so a user templates_dir override
    in the local config does not shadow the templates under test."""
    bundled = Path(__file__).parents[2] / "openviking" / "prompts" / "templates"
    monkeypatch.setenv("OPENVIKING_PROMPT_TEMPLATES_DIR", str(bundled))
    monkeypatch.setattr(prompt_manager_module, "_default_manager", None)

_PRIOR_WM = """# Working Memory

## Session Title
Outage investigation

## Current State
Investigation continues.

## Task & Goals
Find the outage cause.

## Key Facts & Decisions
None.

## Files & Context
None.

## Errors & Corrections
None.

## Open Issues
Confirm the database state.
"""


def _zh_message() -> Message:
    return Message(
        id="zh-user-1",
        role="user",
        parts=[TextPart("排查退款接口的积分回滚问题，先查事务日志")],
    )


def _session_with_vlm(vlm, monkeypatch, override=None):
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(vlm=vlm, output_language_override=override),
    )
    return Session.__new__(Session)


def _assert_language_and_headers(prompt: str, language: str) -> None:
    assert f"in {language}" in prompt
    for header in WM_SEVEN_SECTIONS:
        assert f"## {header}" in prompt


async def test_wm_creation_prompt_includes_detected_language(monkeypatch):
    calls = []

    class FakeVLM:
        @staticmethod
        def is_available() -> bool:
            return True

        async def get_completion_async(self, prompt=None, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return "WM document"

    session = _session_with_vlm(FakeVLM(), monkeypatch)
    await session._generate_archive_summary_async([_zh_message()])

    assert len(calls) == 1
    _assert_language_and_headers(calls[0]["prompt"], "zh-CN")


async def test_wm_update_prompt_includes_detected_language(monkeypatch):
    from openviking.models.vlm.base import ToolCall

    calls = []

    class FakeVLM:
        @staticmethod
        def is_available() -> bool:
            return True

        async def get_completion_async(self, prompt=None, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return VLMResponse(
                tool_calls=[
                    ToolCall(
                        id="tool-call-1",
                        name="update_working_memory",
                        arguments={
                            "sections": {
                                name: {"op": "KEEP"} for name in WM_SEVEN_SECTIONS
                            }
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

    session = _session_with_vlm(FakeVLM(), monkeypatch)
    result = await session._generate_archive_summary_async(
        [_zh_message()], latest_archive_overview=_PRIOR_WM
    )

    assert len(calls) == 1
    assert "Investigation continues." in result
    _assert_language_and_headers(calls[0]["prompt"], "zh-CN")


async def test_wm_update_fallback_prompt_includes_detected_language(monkeypatch):
    calls = []

    class FakeVLM:
        @staticmethod
        def is_available() -> bool:
            return True

        async def get_completion_async(self, prompt=None, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            if "tools" in kwargs:
                # Update tool_call path: no tool call -> falls back to creation.
                return VLMResponse(finish_reason="stop")
            return "WM document"

    session = _session_with_vlm(FakeVLM(), monkeypatch)
    await session._generate_archive_summary_async(
        [_zh_message()], latest_archive_overview=_PRIOR_WM
    )

    assert len(calls) == 2
    assert "tools" in calls[0]
    assert "tools" not in calls[1]
    _assert_language_and_headers(calls[1]["prompt"], "zh-CN")


async def test_wm_creation_language_override_wins(monkeypatch):
    calls = []

    class FakeVLM:
        @staticmethod
        def is_available() -> bool:
            return True

        async def get_completion_async(self, prompt=None, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return "WM document"

    session = _session_with_vlm(FakeVLM(), monkeypatch, override="de")
    await session._generate_archive_summary_async([_zh_message()])

    assert len(calls) == 1
    _assert_language_and_headers(calls[0]["prompt"], "de")
