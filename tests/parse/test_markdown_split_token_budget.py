"""Native section limits use characters, independently of legacy token settings."""

import pytest

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
async def test_layout_uses_character_budget_ignoring_legacy_tokens(with_headings):
    parser = MarkdownParser(ParserConfig(max_section_size=1))
    content = ("# Title\n\n" if with_headings else "") + "中" * 1990
    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="doc")
    writes = [op for op in layout.ops if op.kind == "write"]
    assert len(writes) == 1
    assert writes[0].content == content


@pytest.mark.asyncio
async def test_heading_merges_stay_within_character_budget():
    parser = MarkdownParser()
    content = "\n\n".join(f"## Heading {i}\n\n" + "文" * 750 for i in range(12))
    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="doc")
    writes = [op for op in layout.ops if op.kind == "write"]
    assert all(len(op.content) <= 2000 for op in writes)
    assert sum(op.content.count("文") for op in writes) == 9000


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
@pytest.mark.parametrize(
    "case",
    [
        (1, "", 900, True),
        (2, "", 900, True),
        (3, "", 900, True),
        (1, " \t\n ", 900, True),
        (1, "Introduction.", 900, True),
        (1, "", 3000, True),
        (1, "", 900, False),
        (1, "Introduction.", 3000, True),
    ],
)
async def test_parent_heading_survives_section_planning(case):
    level, introduction, budget, split_content = case
    title = (
        "Deployment guidance for distributed services with detailed "
        "release preparation and recovery procedures"
    )
    ancestors = [f"{'#' * i} Ancestor {i}" for i in range(1, level)]
    parent_heading = f"{'#' * level} {title}"
    child_a = f"{'#' * (level + 1)} Child A"
    child_b = f"{'#' * (level + 1)} Child B"
    content = "\n\n".join(
        [
            *ancestors,
            parent_heading,
            introduction,
            child_a,
            "BODY_A " + "a" * 600,
            child_b,
            "BODY_B " + "b" * 600,
        ]
    )
    parser = MarkdownParser(config=ParserConfig(max_section_chars=budget))
    assert parser.config.max_section_chars == budget
    layout = await parser._compute_layout(
        content, "viking://temp/test", resource_name="doc", split_content=split_content
    )
    writes = [op.content for op in layout.ops if op.kind == "write"]
    assert writes
    output_headings = [
        line for text in writes for line in text.splitlines() if line.startswith("#")
    ]
    assert output_headings == [*ancestors, parent_heading, child_a, child_b]
    output = "\n\n".join(writes)
    assert output.count("BODY_A " + "a" * 600) == 1
    assert output.count("BODY_B " + "b" * 600) == 1
    assert output.index("BODY_A") < output.index("BODY_B")
    if introduction.strip():
        assert output.count(introduction.strip()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 1])
async def test_bodyless_parent_merge_respects_exact_character_budget(extra):
    budget = 900
    title = "Deployment guidance for distributed services and release recovery procedures"
    prefix = f"# {title}\n\n## Child A\n\n"
    body_a = "BODY_A " + "a" * (budget - len(prefix) - len("BODY_A ") + extra)
    body_b = "BODY_B " + "b" * 600
    content = prefix + body_a + "\n\n## Child B\n\n" + body_b
    parser = MarkdownParser(config=ParserConfig(max_section_chars=budget))
    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="doc")
    writes = [op.content for op in layout.ops if op.kind == "write"]
    output = "\n\n".join(writes)
    assert sum(text.count(f"# {title}") for text in writes) == 1
    assert output.count(body_a) == 1
    assert output.count(body_b) == 1
    assert all(len(text) <= budget for text in writes)
    assert output.index(body_a) < output.index(body_b)
