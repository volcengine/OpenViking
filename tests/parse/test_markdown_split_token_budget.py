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


def test_smart_split_chunks_crlf_documents_like_lf_twins():
    """CRLF blank lines must separate paragraphs just like LF blank lines.

    ``split("\\n\\n")`` finds no separator inside ``"\\r\\n\\r\\n"``, so a CRLF
    document collapsed into a single oversized "paragraph" that was force-split
    by blind character slicing, while the same file saved with LF endings
    chunked at paragraph edges.
    """
    parser = MarkdownParser()
    paragraphs = [letter * 900 for letter in "abcd"]  # ~270 estimated tokens each
    lf = "\n\n".join(paragraphs)
    crlf = lf.replace("\n", "\r\n")

    lf_parts = parser._smart_split_content(lf, max_size=500)
    crlf_parts = parser._smart_split_content(crlf, max_size=500)

    assert len(lf_parts) > 1, "the document genuinely chunks at this budget"
    assert [part.replace("\r", "") for part in crlf_parts] == lf_parts
