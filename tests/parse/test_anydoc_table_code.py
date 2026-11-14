# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from zipfile import ZipFile

from openviking.parse.parsers.anydoc_converter import AnyDocConverter


def test_epub_table_preserves_pipes_in_inline_and_block_code(tmp_path):
    book = tmp_path / "table.epub"
    with ZipFile(book, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
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
                <dc:identifier id="id">table</dc:identifier>
                <dc:title>Table</dc:title>
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
            r"""<html xmlns="http://www.w3.org/1999/xhtml">
              <head><title>Table</title></head>
              <body>
                <table>
                  <thead><tr><th>Expression</th><th>Meaning</th></tr></thead>
                  <tbody>
                    <tr><td><code>a|b</code></td><td>choice</td></tr>
                    <tr><td><pre><code>left|right</code></pre></td><td>pipeline</td></tr>
                    <tr><td><code>a\|b</code></td><td>literal backslash</td></tr>
                  </tbody>
                </table>
                <p><code>c|d</code></p>
              </body>
            </html>""",
        )

    result = AnyDocConverter().convert(book, resource_name="table", storage=SimpleNamespace())
    lines = result.markdown.splitlines()

    assert r"| `a\|b` | choice |" in lines
    assert r"| `left\|right` | pipeline |" in lines
    assert r"| `a\\|b` | literal backslash |" in lines
    assert "`c|d`" in lines
