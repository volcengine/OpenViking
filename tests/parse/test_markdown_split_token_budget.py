"""Native section limits use characters, independently of legacy token settings."""

import pytest

from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig


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


@pytest.mark.asyncio
@pytest.mark.parametrize("levels", [(3, 3), (3, 2), (2, 2)])
async def test_layout_keeps_subsection_body_with_heading_when_levels_are_skipped(levels):
    parser = MarkdownParser(config=ParserConfig(max_section_chars=950))
    guide = "# Guide\n\n" + ("Read these installation instructions carefully. " * 12).strip()
    install = f"{'#' * levels[0]} Install\n\n" + (
        "Complete this installation step and record the result. " * 15
    ).strip()
    verify = f"{'#' * levels[1]} Verify\n\n" + (
        "Complete this verification step and record the result. " * 15
    ).strip()

    layout = await parser._compute_layout(
        "\n\n".join([guide, install, verify]), "viking://temp/test", resource_name="doc"
    )
    writes = [op for op in layout.ops if op.kind == "write"]

    assert [(op.uri.rsplit("/", 1)[-1], op.content) for op in writes] == [
        ("Guide.md", guide),
        ("Install.md", install),
        ("Verify.md", verify),
    ]
