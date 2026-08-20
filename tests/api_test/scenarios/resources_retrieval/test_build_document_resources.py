import os
import shutil
import tempfile
import uuid
import zipfile

from build_test_helpers import (
    assert_content_no_html_tags,
    assert_resource_indexed,
    assert_root_uri_valid,
    assert_source_format,
)


def _create_html_file():
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

    return html_path, temp_dir, unique_keyword


def _create_pptx_file():
    random_id = str(uuid.uuid4())[:8]
    unique_keyword = f"pptx_keyword_{random_id}"
    temp_dir = tempfile.mkdtemp()
    pptx_path = os.path.join(temp_dir, f"test_{random_id}.pptx")

    with zipfile.ZipFile(pptx_path, "w", zipfile.ZIP_DEFLATED) as pptx:
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
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>PPT测试标题 {random_id}</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>包含唯一关键词：{unique_keyword}</a:t></a:r></a:p><a:p><a:r><a:t>用于验证PPT文件构建产物。</a:t></a:r></a:p></p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>""",
        )

    return pptx_path, temp_dir, unique_keyword


class TestBuildDocumentResources:
    """TC-B04, B07 文档类资源构建测试（快速用例，≤20s）"""

    def test_build_html_file(self, api_client):
        """TC-B04 HTML文件构建：验证 .html 文件添加后 source_format=html 且标签被剥离"""
        html_path, temp_dir, unique_keyword = _create_html_file()
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

    def test_build_pptx_file(self, api_client):
        """TC-B07 PowerPoint构建：验证 .pptx 文件添加后 source_format=pptx 且内容可检索"""
        pptx_path, temp_dir, unique_keyword = _create_pptx_file()
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
