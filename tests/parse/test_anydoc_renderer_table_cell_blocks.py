# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract test for table cells holding display math in AnyDoc documents.

A DOCX table cell can contain a display formula (``m:oMathPara``), which AnyDoc
surfaces as a block-level ``math`` block. Rendering it used to raise
``RuntimeError: Unsupported AnyDoc table-cell block kind: math``, which aborted
the whole document parse. The formula must instead render and the rest of the
document must survive.
"""

import zipfile
from pathlib import Path

from openviking.parse.parsers.anydoc_converter import AnyDocConverter

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
<w:body>
<w:p><w:r><w:t>Before</w:t></w:r></w:p>
<w:tbl>
<w:tr><w:tc><w:p><w:t>a/b</w:t></w:p></w:tc>
<w:tc><w:p><m:oMathPara><m:oMath>
<m:r><m:t>x = </m:t></m:r>
<m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f>
</m:oMath></m:oMathPara></w:p></w:tc></w:tr>
</w:tbl>
</w:body>
</w:document>"""


def _write_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", _DOCUMENT)


def test_display_math_in_table_cell_renders(tmp_path: Path):
    source = tmp_path / "repro.docx"
    _write_docx(source)

    result = AnyDocConverter().convert(source, resource_name="repro.docx", storage=None)

    assert "\\frac{a}{b}" in result.markdown
    assert "Before" in result.markdown
