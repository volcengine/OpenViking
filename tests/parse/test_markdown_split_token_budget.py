"""Native section limits use characters, independently of legacy token settings."""

import pytest
from markdown_it import MarkdownIt

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


def _code_blocks(content):
    return [
        token.content
        for token in MarkdownIt("commonmark").parse(content)
        if token.type == "code_block"
    ]


def _code_example(indent):
    return "".join(
        f"{indent}value_{index:02d} = 'example text for line {index:02d}'\n" for index in range(20)
    )


def _document(indent, structure, prose_before_code):
    code = _code_example(indent)
    body = ("Normal prose before the code.\n\n" if prose_before_code else "") + code
    if structure == "leaf":
        return f"# Example\n\n{body}\n# Other\n\n" + "B" * 1400 + "\n"
    if structure == "child":
        return (
            f"# Parent\n\nParent overview.\n\n## Example\n\n{body}\n## Other\n\n"
            + "B" * 1400
            + "\n"
        )
    return f"# Parent\n\n{body}\n## Other\n\n" + "B" * 1400 + "\n\n## More\n\n" + "C" * 700 + "\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
@pytest.mark.parametrize("structure", ["leaf", "child", "parent_direct"])
@pytest.mark.parametrize("prose_before_code", [False, True])
async def test_section_layout_preserves_indented_code(indent, structure, prose_before_code):
    parser = MarkdownParser()
    content = _document(indent, structure, prose_before_code)
    assert len(content) > parser.config.max_section_chars

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]

    assert len(writes) > 1
    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    actual_code = [block for op in writes for block in _code_blocks(op.content)]
    assert actual_code == _code_blocks(content)


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
async def test_tiny_pending_section_preserves_following_parent_code(indent):
    parser = MarkdownParser()
    content = "# Intro\n\nIntro marker.\n\n" + _document(indent, "parent_direct", False)

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]

    assert sum(op.content.count("Intro marker.") for op in writes) == 1
    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    actual_code = [block for op in writes for block in _code_blocks(op.content)]
    assert actual_code == _code_blocks(content)


@pytest.mark.asyncio
@pytest.mark.parametrize("blank_body", ["   \n", "\t\n"])
async def test_empty_leaf_body_keeps_existing_layout(blank_body):
    parser = MarkdownParser()
    suffix = "# Other\n\n" + "B" * 1400 + "\n\n# More\n\n" + "C" * 1400
    control = await parser._compute_layout(
        "# Empty\n\n" + suffix, "viking://temp/test", resource_name="manual"
    )
    actual = await parser._compute_layout(
        "# Empty\n\n" + blank_body + suffix, "viking://temp/test", resource_name="manual"
    )

    assert [(op.kind, op.uri, op.content) for op in actual.ops] == [
        (op.kind, op.uri, op.content) for op in control.ops
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
@pytest.mark.parametrize("split_content", [True, False])
async def test_small_or_unsplit_code_keeps_original_content(indent, split_content):
    parser = MarkdownParser()
    content = (
        "# Example\n\n" + _code_example(indent)
        if split_content
        else _document(indent, "leaf", False)
    )
    layout = await parser._compute_layout(
        content, "viking://temp/test", resource_name="manual", split_content=split_content
    )
    writes = [op for op in layout.ops if op.kind == "write"]

    assert len(writes) == 1
    assert writes[0].content == content
    assert _code_blocks(writes[0].content) == _code_blocks(content)


def _preamble_document(indent, flow, prose_before_code=False):
    code = _code_example(indent)
    tiny_code = "".join(code.splitlines(keepends=True)[:2])
    prose = "Normal preamble prose.\n\n" if prose_before_code else ""
    if flow == "virtual":
        return prose + code + "\n# Other\n\nBODY_MARKER " + "B" * 1400
    if flow == "merged_leaf":
        return (
            prose
            + tiny_code
            + "\n# Other\n\nBODY_MARKER "
            + "B" * 1400
            + "\n\n# More\n\n"
            + "C" * 1400
        )
    if flow == "merged_parent":
        return (
            prose
            + tiny_code
            + "\n# Parent\n\nParent prose.\n\n## Child\n\nBODY_MARKER "
            + "B" * 1400
            + "\n\n## Other\n\n"
            + "C" * 700
        )
    if flow == "trailing_tiny":
        return prose + code + code + "\n# Appendix\n\nBODY_MARKER " + "D" * 450
    if flow == "oversized_paragraph":
        return prose + code + "\nBODY_MARKER " + "B" * 2002 + "\n\n# Other\n\n" + "C" * 700
    if flow == "oversized_leaf":
        return (
            prose
            + tiny_code
            + "\n# Other\n\nBODY_MARKER "
            + "B" * 2200
            + "\n\n# More\n\n"
            + "C" * 700
        )
    table = "| Column |\n| --- |\n" + "\n".join(
        "| " + ("BODY_MARKER " if index == 0 else f"ROW_{index} ") + "D" * 690 + " |"
        for index in range(4)
    )
    return prose + tiny_code + "\n" + table


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
@pytest.mark.parametrize(
    "flow",
    [
        "virtual",
        "merged_leaf",
        "merged_parent",
        "trailing_tiny",
        "oversized_leaf",
        "table_prefix",
        "oversized_paragraph",
    ],
)
async def test_preamble_layout_preserves_indented_code(indent, flow):
    parser = MarkdownParser()
    content = _preamble_document(indent, flow)
    assert len(content) > parser.config.max_section_chars
    assert len(_code_example(indent) * 2) < parser.config.max_section_chars

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]
    combined = "\n\n".join(op.content for op in writes)

    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    assert [block for op in writes for block in _code_blocks(op.content)] == _code_blocks(content)
    assert combined.count("BODY_MARKER") == 1
    assert combined.index("value_00") < combined.index("BODY_MARKER")


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
@pytest.mark.parametrize("flow", ["virtual", "merged_parent"])
async def test_preamble_prose_before_code_keeps_structure(indent, flow):
    parser = MarkdownParser()
    content = _preamble_document(indent, flow, prose_before_code=True)

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]

    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    assert [block for op in writes for block in _code_blocks(op.content)] == _code_blocks(content)


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
async def test_preamble_tail_merge_uses_preserved_indentation_budget(indent):
    parser = MarkdownParser()
    preamble = "".join(_code_example(indent).splitlines(keepends=True)[:2]).rstrip("\r\n")
    parent = "# Parent\n\nParent prose.\n\n## Child\n\nCHILD_MARKER "
    tail = "# Tail\n\nTAIL_MARKER " + "T" * 100
    filler_size = (
        parser.config.max_section_chars + 1 - len(preamble + "\n\n" + parent + "\n\n" + tail)
    )
    content = preamble + "\n\n" + parent + "C" * filler_size + "\n\n" + tail
    assert len(content) == parser.config.max_section_chars + 1

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]
    combined = "\n\n".join(op.content for op in writes)

    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    assert [block for op in writes for block in _code_blocks(op.content)] == _code_blocks(content)
    assert combined.index("CHILD_MARKER") < combined.index("TAIL_MARKER")


@pytest.mark.asyncio
@pytest.mark.parametrize("blank_preamble", ["   \n", "\t\n"])
async def test_blank_preamble_keeps_existing_layout(blank_preamble):
    parser = MarkdownParser()
    content = "# First\n\n" + "B" * 1400 + "\n\n# Other\n\n" + "C" * 1400
    control = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    actual = await parser._compute_layout(
        blank_preamble + content, "viking://temp/test", resource_name="manual"
    )

    assert [(op.kind, op.uri, op.content) for op in actual.ops] == [
        (op.kind, op.uri, op.content) for op in control.ops
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("indent", ["    ", "\t"])
async def test_tiny_preamble_merged_with_fitting_leaf_keeps_code(indent):
    parser = MarkdownParser()
    code = f"{indent}value_0 = 'first line'\n{indent}value_1 = 'second line'\n"
    leaf = "# Leaf\n\n" + "B" * 1950
    content = code + "\n" + leaf
    assert len(leaf) <= parser.config.max_section_chars < len(content)

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]

    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    assert [block for op in writes for block in _code_blocks(op.content)] == _code_blocks(content)


@pytest.mark.asyncio
async def test_later_character_slice_does_not_create_indented_code():
    parser = MarkdownParser()
    content = "A" * 2000 + "    continuation"
    assert _code_blocks(content) == []

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="manual")
    writes = [op for op in layout.ops if op.kind == "write"]

    assert all(len(op.content) <= parser.config.max_section_chars for op in writes)
    assert [block for op in writes for block in _code_blocks(op.content)] == []
