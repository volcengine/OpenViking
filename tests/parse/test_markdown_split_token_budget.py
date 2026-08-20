"""Regression test: _smart_split_content must respect the token budget.

The force-split branch previously stepped through an oversized paragraph by
``max_chars`` only. A paragraph that is under the character limit but over the
token budget (e.g. dense CJK text, weighted ~0.7 token/char) was therefore
emitted as a single chunk that exceeded ``max_size`` tokens.
"""

from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig


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


def test_smart_split_splits_large_markdown_table_on_row_boundaries():
    parser = MarkdownParser(config=ParserConfig(max_section_chars=110))
    table = "\n".join(
        [
            "| id | value |",
            "| --- | --- |",
            "| row-001 | alpha alpha alpha |",
            "| row-002 | beta beta beta |",
            "| row-003 | gamma gamma gamma |",
            "| row-004 | delta delta delta |",
        ]
    )

    parts = parser._smart_split_content(table, max_size=2048)

    assert len(parts) > 1
    assert all(len(part) <= 110 for part in parts)
    assert all(line.startswith("|") and line.endswith("|") for part in parts for line in part.splitlines())
    for row_id in ("row-001", "row-002", "row-003", "row-004"):
        assert sum(row_id in part for part in parts) == 1


def test_smart_split_repeats_markdown_table_header_after_first_chunk():
    parser = MarkdownParser(config=ParserConfig(max_section_chars=90))
    table = "\n".join(
        [
            "# Sheet",
            "",
            "| id | value |",
            "| --- | --- |",
            "| row-001 | alpha alpha alpha |",
            "| row-002 | beta beta beta |",
            "| row-003 | gamma gamma gamma |",
        ]
    )

    parts = parser._smart_split_content(table, max_size=2048)

    assert parts[0].startswith("# Sheet\n\n| id | value |\n| --- | --- |")
    assert all(part.startswith("| id | value |\n| --- | --- |") for part in parts[1:])
