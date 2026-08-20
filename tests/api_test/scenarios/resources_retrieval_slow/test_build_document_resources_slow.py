import os
import shutil
import tempfile
import uuid
import zipfile

import pytest
from build_test_helpers import (
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
)


def _create_pdf_file():
    try:
        from fpdf import FPDF
    except ImportError:
        pytest.skip("fpdf 未安装，跳过 PDF 构建测试")

    random_id = str(uuid.uuid4())[:8]
    unique_keyword = f"pdf_keyword_{random_id}"
    temp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, f"test_{random_id}.pdf")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(200, 10, text=f"PDF Test Document {random_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(200, 10, text=f"Unique keyword: {unique_keyword}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        200, 10, text="This is a test PDF file for build validation.", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.output(pdf_path)

    return pdf_path, temp_dir, unique_keyword


def _create_docx_file():
    random_id = str(uuid.uuid4())[:8]
    unique_keyword = f"docx_keyword_{random_id}"
    temp_dir = tempfile.mkdtemp()
    docx_path = os.path.join(temp_dir, f"test_{random_id}.docx")

    _write_minimal_docx(
        docx_path,
        [
            f"Word测试标题 {random_id}",
            f"包含唯一关键词：{unique_keyword}",
            "用于验证Word文档构建产物。",
        ],
    )

    return docx_path, temp_dir, unique_keyword


def _create_xlsx_file():
    random_id = str(uuid.uuid4())[:8]
    unique_keyword = f"xlsx_keyword_{random_id}"
    temp_dir = tempfile.mkdtemp()
    xlsx_path = os.path.join(temp_dir, f"test_{random_id}.xlsx")

    _write_minimal_xlsx(
        xlsx_path,
        [
            ["Column A", "Column B", "Column C"],
            [f"数据1 {random_id}", unique_keyword, "数据3"],
            ["数据4", "数据5", "数据6"],
        ],
    )

    return xlsx_path, temp_dir, unique_keyword


def _xml_escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _write_minimal_docx(path, paragraphs):
    body = "".join(
        f"<w:p><w:r><w:t>{_xml_escape(paragraph)}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        docx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        docx.writestr(
            "word/document.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{body}<w:sectPr/></w:body>
</w:document>""",
        )


def _write_minimal_xlsx(path, rows):
    row_xml = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row):
            col = chr(ord("A") + col_idx)
            cells.append(
                f'<c r="{col}{row_idx}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        xlsx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        xlsx.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="TestSheet" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        xlsx.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        xlsx.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>{"".join(row_xml)}</sheetData>
</worksheet>""",
        )


def _write_minimal_pptx(path, title, paragraphs):
    paragraph_xml = "".join(
        f"<a:p><a:r><a:t>{_xml_escape(paragraph)}</a:t></a:r></a:p>" for paragraph in paragraphs
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as pptx:
        pptx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>""",
        )
        pptx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        )
        pptx.writestr(
            "ppt/presentation.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
</p:presentation>""",
        )
        pptx.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>""",
        )
        pptx.writestr(
            "ppt/slides/slide1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{_xml_escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/>{paragraph_xml}</p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>""",
        )


def _write_minimal_epub(path, title, html_content):
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        epub.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="book-id" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{_xml_escape(title)}</dc:identifier>
    <dc:title>{_xml_escape(title)}</dc:title>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        epub.writestr("OEBPS/chapter.xhtml", html_content)


class TestBuildDocumentResourcesSlow:
    """TC-B03~B09 文档类资源构建测试"""

    def test_build_pdf_file(self, api_client):
        """TC-B03 PDF文件构建：验证 .pdf 文件添加后 source_format=pdf 且内容可检索"""
        pdf_path, temp_dir, unique_keyword = _create_pdf_file()
        try:
            response = api_client.add_resource(path=pdf_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["pdf", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B03 PDF文件构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_docx_file(self, api_client):
        """TC-B05 Word文档构建：验证 .docx 文件添加后 source_format=docx 且内容可检索"""
        docx_path, temp_dir, unique_keyword = _create_docx_file()
        try:
            response = api_client.add_resource(path=docx_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["docx", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B05 Word文档构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_xlsx_file(self, api_client):
        """TC-B08 Excel构建：验证 .xlsx 文件添加后 source_format=xlsx 且表格数据可检索"""
        xlsx_path, temp_dir, unique_keyword = _create_xlsx_file()
        try:
            response = api_client.add_resource(path=xlsx_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["xlsx", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B08 Excel构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_html_file(self, api_client):
        """TC-B04 HTML文件构建：验证 .html 文件添加后 source_format=html 且标签被剥离"""
        from build_test_helpers import assert_content_no_html_tags

        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"html_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()
        html_path = os.path.join(temp_dir, f"test_{random_id}.html")

        content = (
            f"<html><head><title>HTML Test {random_id}</title></head>"
            f"<body><h1>HTML测试标题 {random_id}</h1>"
            f"<p>包含唯一关键词：{unique_keyword}</p>"
            f"<p>用于验证HTML文件构建产物。</p>"
            f"</body></html>"
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)

        try:
            response = api_client.add_resource(path=html_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["html", "markdown"])

            assert_content_no_html_tags(api_client, root_uri)

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B04 HTML文件构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_doc_file(self, api_client):
        """TC-B06 Word .doc 构建：验证 .doc 文件添加后 source_format=doc 且内容可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"doc_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()
        doc_path = os.path.join(temp_dir, f"test_{random_id}.doc")

        _write_minimal_docx(
            doc_path,
            [f"Word .doc 测试标题 {random_id}", f"包含唯一关键词：{unique_keyword}"],
        )

        try:
            response = api_client.add_resource(path=doc_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["doc", "docx", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B06 Word .doc 构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_pptx_file(self, api_client):
        """TC-B07 PowerPoint构建：验证 .pptx 文件添加后 source_format=pptx 且内容可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"pptx_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()
        pptx_path = os.path.join(temp_dir, f"test_{random_id}.pptx")

        _write_minimal_pptx(
            pptx_path,
            f"PPT测试标题 {random_id}",
            [f"包含唯一关键词：{unique_keyword}", "用于验证PPT文件构建产物。"],
        )

        try:
            response = api_client.add_resource(path=pptx_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["pptx", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B07 PowerPoint构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_epub_file(self, api_client):
        """TC-B09 EPUB构建：验证 .epub 文件添加后 source_format=epub 且内容可检索"""
        random_id = str(uuid.uuid4())[:8]
        unique_keyword = f"epub_keyword_{random_id}"
        temp_dir = tempfile.mkdtemp()
        epub_path = os.path.join(temp_dir, f"test_{random_id}.epub")

        _write_minimal_epub(
            epub_path,
            f"EPUB测试 {random_id}",
            f"<html><body><h1>EPUB测试章节 {random_id}</h1>"
            f"<p>包含唯一关键词：{unique_keyword}</p>"
            f"<p>用于验证EPUB文件构建产物。</p>"
            f"</body></html>",
        )

        try:
            response = api_client.add_resource(path=epub_path, wait=True)
            assert response.status_code == 200

            data = response.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            root_uri = result.get("root_uri")
            assert_root_uri_valid(root_uri)

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200

            assert_source_format(api_client, root_uri, ["epub", "markdown"])

            assert_resource_indexed(api_client, root_uri, unique_keyword)

            print(f"✓ TC-B09 EPUB构建通过, root_uri: {root_uri}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
