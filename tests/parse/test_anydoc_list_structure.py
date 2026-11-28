# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from zipfile import ZIP_STORED, ZipFile

import pytest
from markdown_it import MarkdownIt

from openviking.parse.parsers.anydoc_converter import AnyDocConverter


@pytest.mark.parametrize(
    ("attributes", "label"),
    [
        ('type="i" start="2"', "ii. "),
        ('type="I" start="3"', "III. "),
        ('type="a" start="27"', "aa. "),
        ('start="12"', ""),
    ],
)
def test_epub_ordered_list_preserves_continuation_blocks(tmp_path, attributes, label):
    book = tmp_path / "lists.epub"
    with ZipFile(book, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles>
                <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>""",
        )
        archive.writestr(
            "content.opf",
            """<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
                        unique-identifier="id">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:identifier id="id">lists</dc:identifier>
                <dc:title>Lists</dc:title>
                <dc:language>en</dc:language>
              </metadata>
              <manifest>
                <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
              <spine><itemref idref="chapter"/></spine>
            </package>""",
        )
        archive.writestr(
            "chapter.xhtml",
            f"""<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Lists</title></head>
              <body><ol {attributes}>
                <li>
                  <p>First paragraph.</p><p>Second paragraph.</p>
                  <blockquote><p>Quoted paragraph.</p></blockquote>
                  <ul><li>Nested item.</li></ul>
                </li>
                <li>Next item.</li>
              </ol></body>
            </html>""",
        )

    result = AnyDocConverter().convert(book, resource_name="lists", storage=None, format_hint="epub")
    tokens = MarkdownIt("commonmark").parse(result.markdown)

    assert not any(t.type == "code_block" for t in tokens)
    assert any(t.type == "inline" and t.content == label + "First paragraph." for t in tokens)
    assert any(
        t.type == "inline" and t.level == 3 and t.content == "Second paragraph." for t in tokens
    )
    assert any(t.type == "blockquote_open" and t.level == 2 for t in tokens)
    assert any(t.type == "bullet_list_open" and t.level == 2 for t in tokens)
