# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for truncate_text_by_tokens and EmbedderBase._truncate_input"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder.base import (
    EmbedderBase,
    EmbedResult,
    truncate_text_by_tokens,
)


# ---------------------------------------------------------------------------
# truncate_text_by_tokens
# ---------------------------------------------------------------------------


class TestTruncateTextByTokens:
    def test_short_text_returned_unchanged(self):
        text = "hello world"
        result = truncate_text_by_tokens(text, max_tokens=100)
        assert result == text

    def test_empty_string(self):
        assert truncate_text_by_tokens("", max_tokens=10) == ""

    def test_exact_limit_not_truncated(self):
        # 10 ASCII chars → 10 utf-8 bytes → fast path allows it through
        text = "a" * 10
        result = truncate_text_by_tokens(text, max_tokens=10)
        assert result == text

    def test_ascii_truncated_by_tiktoken(self):
        # Each ASCII word is ~1 token; build a text that exceeds max_tokens
        words = ["word"] * 200  # ~200 tokens
        text = " ".join(words)
        max_tokens = 50
        result = truncate_text_by_tokens(text, max_tokens=max_tokens)
        assert len(result) < len(text)
        # Verify the result is within the token limit (requires tiktoken)
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            assert len(enc.encode(result, disallowed_special=())) <= max_tokens
        except ImportError:
            pass  # tiktoken not installed; byte-based fallback used

    def test_cjk_text_truncated(self):
        # Chinese text: each CJK char is 3 utf-8 bytes, typically 1-2 tokens
        text = "你好世界" * 500  # 2000 CJK chars, ~6000 utf-8 bytes
        max_tokens = 100
        result = truncate_text_by_tokens(text, max_tokens=max_tokens)
        assert len(result) < len(text)
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            assert len(enc.encode(result, disallowed_special=())) <= max_tokens
        except ImportError:
            pass

    def test_special_tokens_do_not_raise(self):
        # Text containing OpenAI special token strings should not raise
        text = "<|endoftext|>" * 100
        result = truncate_text_by_tokens(text, max_tokens=10)
        assert isinstance(result, str)

    def test_returns_str_not_tuple(self):
        result = truncate_text_by_tokens("hello", max_tokens=100)
        assert isinstance(result, str)

    def test_tiktoken_unavailable_fallback(self):
        # Simulate tiktoken import failure → byte-based fallback
        long_text = "x" * 10000
        max_tokens = 100
        with patch(
            "openviking.models.embedder.base._get_tiktoken_encoder", return_value=None
        ):
            result = truncate_text_by_tokens(long_text, max_tokens=max_tokens)
        assert len(result.encode("utf-8")) <= max_tokens

    def test_utf8_fast_path_skips_tokenization(self):
        # text whose utf-8 byte count <= max_tokens → returned unchanged without tiktoken
        text = "hi"  # 2 utf-8 bytes
        called = []

        original_get_encoder = __import__(
            "openviking.models.embedder.base", fromlist=["_get_tiktoken_encoder"]
        )._get_tiktoken_encoder

        def tracking_get_encoder():
            called.append(True)
            return original_get_encoder()

        with patch(
            "openviking.models.embedder.base._get_tiktoken_encoder",
            side_effect=tracking_get_encoder,
        ):
            result = truncate_text_by_tokens(text, max_tokens=100)

        assert result == text
        assert not called, "tiktoken encoder should not be called for short text"


# ---------------------------------------------------------------------------
# EmbedderBase._truncate_input (via a concrete stub)
# ---------------------------------------------------------------------------


class _StubEmbedder(EmbedderBase):
    """Minimal concrete embedder for testing _truncate_input."""

    def __init__(self, max_tokens: int):
        super().__init__(model_name="stub", config={})
        self._max_tokens = max_tokens

    @property
    def max_input_tokens(self) -> int:
        return self._max_tokens

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        return EmbedResult(dense_vector=[])


class TestTruncateInput:
    def test_short_text_returned_unchanged(self):
        embedder = _StubEmbedder(max_tokens=4000)
        text = "short text"
        assert embedder._truncate_input(text) == text

    def test_long_text_is_truncated(self):
        embedder = _StubEmbedder(max_tokens=50)
        long_text = "word " * 200
        result = embedder._truncate_input(long_text)
        assert len(result) < len(long_text)

    def test_warning_logged_on_truncation(self):
        import logging as _logging

        embedder = _StubEmbedder(max_tokens=20)
        long_text = "token " * 100

        records = []

        class _Capture(_logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = _logging.getLogger("openviking.models.embedder.base")
        handler = _Capture()
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(_logging.WARNING)
        try:
            result = embedder._truncate_input(long_text)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        assert len(result) < len(long_text)
        assert any("truncated" in r.getMessage().lower() for r in records)

    def test_no_warning_for_short_text(self, caplog):
        embedder = _StubEmbedder(max_tokens=4000)
        with caplog.at_level(logging.WARNING, logger="openviking.models.embedder.base"):
            embedder._truncate_input("hello")
        assert not caplog.records

    def test_cjk_truncated_within_limit(self):
        max_tokens = 50
        embedder = _StubEmbedder(max_tokens=max_tokens)
        text = "中文测试" * 300
        result = embedder._truncate_input(text)
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            assert len(enc.encode(result, disallowed_special=())) <= max_tokens
        except ImportError:
            assert len(result.encode("utf-8")) <= max_tokens

    def test_truncated_result_is_valid_utf8(self):
        embedder = _StubEmbedder(max_tokens=30)
        # Mixed ASCII + CJK to exercise multi-byte boundary handling
        text = "hello " * 10 + "世界" * 100
        result = embedder._truncate_input(text)
        # Should not raise when encoding
        result.encode("utf-8")
