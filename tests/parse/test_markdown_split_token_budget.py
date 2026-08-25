"""Regression test: _smart_split_content must respect the token budget.

The force-split branch previously stepped through an oversized paragraph by
``max_chars`` only. A paragraph that is under the character limit but over the
token budget (e.g. dense CJK text, weighted ~0.7 token/char) was therefore
emitted as a single chunk that exceeded ``max_size`` tokens.
"""

from openviking.parse.parsers.markdown import MarkdownParser


def test_smart_split_enforces_token_budget_for_cjk_paragraph():
    parser = MarkdownParser()
    max_size = 2048
    # 5000 CJK chars ~= 3500 estimated tokens (> max_size) but < the default
    # char limit, so the char-only force-split produced one over-budget chunk.
    paragraph = "\u4e2d" * 5000
    parts = parser._smart_split_content(paragraph, max_size=max_size)
    worst = max(parser._estimate_token_count(p) for p in parts)
    assert worst <= max_size, f"chunk has {worst} tokens, exceeds max_size={max_size}"


def test_smart_split_preserves_content():
    parser = MarkdownParser()
    paragraph = "\u4e2d" * 5000
    parts = parser._smart_split_content(paragraph, max_size=2048)
    assert "".join(parts) == paragraph
