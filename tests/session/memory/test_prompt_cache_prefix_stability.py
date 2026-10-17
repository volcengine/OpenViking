# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Regression tests for prompt-cache-friendly ordering of memory prompts (#5235).

The transcript grows append-only across commits, so every byte placed before it
must stay stable; otherwise the provider prompt-cache prefix breaks at the
dynamic block and the whole transcript is re-sent as fresh input on each commit.
"""

from types import SimpleNamespace

import pytest

from openviking.message import Message, TextPart
from openviking.prompts import render_prompt
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)

TAIL_OF_ORIGINAL = "TAIL_OF_ORIGINAL_MARKER"


@pytest.fixture(autouse=True)
def _runtime_config(monkeypatch):
    config = SimpleNamespace(
        output_language_override="",
        language_fallback="en",
        memory=SimpleNamespace(
            eager_prefetch=False,
            prefetch_search_topn=5,
            link_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.get_openviking_config",
        lambda: config,
    )


def _message(id: str, role: str, text: str, created_at: str) -> Message:
    return Message(id=id, role=role, parts=[TextPart(text)], created_at=created_at)


class TestConversationMessagePrefixStability:
    """The extraction conversation message must keep a byte-stable transcript prefix."""

    def test_session_time_header_uses_first_message_time_only(self):
        provider = SessionExtractContextProvider(
            messages=[
                _message("m1", "user", "hello there", "2026-09-01T10:00:00"),
                _message("m2", "assistant", "hi", "2026-09-02T18:30:00"),
            ]
        )
        content = provider._build_conversation_message()["content"]
        header = content.split("\n")[1]
        assert "**Session Time:** 2026-09-01 10:00" in header
        assert "2026-09-02" not in header

    def test_range_end_renders_below_transcript(self):
        provider = SessionExtractContextProvider(
            messages=[
                _message("m1", "user", "hello there", "2026-09-01T10:00:00"),
                _message("m2", "assistant", TAIL_OF_ORIGINAL, "2026-09-02T18:30:00"),
            ]
        )
        content = provider._build_conversation_message()["content"]
        ends_index = content.index("**Conversation ends:** 2026-09-02 18:30")
        assert ends_index > content.index(TAIL_OF_ORIGINAL)

    def test_range_end_tracks_newest_message_after_growth(self):
        original = [
            _message("m1", "user", "hello there", "2026-09-01T10:00:00"),
            _message("m2", "assistant", "hi", "2026-09-02T18:30:00"),
        ]
        grown = original + [
            _message("m3", "user", "one more turn", "2026-09-03T09:00:00"),
        ]
        grown_content = SessionExtractContextProvider(messages=grown)._build_conversation_message()[
            "content"
        ]
        assert "**Conversation ends:** 2026-09-03 09:00" in grown_content
        assert "**Conversation ends:** 2026-09-02 18:30" not in grown_content

    def test_transcript_prefix_stable_across_growth(self):
        original = [
            _message("m1", "user", "hello there", "2026-09-01T10:00:00"),
            _message("m2", "assistant", TAIL_OF_ORIGINAL, "2026-09-02T18:30:00"),
        ]
        grown = original + [
            _message("m3", "user", "one more turn", "2026-09-03T09:00:00"),
        ]
        original_content = SessionExtractContextProvider(
            messages=original
        )._build_conversation_message()["content"]
        grown_content = SessionExtractContextProvider(messages=grown)._build_conversation_message()[
            "content"
        ]
        stable_length = original_content.index(TAIL_OF_ORIGINAL) + len(TAIL_OF_ORIGINAL)
        assert grown_content.startswith(original_content[:stable_length])

    def test_single_message_session_has_no_range_end(self):
        provider = SessionExtractContextProvider(
            messages=[_message("m1", "user", "solo", "2026-09-01T10:00:00")]
        )
        content = provider._build_conversation_message()["content"]
        assert "**Session Time:** 2026-09-01 10:00" in content
        assert "Conversation ends" not in content


@pytest.fixture()
def _bundled_templates(monkeypatch):
    """Pin prompt rendering to the bundled templates, independent of ambient config."""

    def _no_config():
        raise RuntimeError("unit tests must use bundled templates")

    monkeypatch.setattr("openviking.prompts.manager.get_openviking_config", _no_config)


class TestWmUpdateTemplateOrder:
    """The WM update template must keep the transcript ahead of per-commit content."""

    def _render(self, messages: str, wm: str, reminders: str = "") -> str:
        return render_prompt(
            "compression.ov_wm_v2_update",
            {
                "messages": messages,
                "latest_archive_overview": wm,
                "wm_section_reminders": reminders,
                "checkpoint_instructions": "",
                "output_language": "en",
            },
        )

    def test_transcript_block_precedes_current_wm(self):
        prompt = self._render(messages="TRANSCRIPT_BODY", wm="WM_BODY")
        assert prompt.index("<new_content>") < prompt.index("<current_wm>")
        assert prompt.index("TRANSCRIPT_BODY") < prompt.index("WM_BODY")

    def test_transcript_prefix_stable_when_wm_and_reminders_change(self):
        transcript = "turn one\n" + TAIL_OF_ORIGINAL
        first = self._render(messages=transcript, wm="WM version one", reminders="")
        second = self._render(
            messages=transcript + "\nturn two",
            wm="WM version two CHANGED",
            reminders="Key Facts & Decisions is oversized",
        )
        # A later commit appends inside <new_content>, so the shared prefix ends
        # at the last transcript byte, before first's closing </new_content>.
        stable_prefix = first[: first.index(TAIL_OF_ORIGINAL) + len(TAIL_OF_ORIGINAL)]
        assert first.index(TAIL_OF_ORIGINAL) < first.index("<current_wm>")
        assert second.startswith(stable_prefix)
