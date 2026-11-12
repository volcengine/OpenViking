# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keep native document payload characters distinct from Markdown line boundaries."""

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from openviking.parse.parsers.anydoc_converter import AnyDocConverter


def _write_epub(path: Path, body: str) -> None:
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>Unicode data</title></head><body>"
        f"{body}</body></html>"
    )
    package = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="book-id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="book-id">unicode-data</dc:identifier>'
        "<dc:title>Unicode data</dc:title><dc:language>en</dc:language>"
        '</metadata><manifest><item id="chapter" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="chapter"/></spine></package>'
    )
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        for name, content in (
            ("META-INF/container.xml", container),
            ("OPS/content.opf", package),
            ("OPS/chapter.xhtml", chapter),
        ):
            archive.writestr(name, content, compress_type=ZIP_DEFLATED)


def _write_footnote_docx(path: Path, text: str) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    document = (
        f'<w:document xmlns:w="{word}"><w:body><w:p>'
        "<w:r><w:t>Note</w:t></w:r>"
        '<w:r><w:footnoteReference w:id="1"/></w:r></w:p></w:body></w:document>'
    )
    notes = (
        f'<w:footnotes xmlns:w="{word}"><w:footnote w:id="1"><w:p><w:r>'
        f'<w:t xml:space="preserve">{escape(text)}</w:t>'
        "</w:r></w:p></w:footnote></w:footnotes>"
    )
    package = (
        f'<Relationships xmlns="{rels}"><Relationship Id="doc" '
        f'Type="{office}officeDocument" Target="word/document.xml"/></Relationships>'
    )
    doc_rels = (
        f'<Relationships xmlns="{rels}"><Relationship Id="note" '
        f'Type="{office}footnotes" Target="footnotes.xml"/></Relationships>'
    )
    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/footnotes.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.footnotes+xml"/></Types>'
    )
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", types),
            ("_rels/.rels", package),
            ("word/document.xml", document),
            ("word/_rels/document.xml.rels", doc_rels),
            ("word/footnotes.xml", notes),
        ):
            archive.writestr(name, content)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"], ids=["LS", "PS", "NEL"])
@pytest.mark.parametrize(
    "wrapper",
    [
        "{}",
        "<blockquote>{}</blockquote>",
        "<ul><li>{}</li></ul>",
        "<table><tr><th>Value</th></tr><tr><td>{}</td></tr></table>",
    ],
    ids=["direct", "quote", "list", "table"],
)
def test_epub_code_preserves_unicode_payload(tmp_path: Path, separator: str, wrapper: str):
    pytest.importorskip("anydoc")
    expected = 'value = "left' + separator + 'right"'
    source = tmp_path / "unicode-code.epub"
    _write_epub(source, wrapper.format("<pre><code>" + escape(expected) + "</code></pre>"))

    result = AnyDocConverter().convert(
        source, resource_name="unicode", storage=None, format_hint="epub"
    )

    assert expected in result.markdown


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", " "], ids=["LS", "PS", "ordinary"])
def test_docx_footnote_preserves_unicode_payload(tmp_path: Path, separator: str):
    pytest.importorskip("anydoc")
    expected = "left" + separator + "right"
    source = tmp_path / "unicode-note.docx"
    _write_footnote_docx(source, expected)

    result = AnyDocConverter().convert(
        source, resource_name="notes", storage=None, format_hint="docx"
    )

    assert "[^1]" in result.markdown
    assert expected in result.markdown


@pytest.mark.parametrize(
    ("wrapper", "expected"),
    [
        ("{}", "alpha\nbeta"),
        ("<blockquote>{}</blockquote>", "> alpha\n> beta"),
        ("<ul><li>{}</li></ul>", "  alpha\n  beta"),
        (
            "<table><tr><th>Value</th></tr><tr><td>{}</td></tr></table>",
            "alpha<br>beta",
        ),
    ],
    ids=["direct", "quote", "list", "table"],
)
def test_epub_actual_newlines_keep_existing_framing(tmp_path: Path, wrapper: str, expected: str):
    pytest.importorskip("anydoc")
    source = tmp_path / "multiline-code.epub"
    _write_epub(source, wrapper.format("<pre><code>alpha\nbeta\n\n</code></pre>"))

    result = AnyDocConverter().convert(
        source, resource_name="multiline", storage=None, format_hint="epub"
    )

    assert expected in result.markdown
