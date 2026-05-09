# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.extract_loop import ExtractLoop


class TestValidateMatchText:
    """Tests for ExtractLoop._validate_match_text static method."""

    def test_none_match_text_allowed(self):
        assert ExtractLoop._validate_match_text(None, "some content") is True

    def test_empty_match_text_allowed(self):
        assert ExtractLoop._validate_match_text("", "some content") is True

    def test_word_found_in_content(self):
        assert ExtractLoop._validate_match_text("Python", "I love Python programming") is True

    def test_short_phrase_found_in_content(self):
        assert (
            ExtractLoop._validate_match_text(
                "machine learning", "I study machine learning at school"
            )
            is True
        )

    def test_chinese_word_found(self):
        assert ExtractLoop._validate_match_text("机器学习", "我在研究机器学习技术") is True

    def test_chinese_short_phrase_found(self):
        assert (
            ExtractLoop._validate_match_text("深度学习模型", "我们训练了一个深度学习模型") is True
        )

    def test_not_found_in_content(self):
        assert ExtractLoop._validate_match_text("Java", "I love Python programming") is False

    def test_not_found_none_content(self):
        assert ExtractLoop._validate_match_text("Python", None) is False

    def test_not_found_empty_content(self):
        assert ExtractLoop._validate_match_text("Python", "") is False

    def test_long_text_rejected(self):
        long_text = "A" * 51
        assert ExtractLoop._validate_match_text(long_text, long_text) is False

    def test_max_length_accepted(self):
        text_50 = "A" * 50
        assert ExtractLoop._validate_match_text(text_50, text_50) is True

    def test_sentence_with_period_rejected(self):
        assert ExtractLoop._validate_match_text("Hello. World", "Hello. World is here") is False

    def test_sentence_with_chinese_period_rejected(self):
        assert ExtractLoop._validate_match_text("你好。世界", "你好。世界在这里") is False

    def test_trailing_period_word_accepted(self):
        # Single word ending with period (e.g., abbreviation) should be accepted
        assert ExtractLoop._validate_match_text("Python.", "I use Python. daily") is True

    def test_word_without_punctuation(self):
        assert ExtractLoop._validate_match_text("API", "The API is great") is True
