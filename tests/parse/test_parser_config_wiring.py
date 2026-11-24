"""Verify that the parser-specific character limit controls stored Markdown."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.parser_config import HTMLConfig, PDFConfig
from openviking_cli.utils.storage import StoragePath


@pytest.fixture
def mineru_transport(monkeypatch):
    client = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
        )

    return install


def write_text_pdf(path: Path, lines: list[str]) -> None:
    stream = (
        "BT /F1 12 Tf 20 TL 50 750 Td " + " T* ".join(f"({line}) Tj" for line in lines) + " ET"
    ).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload += b"%d 0 obj\n%s\nendobj\n" % (index, obj)
    xref = len(payload)
    payload += b"xref\n0 %d\n0000000000 65535 f \n" % len(offsets)
    for offset in offsets[1:]:
        payload += b"%010d 00000 n \n" % offset
    payload += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(offsets),
        xref,
    )
    path.write_bytes(payload)


@pytest.mark.parametrize(
    ("format_name", "config"),
    [
        ("pdf", PDFConfig(strategy="local", max_section_chars=160)),
        ("html", HTMLConfig(max_section_chars=160)),
    ],
)
async def test_registry_writes_document_sections_using_format_specific_limit(
    tmp_path, format_name, config
):
    source = tmp_path / f"report.{format_name}"
    lines = [f"Paragraph {i} describes the import process in OpenViking." for i in range(8)]
    if format_name == "pdf":
        write_text_pdf(source, lines)
    else:
        source.write_text("<article>" + "".join(f"<p>{line}</p>" for line in lines) + "</article>")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    result = await ParserRegistry({format_name: config}).parse(source, parse_output_store=store)

    assert result.source_format == format_name
    assert result.artifact_ref is not None
    files = sorted(Path(result.artifact_ref.root).rglob("*.md"))
    bodies = [file.read_text(encoding="utf-8") for file in files]
    assert len(bodies) > 1
    assert all(len(body) <= 160 for body in bodies)
    rendered = "".join(bodies).removeprefix("<!-- Page 1 -->")
    assert "".join(rendered.split()) == "".join("".join(lines).split())


async def test_registry_writes_mineru_v1_pdf(tmp_path, monkeypatch, mineru_transport):
    source = tmp_path / "upload.pdf"
    write_text_pdf(source, ["Local text"])
    media = StoragePath(tmp_path / "storage")
    monkeypatch.setattr("openviking_cli.utils.storage._default_storage", media)
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    image = io.BytesIO()
    Image.new("RGB", (32, 32)).save(image, format="PNG")
    archive = io.BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("markdown.md", "# MinerU output\n\n![Diagram](images/diagram.png)\n")
        output.writestr("images/diagram.png", image.getvalue())

    async def handler(request):
        if request.method == "GET" and request.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={"status": "ok", "version": "4.0.12", "features": {"output_formats": ["zip"]}},
            )
        if request.method == "POST" and request.url.path == "/v1/file_parse":
            assert request.url.params.get_list("output_formats") == ["zip"]
            assert request.url.params["response_format"] == "zip"
            body = await request.aread()
            assert b'name="files"; filename="upload.pdf"' in body
            assert source.read_bytes() in body
            assert b"return_images" not in body
            return httpx.Response(200, content=archive.getvalue())
        return httpx.Response(404, json={"detail": "Not Found"})

    mineru_transport(handler)
    config = PDFConfig.from_dict({"strategy": "mineru", "mineru_endpoint": "http://mineru.test"})
    result = await ParserRegistry({"pdf": config}).parse(
        source, resource_name="Report.pdf", parse_output_store=store
    )

    assert not result.warnings, result.warnings
    assert result.artifact_ref is not None
    assert result.meta["intermediate_markdown_preview"] == (
        "# MinerU output\n\n![Diagram](Report.pdf/images/diagram.png)\n"
    )
    assert b"# MinerU output" in await store.read_bytes(
        result.artifact_ref, "Report.pdf/Report.pdf.md"
    )
    assert result.meta["images_saved"] == 1
    for root in (media.media_dir, Path(result.artifact_ref.root)):
        assert [path.read_bytes() for path in root.rglob("diagram.png")] == [image.getvalue()]


async def test_registry_falls_back_to_mineru_v1_markdown(tmp_path, mineru_transport):
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"PDF text unreadable by the local parser")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    async def handler(request):
        route = (request.method, request.url.path)
        if route == ("GET", "/v1/health"):
            return httpx.Response(200, json={"status": "healthy", "version": "4.0.12"})
        if route == ("POST", "/v1/file_parse"):
            assert request.url.params.get_list("output_formats") == ["markdown"]
            assert request.url.params["response_format"] == "json"
            return httpx.Response(202, json={"task_id": "task_1", "status": "running"})
        if route == ("GET", "/v1/tasks/task_1/result"):
            return httpx.Response(
                200,
                json={
                    "task_id": "task_1",
                    "status": "completed",
                    "files": [{"content": {"markdown": "Parsed after local failure.\n"}}],
                },
            )
        return httpx.Response(404)

    mineru_transport(handler)
    config = PDFConfig(
        mineru_endpoint="http://mineru.test",
        mineru_output_formats=["markdown"],
    )
    result = await ParserRegistry({"pdf": config}).parse(source, parse_output_store=store)

    assert not result.warnings, result.warnings
    assert result.meta["task_id"] == "task_1"
    assert result.meta["intermediate_markdown_preview"] == "Parsed after local failure.\n"


async def test_registry_writes_mineru_v41_task_result(tmp_path, mineru_transport):
    source = tmp_path / "upload.pdf"
    write_text_pdf(source, ["Local text"])
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    markdown = b"Parsed using a MinerU task.\n"
    archive = io.BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("markdown.md", markdown)

    async def handler(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        route = (request.method, request.url.path)
        if route == ("GET", "/proxy/v1/health"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "version": "4.1.0",
                    "features": {"task_output_formats": ["default", "docx"]},
                },
            )
        if route == ("POST", "/proxy/v1/tasks"):
            assert json.loads(await request.aread()) == {
                "files": [{"file_name": source.name, "bytes": source.stat().st_size}],
                "tier": "basic",
                "ocr_mode": "ocr",
                "output_formats": ["default"],
            }
            return httpx.Response(
                202,
                json={
                    "task_id": "task_1",
                    "files": [
                        {
                            "file_id": "file_1",
                            "status": "waiting-file",
                            "upload": {
                                "url": "http://mineru.test/proxy/v1/tasks/task_1/uploads/file_1"
                            },
                        }
                    ],
                },
            )
        if route == ("PUT", "/proxy/v1/tasks/task_1/uploads/file_1"):
            assert request.headers["Content-Type"] == "application/octet-stream"
            assert await request.aread() == source.read_bytes()
            return httpx.Response(200, json={"status": "running"})
        if route == ("GET", "/proxy/v1/tasks/task_1"):
            return httpx.Response(
                200, json={"files": [{"file_id": "file_1", "status": "completed"}]}
            )
        if route == ("GET", "/proxy/v1/files/file_1/content"):
            return httpx.Response(200, content=archive.getvalue())
        return httpx.Response(404)

    mineru_transport(handler)
    config = OpenVikingConfig.from_dict(
        {
            "storage": {"workspace": str(tmp_path)},
            "parsers": {
                "pdf": {
                    "strategy": "mineru",
                    "mineru_endpoint": "http://mineru.test/proxy/v1/",
                    "mineru_api_mode": "v1",
                    "mineru_token": "test-token",
                    "mineru_tier": "basic",
                    "mineru_ocr_mode": "ocr",
                    "mineru_output_formats": ["markdown"],
                }
            },
        }
    )
    result = await ParserRegistry({"pdf": config.pdf}).parse(source, parse_output_store=store)

    assert not result.warnings, result.warnings
    assert result.meta["task_id"] == "task_1"
    assert result.meta["api_version"] == "4.1.0"
    assert result.meta["intermediate_markdown_preview"] == markdown.decode()


@pytest.mark.parametrize("strategy", ["mineru", "auto"])
async def test_registry_preserves_legacy_and_local_first_pdf(tmp_path, mineru_transport, strategy):
    source = tmp_path / "upload.pdf"
    write_text_pdf(source, ["Local text"])
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    async def handler(request):
        assert strategy == "mineru", "PDF strategy auto must prefer successful local parsing"
        if request.url.path == "/v1/health":
            return httpx.Response(404)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy", "protocol_version": 2})
        assert request.method == "POST"
        assert request.url.path == "/file_parse"
        body = await request.aread()
        assert b'name="files"; filename="upload.pdf"' in body
        assert b'name="backend"\r\n\r\nhybrid-auto-engine' in body
        assert b'name="return_images"\r\n\r\ntrue' in body
        return httpx.Response(
            200, json={"status": "completed", "results": {"upload": {"md_content": "Legacy text"}}}
        )

    mineru_transport(handler)
    config = PDFConfig(
        strategy=strategy,
        mineru_endpoint="http://mineru.test/file_parse",
        mineru_bodys={"backend": "hybrid-auto-engine"},
    )
    result = await ParserRegistry({"pdf": config}).parse(source, parse_output_store=store)

    assert not result.warnings, result.warnings
    assert result.artifact_ref is not None
    expected = "Legacy text" if strategy == "mineru" else "Local text"
    assert expected in result.meta["intermediate_markdown_preview"]


def test_mineru_v1_config_rejects_legacy_form_fields():
    config = PDFConfig(mineru_api_mode="v1", mineru_bodys={"table_enable": True})
    with pytest.raises(ValueError, match="mineru_bodys is only supported by legacy MinerU"):
        ParserRegistry({"pdf": config})
