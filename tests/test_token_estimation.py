# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Shared token estimation tests."""

from openviking.message import Message, TextPart
from openviking.session.archive_store import ArchiveStore
from openviking.utils.token_estimation import (
    estimate_text_tokens,
    truncate_text_to_token_budget,
)


def test_message_estimated_tokens_is_cjk_aware():
    """Chinese text should not be estimated with the ASCII chars/4 fallback."""
    msg = Message(id="msg-cjk", role="user", parts=[TextPart("\u4f60\u597d\u4e16\u754c")])

    assert msg.estimated_tokens == 6
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("\U0001f600") == 2


def test_embedder_estimate_tokens_is_cjk_aware():
    """EmbedderBase._estimate_tokens must use the shared CJK-aware estimator.

    The embedder previously used a hand-rolled estimate that counted CJK
    unified ideographs at 1 token/char and treated CJK Extension A, CJK
    punctuation, fullwidth forms, kana and Hangul as "other" (1/4 token),
    undercounting Chinese content by up to ~3x in embedding token usage
    accounting. It must stay consistent with estimate_text_tokens.
    """
    from openviking.models.embedder.base import EmbedderBase

    class _MinimalEmbedder(EmbedderBase):
        def embed(self, content, is_query=False):
            raise NotImplementedError

    # _estimate_tokens is a pure method; bypass __init__ to avoid provider setup.
    embedder = _MinimalEmbedder.__new__(_MinimalEmbedder)

    assert embedder._estimate_tokens("\u4f60\u597d\u4e16\u754c") == 6  # 你好世界
    assert embedder._estimate_tokens("\u3400\u3401") == 3  # CJK Extension A
    assert embedder._estimate_tokens("\u3001\u3002") == 3  # CJK punctuation
    assert embedder._estimate_tokens("\uff01\uff02") == 3  # fullwidth forms
    assert embedder._estimate_tokens("\u3042\u3044") == 3  # kana
    assert embedder._estimate_tokens("\ud55c\uae00") == 3  # Hangul
    assert embedder._estimate_tokens("abcd") == 1
    assert embedder._estimate_tokens("\U0001f600") == 2
    assert embedder._estimate_tokens("") == 0


def test_truncate_text_to_token_budget_preserves_head_and_tail():
    text = "summary-start " + ("填充内容" * 100) + " relevant-tail"

    truncated = truncate_text_to_token_budget(text, 32)

    assert estimate_text_tokens(truncated) <= 32
    assert truncated.startswith("summary-start")
    assert truncated.endswith("relevant-tail")


async def test_archive_overview_tokens_do_not_trust_stale_low_metadata():
    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del uri, ctx
            return '{"overview_tokens": 1}'

    store = ArchiveStore(FakeFS(), None, "viking://session/test")

    tokens = await store.read_overview_tokens(
        "viking://session/test/history/archive_001",
        "\u4f60\u597d\u4e16\u754c",
    )

    assert tokens == 6


async def test_archive_overview_tokens_keep_higher_metadata_estimate():
    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del uri, ctx
            return '{"overview_tokens": 5}'

    store = ArchiveStore(FakeFS(), None, "viking://session/test")

    tokens = await store.read_overview_tokens(
        "viking://session/test/history/archive_001",
        "abcd",
    )

    assert tokens == 5
