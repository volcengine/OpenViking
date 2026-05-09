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

    def test_single_word_found_in_conversation(self):
        assert ExtractLoop._validate_match_text("Python", "I love Python programming") is True

    def test_chinese_word_found_in_conversation(self):
        assert ExtractLoop._validate_match_text("机器学习", "我在研究机器学习技术") is True

    def test_chinese_compound_word(self):
        # Chinese compound words (no spaces) are single words
        assert ExtractLoop._validate_match_text("深度学习", "我们训练了一个深度学习模型") is True

    def test_multi_word_phrase_rejected(self):
        # "machine learning" is a phrase (contains space), not a single word
        assert (
            ExtractLoop._validate_match_text(
                "machine learning", "I study machine learning at school"
            )
            is False
        )

    def test_multi_word_chinese_phrase_with_space_rejected(self):
        # Chinese phrase with space is rejected
        assert ExtractLoop._validate_match_text("机器 学习", "我在研究机器学习技术") is False

    def test_not_found_in_conversation(self):
        assert ExtractLoop._validate_match_text("Java", "I love Python programming") is False

    def test_not_found_in_empty_conversation(self):
        assert ExtractLoop._validate_match_text("Python", "") is False

    def test_word_with_tab_rejected(self):
        # Tab character means it's not a single word
        assert ExtractLoop._validate_match_text("hello\tworld", "hello\tworld here") is False

    def test_word_with_newline_rejected(self):
        assert ExtractLoop._validate_match_text("hello\nworld", "hello\nworld here") is False

    def test_word_without_punctuation(self):
        assert ExtractLoop._validate_match_text("API", "The API is great") is True

    def test_word_with_hyphen(self):
        # Hyphenated word (no spaces) is a single word
        assert ExtractLoop._validate_match_text("self-care", "I practice self-care daily") is True

    def test_word_with_period(self):
        # Word with period (no spaces) is a single word
        assert ExtractLoop._validate_match_text("Python.", "I use Python. daily") is True
