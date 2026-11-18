"""Preserve source text in Markdown artifacts from PDF tables."""

from pathlib import Path

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig


def _table_pdf(backslashes: int) -> bytes:
    """Generate a one-page vector PDF with two ruled table rows."""
    body = (
        b"20 20 m 220 20 l 220 80 l 20 80 l h S\n"
        b"20 50 m 220 50 l S 120 20 m 120 80 l S\n"
        b"BT /F1 10 Tf 25 62 Td (name) Tj 100 0 Td (value) Tj ET\n"
        b"BT /F1 10 Tf 25 32 Td (left "
        + b"\\134" * backslashes
        + b"| right) Tj 100 0 Td (kept) Tj ET\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 250 100] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(body)} >>\nstream\n".encode() + body + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    data.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets))
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("backslashes", [0, 1, 2])
async def test_pdf_table_artifact_preserves_literal_pipe_and_backslashes(tmp_path, backslashes):
    path = tmp_path / "synthetic-table.pdf"
    path.write_bytes(_table_pdf(backslashes))
    parser = PDFParser(config=PDFConfig(strategy="local"))
    result = await parser.parse(
        path,
        split_content=False,
        parse_output_store=LocalParseOutputStore(str(tmp_path / "artifacts")),
    )
    assert not result.warnings
    assert result.meta["tables_extracted"] == 1
    assert result.artifact_ref is not None
    artifact_files = Path(result.artifact_ref.root).rglob("*.md")
    row = "| left " + "\\" * (2 * backslashes + 1) + "| right | kept |"
    assert any(row in file.read_text(encoding="utf-8").splitlines() for file in artifact_files)
