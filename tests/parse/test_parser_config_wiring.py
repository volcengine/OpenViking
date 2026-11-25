"""Verify that the parser-specific character limit controls stored Markdown."""

from pathlib import Path

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.parser_config import HTMLConfig, PDFConfig


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
