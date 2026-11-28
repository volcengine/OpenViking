"""Native section limits use characters, independently of legacy token settings."""

import pytest

from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig


def _oversized_table_rows() -> list[str]:
    # Four data rows of ~700 characters each: any single row fits the budget,
    # the whole table does not.
    return ["| ROW_%02d " % i + chr(65 + i) * 690 + " |" for i in range(4)]


@pytest.mark.parametrize("character", ["中", "a", "🙂"])
def test_smart_split_character_budget_preserves_content(character):
    parser = MarkdownParser()
    paragraph = character * 5000
    parts = parser._smart_split_content(paragraph, max_size=2000)
    assert [len(part) for part in parts] == [2000, 2000, 1000]
    assert "".join(parts) == paragraph


def test_short_tail_does_not_overflow_character_budget():
    parser = MarkdownParser()
    parts = parser._smart_split_content("中" * 4000 + "\n\ntail", max_size=2000)
    assert all(len(part) <= 2000 for part in parts)
    assert parts[-1] == "tail"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_headings", [False, True])
async def test_layout_uses_character_budget_ignoring_legacy_tokens(with_headings, tmp_path):
    parser = MarkdownParser(config=ParserConfig(max_section_size=1))
    content = ("# Title\n\n" if with_headings else "") + "中" * 1990
    store = LocalParseOutputStore(str(tmp_path / "out"))

    result = await parser.parse_content(content, source_path="doc.md", parse_output_store=store)

    assert result.artifact_ref is not None
    assert set(await read_artifact_manifest(store, result.artifact_ref)) == {"doc/doc.md"}
    assert await store.read_bytes(result.artifact_ref, "doc/doc.md") == content.encode()


@pytest.mark.asyncio
async def test_split_sections_keep_source_order_within_character_budget():
    parser = MarkdownParser()
    children = [f"## Chapter {i}\n\n" + "文" * 750 for i in range(1, 4)]
    content = "\n\n".join(["# Guide\n\nIntroduction", *children, "# Appendix\n\nNotes"])
    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="doc")
    sections = [op.content for op in layout.ops if op.kind == "write"]
    assert all(len(section) <= 2000 for section in sections)
    assert "\n\n".join(sections) == content


@pytest.mark.parametrize("leading_row", ["", "| short |\n"])
def test_oversized_table_row_remains_intact_with_header(leading_row):
    parser = MarkdownParser()
    row = "| " + "文" * 2500 + " |"
    header = "| Column |\n| --- |"
    content = header + "\n" + leading_row + row
    parts = parser._smart_split_content(content, max_size=2000)
    assert header + "\n" + row in parts
    assert header not in parts
    assert sum(part.count(row) for part in parts) == 1


def test_table_header_does_not_push_ordinary_rows_over_budget():
    parser = MarkdownParser()
    rows = ["| " + "文" * 1950 + " |" for _ in range(2)]
    content = "| " + "标题" * 40 + " |\n| --- |\n" + "\n".join(rows)
    parts = parser._smart_split_content(content, max_size=2000)
    assert all(len(part) <= 2000 for part in parts)
    assert sum(part.count("文") for part in parts) == 3900
    assert all(any(row in part for part in parts) for row in rows)


@pytest.mark.parametrize("header_size", [1990, 2100])
def test_oversized_header_and_row_stay_together_without_duplicate_header(header_size):
    parser = MarkdownParser()
    header = "| " + "H" * header_size + " | X |\n| --- | --- |"
    row = "| " + "文" * 2500 + " | Y |"
    content = header + "\n" + row
    assert parser._smart_split_content(content, max_size=2000) == [content]


_ADJACENT_TAILS = [
    "\n> End note",
    "\n- End note",
    "\n***\nEnd note",
    "\n\n> End note",
]


@pytest.mark.parametrize("tail", _ADJACENT_TAILS)
def test_adjacent_block_keeps_table_rows_intact(tail):
    """GFM ends a table at a following block without a blank line; row splitting
    must keep working instead of falling back to raw character slicing."""
    parser = MarkdownParser()
    rows = _oversized_table_rows()
    content = "| Column |\n| --- |\n" + "\n".join(rows) + tail
    parts = parser._smart_split_content(content, max_size=2000)

    assert all(len(part) <= 2000 for part in parts)
    joined = "\n".join(parts)
    assert [sum(row in part for part in parts) for row in rows] == [1, 1, 1, 1]
    positions = [joined.index(row) for row in rows]
    assert positions == sorted(positions)
    assert joined.count("End note") == 1
    assert tail.strip() in parts[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", _ADJACENT_TAILS)
async def test_layout_adjacent_block_keeps_table_rows_intact(tail):
    parser = MarkdownParser()
    rows = _oversized_table_rows()
    content = "| Column |\n| --- |\n" + "\n".join(rows) + tail
    layout = await parser._compute_layout(content, "viking://temp/table-tail", resource_name="s")
    parts = [op.content for op in layout.ops if op.kind == "write"]

    assert all(len(part) <= 2000 for part in parts)
    assert [sum(row in part for part in parts) for row in rows] == [1, 1, 1, 1]
    assert tail.strip() in parts[-1]


def test_pipeless_body_rows_stay_intact_before_adjacent_block():
    """GFM body rows may omit outer pipes; they belong to the table, not to the
    following content."""
    parser = MarkdownParser()
    rows = ["ROW_%02d | " % i + chr(65 + i) * 690 for i in range(4)]
    content = "| Column |\n| --- |\n" + "\n".join(rows) + "\n> End note"
    parts = parser._smart_split_content(content, max_size=2000)

    assert all(len(part) <= 2000 for part in parts)
    assert [sum(row in part for part in parts) for row in rows] == [1, 1, 1, 1]
    assert "End note" in parts[-1]


def test_oversized_adjacent_block_is_split_to_budget_after_table():
    parser = MarkdownParser()
    rows = _oversized_table_rows()
    note = "> " + "文" * 2500
    content = "| Column |\n| --- |\n" + "\n".join(rows) + "\n" + note
    parts = parser._smart_split_content(content, max_size=2000)

    assert all(len(part) <= 2000 for part in parts)
    assert [sum(row in part for part in parts) for row in rows] == [1, 1, 1, 1]
    assert sum(part.count("文") for part in parts) == 2500


def test_crlf_table_with_adjacent_block_keeps_rows_intact():
    parser = MarkdownParser()
    rows = _oversized_table_rows()
    content = "\r\n".join(["| Column |", "| --- |", *rows, "> End note"])
    parts = parser._smart_split_content(content, max_size=2000)

    assert [sum(row in part for part in parts) for row in rows] == [1, 1, 1, 1]
    assert "End note" in parts[-1]


def test_table_mismatched_line_coordinates_fall_back_to_budget_split():
    """A lone CR desynchronizes markdown-it line maps from splitlines(); the
    helper keeps the previous raw-slicing fallback in that case."""
    parser = MarkdownParser()
    rows = [row.replace("ROW_02", "ROW_0\r2") for row in _oversized_table_rows()]
    content = "| Column |\n| --- |\n" + "\n".join(rows) + "\n> End note"
    parts = parser._smart_split_content(content, max_size=2000)

    # Fallback: nothing lost, just not row-aligned anymore.
    assert "".join(part.replace("\n", "").replace("\r", "") for part in parts).startswith(
        "| Column || --- |"
    )
    assert all("ROW_0" in part for part in parts)
