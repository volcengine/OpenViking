# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Preserve the first data row of an AnyDoc table that has no header row.

anydoc reports ``header_rows == 0`` for tables that carry no ``<thead>`` while
using structural markup such as ``colspan``/``rowspan`` or a nested table in a
cell. The renderer used to emit an empty ``|  |  |`` placeholder header above
such tables, which shifted every data row down and left the real first row out
of the rendered body. The first data row must instead be promoted to the header.
"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from openviking.parse.parsers.anydoc_converter import AnyDocConverter


def _write_epub(path: Path, body: str) -> None:
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Table headerless</title></head>'
        f"<body>{body}</body></html>"
    )
    package = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="book-id">table-headerless</dc:identifier>'
        "<dc:title>Table headerless</dc:title><dc:language>en</dc:language></metadata>"
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
        source, resource_name="table-headerless", storage=None, format_hint="epub"
    )
    assert result.source_format == "epub"
    assert result.images_saved == 0
    assert result.assets_referenced == 0
    return result.markdown


def test_anydoc_colspan_table_without_header_promotes_first_row(tmp_path: Path) -> None:
    markdown = _convert_epub(
        tmp_path,
        '<table><tr><td colspan="2">Merged</td><td>C</td></tr>'
        "<tr><td>a</td><td>b</td><td>c</td></tr></table>",
    )

    lines = markdown.splitlines()
    # No empty `|  |  |` placeholder header may precede the table.
    assert "|  |  |  |" not in lines
    assert "| Merged |  | C |" in lines
    assert "| a | b | c |" in lines
    # The promoted first row sits directly on top of the separator row.
    merged_idx = lines.index("| Merged |  | C |")
    assert lines[merged_idx + 1] == "| --- | --- | --- |"


def test_anydoc_nested_table_cell_without_header_promotes_first_row(tmp_path: Path) -> None:
    markdown = _convert_epub(
        tmp_path,
        "<table><tr><td><table><tr><td>x</td><td>y</td></tr>"
        "<tr><td>p</td><td>q</td></tr></table></td><td>out</td></tr></table>",
    )

    lines = markdown.splitlines()
    assert "|  |  |" not in lines
    assert "| x / y<br>p / q | out |" in lines
