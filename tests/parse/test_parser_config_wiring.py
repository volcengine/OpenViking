"""Verify how the parser registry applies PDF and HTML configuration."""

import io
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.parser_config import HTMLConfig, PDFConfig
from openviking_cli.utils.storage import StoragePath


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
        (
            "pdf",
            PDFConfig(
                mineru_endpoint="https://mineru.example/api/v4",
                mineru_token="test-token",
                max_section_chars=160,
            ),
        ),
        ("html", HTMLConfig(max_section_chars=160)),
    ],
)
async def test_registry_applies_parser_config_when_storing_document_sections(
    tmp_path, monkeypatch, format_name, config
):
    source = tmp_path / f"report.{format_name}"
    lines = [f"Paragraph {i} describes the import process in OpenViking." for i in range(8)]
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    registry = ParserRegistry({format_name: config})
    if format_name == "pdf":
        write_text_pdf(source, lines)
        image = io.BytesIO()
        Image.new("RGB", (32, 32)).save(image, format="PNG")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as result_zip:
            result_zip.writestr("full.md", "Hosted result\n\n![Figure](images/figure.png)")
            result_zip.writestr("images/figure.png", image.getvalue())
        mineru_available = True

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host == "files.example":
                assert "Authorization" not in request.headers
                if request.method == "PUT":
                    assert "Content-Type" not in request.headers
                    assert request.content == source.read_bytes()
                    return httpx.Response(200)
                return httpx.Response(200, content=archive.getvalue())

            assert request.headers["Authorization"] == "Bearer test-token"
            if request.url.path.endswith("/file-urls/batch"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"batch_id": "batch", "file_urls": ["https://files.example/pdf"]},
                    },
                )
            assert request.url.path == "/api/v4/extract-results/batch/batch"
            if not mineru_available:
                return httpx.Response(503)
            entry = {"state": "done", "full_zip_url": "https://files.example/result.zip"}
            return httpx.Response(200, json={"code": 0, "data": {"extract_result": [entry]}})

        transport = httpx.MockTransport(respond)
        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kw: client_type(transport=transport, **kw)
        )
        monkeypatch.setattr("openviking_cli.utils.storage._default_storage", StoragePath(tmp_path))
        hosted = await registry.parse(source, parse_output_store=store)
        assert hosted.meta["pdf_strategy_effective"] == "mineru"
        assert hosted.artifact_ref is not None
        hosted_root = Path(hosted.artifact_ref.root)
        assert any("Hosted result" in file.read_text() for file in hosted_root.rglob("*.md"))
        assert next(hosted_root.rglob("figure.png")).read_bytes() == image.getvalue()

        mineru_available = False
    else:
        source.write_text("<article>" + "".join(f"<p>{line}</p>" for line in lines) + "</article>")

    result = await registry.parse(source, parse_output_store=store)

    if format_name == "pdf":
        assert result.meta["pdf_strategy_effective"] == "local"
    assert result.source_format == format_name
    assert result.artifact_ref is not None
    files = sorted(Path(result.artifact_ref.root).rglob("*.md"))
    bodies = [file.read_text(encoding="utf-8") for file in files]
    assert len(bodies) > 1
    assert all(len(body) <= 160 for body in bodies)
    rendered = "".join(bodies).removeprefix("<!-- Page 1 -->")
    assert "".join(rendered.split()) == "".join("".join(lines).split())
