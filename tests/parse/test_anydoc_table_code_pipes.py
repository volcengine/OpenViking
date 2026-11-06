# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Preserve code and column contents in AnyDoc-produced Markdown tables."""

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from openviking.parse.parsers.anydoc_converter import AnyDocConverter

_CODE_PIPE_CASES = [
    ("a|b", r"`a\|b`"),
    ("|", r"`\|`"),
    (r"a\|b", r"`a\\|b`"),
    (r"a\\|b", r"`a\\\|b`"),
    ("`a|b`", r"`` `a\|b` ``"),
    ("左|右", r"`左\|右`"),
]


def _write_epub(path: Path, body: str) -> None:
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Table code</title></head>'
        f"<body>{body}</body></html>"
    )
    package = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="book-id">table-code</dc:identifier>'
        "<dc:title>Table code</dc:title><dc:language>en</dc:language></metadata>"
        '<manifest><item id="chapter" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="chapter"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", container, compress_type=ZIP_DEFLATED)
        archive.writestr("OPS/content.opf", package, compress_type=ZIP_DEFLATED)
        archive.writestr("OPS/chapter.xhtml", chapter, compress_type=ZIP_DEFLATED)


def _convert_epub(tmp_path: Path, body: str) -> str:
    source = tmp_path / "table.epub"
    _write_epub(source, body)
    result = AnyDocConverter().convert(
        source, resource_name="table-code", storage=None, format_hint="epub"
    )
    assert result.source_format == "epub"
    assert result.images_saved == 0
    assert result.assets_referenced == 0
    return result.markdown


def _table(cell: str) -> str:
    return (
        "<table><thead><tr><th>Left</th><th>Right</th></tr></thead>"
        f"<tbody><tr><td>{cell}</td><td>sentinel</td></tr></tbody></table>"
    )


def _code(text: str, kind: str) -> str:
    inline = f"<code>{escape(text)}</code>"
    return f"<pre>{inline}</pre>" if kind == "block" else inline


@pytest.mark.parametrize("kind", ["inline", "block"])
@pytest.mark.parametrize("text, expected", _CODE_PIPE_CASES)
def test_anydoc_table_preserves_code_pipes(
    tmp_path: Path, kind: str, text: str, expected: str
) -> None:
    markdown = _convert_epub(tmp_path, _table(_code(text, kind)))

    assert f"| {expected} | sentinel |" in markdown.splitlines()


@pytest.mark.parametrize("kind", ["inline", "block"])
def test_anydoc_table_code_without_pipes_is_unchanged(tmp_path: Path, kind: str) -> None:
    markdown = _convert_epub(tmp_path, _table(_code("plain", kind)))

    assert "| `plain` | sentinel |" in markdown.splitlines()


def test_anydoc_table_plain_text_pipe_is_unchanged(tmp_path: Path) -> None:
    markdown = _convert_epub(tmp_path, _table("a|b"))

    assert r"| a\|b | sentinel |" in markdown.splitlines()


@pytest.mark.parametrize("kind, expected", [("inline", "`a|b`"), ("block", "```\na|b\n```")])
def test_anydoc_code_outside_tables_is_unchanged(tmp_path: Path, kind: str, expected: str) -> None:
    markdown = _convert_epub(tmp_path, _code("a|b", kind))

    assert expected in markdown
    assert r"\|" not in markdown


@pytest.mark.parametrize(
    "markup, expected",
    [
        ("<ul><li><code>a|b</code></li></ul>", r"- `a\|b`"),
        ("<blockquote><p><code>a|b</code></p></blockquote>", r"`a\|b`"),
        ("<ul><li>a|b</li></ul>", r"- a\|b"),
        ("<blockquote><p>a|b</p></blockquote>", r"a\|b"),
        ("<ul><li><code>plain</code></li></ul>", "- `plain`"),
        ("<blockquote><p><code>plain</code></p></blockquote>", "`plain`"),
    ],
)
def test_anydoc_table_nested_blocks_preserve_pipes(
    tmp_path: Path, markup: str, expected: str
) -> None:
    markdown = _convert_epub(tmp_path, _table(markup))
    assert f"| {expected} | sentinel |" in markdown.splitlines()
