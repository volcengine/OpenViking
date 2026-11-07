"""Regression coverage for typed table rows, before Markdown syntax is emitted."""

from copy import deepcopy

import pytest

from openviking.parse.base import format_table_to_markdown
from openviking.parse.parsers.markdown import MarkdownParser


@pytest.mark.parametrize(
    "data_row",
    [["real", "value"], [0, False], ["symbol", r"\|"], ["markup", "&nbsp;"]],
    ids=["text", "zero-false", "escaped-pipe", "html-entity"],
)
def test_formatter_omits_trailing_empty_data_rows(data_row):
    rows = [["Name", "Value"], data_row]
    assert format_table_to_markdown([*rows, *([["", "  \t"]] * 120)]) == format_table_to_markdown(
        rows
    )


def test_formatter_keeps_empty_rows_between_values():
    rows = [["Name", "Value"], ["first", "value"], ["", ""], ["last", "value"]]
    assert format_table_to_markdown([*rows, *([["", ""]] * 120)]) == format_table_to_markdown(rows)


def test_formatter_keeps_entirely_empty_tables():
    rows = [["Name", "Value"], *([["", ""]] * 120)]
    assert len(format_table_to_markdown(rows).splitlines()) == 122


def test_formatter_does_not_modify_input_rows():
    rows = [["Name", "Value"], ["real", "value"], ["", ""]]
    original = deepcopy(rows)
    format_table_to_markdown(rows)
    assert rows == original


def test_formatter_keeps_header_only_tables():
    assert format_table_to_markdown([["Name", "Value"]]) == "| Name | Value |"


def test_formatter_omits_trailing_empty_rows_without_header():
    rows = [["real", "value"]]
    assert format_table_to_markdown(
        [*rows, ["", ""]], has_header=False
    ) == format_table_to_markdown(rows, has_header=False)


def test_formatted_table_avoids_content_free_chunks():
    rows = [["Name", "Value"], ["real", "value"]]
    markdown = format_table_to_markdown([*rows, *([["", ""]] * 120)])
    assert MarkdownParser()._smart_split_content(markdown, max_size=100) == [
        format_table_to_markdown(rows)
    ]


@pytest.mark.parametrize("indent", ["    ", "\t"], ids=["spaces", "tab"])
def test_markdown_literal_pipe_lines_remain_in_indented_code(indent):
    header = f"{indent}| Name | Value |\n{indent}| --- | --- |"
    content = "\n".join([header, f"{indent}| literal | code |", *([f"{indent}|   |   |"] * 120)])
    parts = MarkdownParser()._smart_split_content(content, max_size=100)
    assert (
        sum(
            bool(line.strip()) and not line.replace("|", "").strip()
            for part in parts
            for line in part.splitlines()
        )
        == 120
    )


@pytest.mark.parametrize("fence", ["```text\n", "~~~text\n"], ids=["backticks", "tildes"])
def test_markdown_literal_pipe_lines_remain_in_unclosed_fences(fence):
    header = "| Name | Value |\n| --- | --- |"
    content = fence + "\n".join([header, "| literal | code |", *(["|   |   |"] * 120)])
    parts = MarkdownParser()._smart_split_content(content, max_size=100)
    assert (
        sum(
            bool(line.strip()) and not line.replace("|", "").strip()
            for part in parts
            for line in part.splitlines()
        )
        == 120
    )


def test_formatter_preserves_columns_from_trailing_empty_rows():
    rows = [["Name", "Value"], ["real", "value"], ["", "", ""]]
    lines = format_table_to_markdown(rows).splitlines()
    assert all(len(line.split("|")) == 5 for line in lines)
    assert len(lines) == 3
