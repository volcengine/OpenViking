# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for language detection utilities — regression coverage for
hash-token pollution from bot adapters (Feishu, Slack, etc.) that inject
message IDs / open IDs into user message content.
"""

import pytest

from openviking.session.memory.utils.language import (
    _detect_language_from_text,
    _strip_hash_like_tokens,
)


class TestStripHashLikeTokens:
    """Verify the hex-token stripper isolates real text from ID metadata."""

    def test_strips_uuid_like_run(self):
        out = _strip_hash_like_tokens("ou_a56ad44f6de54e96b53508d5e3cff3cb hello")
        assert "a56ad44f" not in out
        assert "hello" in out

    def test_strips_feishu_message_id(self):
        text = "[message_id: om_x100b6eb5f06fc8acb29491741d6eb30] hello"
        out = _strip_hash_like_tokens(text)
        assert "100b6eb5" not in out
        assert "hello" in out

    def test_leaves_real_english_words_intact(self):
        # No regular English word is 8+ chars long using only [0-9a-f].
        text = "facade deadbeef cabbage portuguese español"
        out = _strip_hash_like_tokens(text)
        # 'deadbeef' is 8 hex chars — stripped (intentional, it's hash-like).
        assert "deadbeef" not in out
        # 'facade' is only 6 chars — kept.
        assert "facade" in out
        # 'cabbage' contains 'g' (non-hex) — kept.
        assert "cabbage" in out
        assert "portuguese" in out
        assert "español" in out

    def test_empty_input(self):
        assert _strip_hash_like_tokens("") == ""

    def test_no_hex_content(self):
        text = "我有哪些喜好"
        assert _strip_hash_like_tokens(text) == text


class TestDetectLanguageWithHashPollution:
    """Regression: hash-like tokens (message IDs, UUIDs, OAuth open IDs) must
    not bias language detection toward Latin languages when the actual user
    content is in a non-Latin script.

    Real-world trigger: IM bot adapters prepend metadata like
    ``[message_id: om_xxx] ou_xxx:`` to every user message body before the
    text is stored as the message ``content``. Without filtering, hex digits
    dominate the latin-char count, ``_passes_threshold`` fails for zh-CN, and
    ``_detect_latin_language`` pattern-matches stopword-like fragments
    (e.g. ``de`` / ``do``) → returns ``pt`` / ``fr`` / ``es``. Downstream
    prompts then instruct the LLM to emit memories in the wrong language.
    """

    def test_chinese_with_feishu_message_id_detected_as_chinese(self):
        text = (
            "[message_id: om_x100b6eb5f06fc8acb29491741d6eb30] "
            "ou_a56ad44f6de54e96b53508d5e3cff3cb: 我有哪些喜好\n"
            "[message_id: om_x100b6eb5b58cb4a0b3c719778034103] "
            "ou_a56ad44f6de54e96b53508d5e3cff3cb: 我爱吃苹果"
        )
        assert _detect_language_from_text(text, "en") == "zh-CN"

    def test_pure_hash_content_falls_back(self):
        # Content that is *only* hash-like should not bias toward any specific
        # latin language — fall back to the fallback (en here).
        text = (
            "om_x100b6eb5f06fc8acb29491741d6eb30 "
            "ou_a56ad44f6de54e96b53508d5e3cff3cb"
        )
        assert _detect_language_from_text(text, "en") == "en"

    def test_real_portuguese_still_detects_pt(self):
        # Sanity: the stripper must not break legitimate latin-language
        # detection. Real Portuguese stopwords + accents should still resolve
        # to pt.
        text = (
            "Olá, este documento é para o usuário. As preferências do "
            "projeto são importantes e devem ser registradas com cuidado, "
            "para que cada usuário possa consultá-las."
        )
        assert _detect_language_from_text(text, "en") == "pt"

    def test_korean_with_hash_pollution_detected_as_korean(self):
        # ko isn't in _PRIMARY_LANGUAGES, so it only wins via the strong
        # dominance path (>=10 chars AND >=95% ratio of non-latin signal).
        # The stripper must remove the hex pollution so Korean dominates.
        text = (
            "[message_id: a56ad44f6de54e96b53508d5e3cff3cb] "
            "안녕하세요 어떻게 지내세요 오늘 날씨가 정말 좋네요 점심 같이 먹어요"
        )
        assert _detect_language_from_text(text, "ko") == "ko"
