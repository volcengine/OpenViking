# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from zipfile import ZIP_STORED, ZipFile

import pytest
from markdown_it import MarkdownIt

from openviking.parse.parsers.anydoc_converter import AnyDocConverter


@pytest.mark.parametrize("separator", ["", "<p>Follow the next procedure.</p>"])
def test_epub_preserves_separate_ordered_list_starts(tmp_path, separator):
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
              <body>
                <ol><li>Connect power</li><li>Turn on controller</li></ol>
                {separator}
                <ol start="5"><li>Check lights</li><li>Record reading</li></ol>
              </body>
            </html>""",
        )

    result = AnyDocConverter().convert(book, resource_name="lists", storage=None, format_hint="epub")
    tokens = MarkdownIt("commonmark").parse(result.markdown)
    starts = [
        int(token.attrGet("start") or 1) for token in tokens if token.type == "ordered_list_open"
    ]
    assert starts == [1, 5]
