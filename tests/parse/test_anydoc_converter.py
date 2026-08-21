from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.parse.parsers.anydoc_converter import AnyDocConverter


class FakeStorage:
    def __init__(self, root: Path):
        self.media_dir = root
        self.saved = []

    def save_image(self, resource_name, image_data, filename=None, extension=".png"):
        p = self.media_dir / resource_name / "images" / f"{filename}{extension}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(image_data)
        self.saved.append(p)
        return p


def _doc_with_image():
    asset = SimpleNamespace(
        id=0,
        media_type="image/png",
        origin_part="word/media/image1.png",
        bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
    )
    image = SimpleNamespace(
        kind="image",
        alt="chart",
        source=SimpleNamespace(kind="asset", asset_id=0),
    )
    para = SimpleNamespace(kind="paragraph", content=[image])
    return SimpleNamespace(blocks=[para], notes=[], assets=[asset])


def test_converter_rewrites_asset_images(tmp_path, monkeypatch):
    storage = FakeStorage(tmp_path)
    converter = AnyDocConverter()
    monkeypatch.setattr(
        "openviking.parse.parsers.anydoc_converter._load_document",
        lambda path, format_hint=None: ("docx", _doc_with_image()),
    )
    result = converter.convert(tmp_path / "x.docx", resource_name="Demo", storage=storage)
    assert result.images_saved == 1
    assert "](Demo/images/image1.png)" in result.markdown or "image1.png)" in result.markdown
    assert storage.saved


def test_converter_skips_non_image_assets(tmp_path, monkeypatch):
    asset = SimpleNamespace(
        id=0,
        media_type="application/octet-stream",
        origin_part="oleObject1.bin",
        bytes=b"BIN",
    )
    image = SimpleNamespace(
        kind="image",
        alt="obj",
        source=SimpleNamespace(kind="asset", asset_id=0),
    )
    doc = SimpleNamespace(
        blocks=[SimpleNamespace(kind="paragraph", content=[image])],
        notes=[],
        assets=[asset],
    )
    storage = FakeStorage(tmp_path)
    monkeypatch.setattr(
        "openviking.parse.parsers.anydoc_converter._load_document",
        lambda path, format_hint=None: ("docx", doc),
    )
    result = AnyDocConverter().convert(tmp_path / "x.docx", resource_name="Demo", storage=storage)
    assert result.images_saved == 0
    assert storage.saved == []


def test_converter_continues_when_image_save_fails(tmp_path, monkeypatch):
    class FailingStorage(FakeStorage):
        def save_image(self, *args, **kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(
        "openviking.parse.parsers.anydoc_converter._load_document",
        lambda path, format_hint=None: ("docx", _doc_with_image()),
    )

    result = AnyDocConverter().convert(
        tmp_path / "x.docx",
        resource_name="Demo",
        storage=FailingStorage(tmp_path),
    )

    assert result.images_saved == 0
    assert result.markdown.strip() == "chart"


def test_converter_rejects_pdf(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    storage = FakeStorage(tmp_path)
    with pytest.raises(ValueError, match="PDF"):
        AnyDocConverter().convert(pdf, resource_name="Demo", storage=storage)


def test_converter_loads_signatureless_csv_from_extension(tmp_path):
    pytest.importorskip("anydoc")
    source = tmp_path / "minimal.csv"
    source.write_text("name,value\nalpha,1\n", encoding="utf-8")

    result = AnyDocConverter().convert(
        source,
        resource_name="minimal",
        storage=FakeStorage(tmp_path / "media"),
    )

    assert result.source_format == "csv"
    assert "alpha" in result.markdown


def test_anydoc_maps_xlsb_to_xlsx_parser():
    anydoc = pytest.importorskip("anydoc")

    assert anydoc.format_from_extension(".xlsb") == "xlsx"


def test_converter_adds_path_to_anydoc_conversion_error(tmp_path):
    anydoc = pytest.importorskip("anydoc")
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not a document")

    with pytest.raises(anydoc.ConvertError, match="broken\\.docx"):
        AnyDocConverter().convert(
            source,
            resource_name="broken",
            storage=FakeStorage(tmp_path / "media"),
        )


def test_converter_saves_table_cell_image_once(tmp_path, monkeypatch):
    document = _doc_with_image()
    image = document.blocks[0].content[0]
    cell = SimpleNamespace(content=[SimpleNamespace(kind="paragraph", content=[image])])
    document.blocks = [
        SimpleNamespace(
            kind="table",
            rows=[SimpleNamespace(cells=[cell])],
        )
    ]
    storage = FakeStorage(tmp_path)
    monkeypatch.setattr(
        "openviking.parse.parsers.anydoc_converter._load_document",
        lambda path, format_hint=None: ("docx", document),
    )

    result = AnyDocConverter().convert(tmp_path / "x.docx", resource_name="Demo", storage=storage)

    assert result.images_saved == 1
    assert len(storage.saved) == 1


def test_converter_serializes_anydoc_list_and_table_shapes(tmp_path, monkeypatch):
    def paragraph(text):
        return SimpleNamespace(
            kind="paragraph",
            content=[SimpleNamespace(kind="text", text=text, style=None)],
        )

    list_block = SimpleNamespace(
        kind="list",
        list=SimpleNamespace(
            marker="decimal",
            start=1,
            items=[SimpleNamespace(blocks=[paragraph("Item")])],
        ),
        content=None,
    )

    def cell(text):
        return SimpleNamespace(
            kind="origin",
            cell=SimpleNamespace(blocks=[paragraph(text)]),
        )

    table_block = SimpleNamespace(
        kind="table",
        table=SimpleNamespace(grid=[[cell("A"), cell("B")]]),
        content=None,
    )
    document = SimpleNamespace(
        blocks=[list_block, table_block],
        notes=[],
        assets=[],
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.anydoc_converter._load_document",
        lambda path, format_hint=None: ("docx", document),
    )

    result = AnyDocConverter().convert(
        tmp_path / "x.docx",
        resource_name="Demo",
        storage=FakeStorage(tmp_path),
    )

    assert "1. Item" in result.markdown
    assert "| A | B |" in result.markdown


def test_converter_serializes_anydoc_link_target(tmp_path, monkeypatch):
    link = SimpleNamespace(
        kind="link",
        content=[SimpleNamespace(kind="text", text="Example", style=None)],
        target=SimpleNamespace(kind="external", value="https://example.com"),
    )
    document = SimpleNamespace(
        blocks=[SimpleNamespace(kind="paragraph", content=[link])],
        notes=[],
        assets=[],
    )
    monkeypatch.setattr(
        "openviking.parse.parsers.anydoc_converter._load_document",
        lambda path, format_hint=None: ("docx", document),
    )

    result = AnyDocConverter().convert(
        tmp_path / "x.docx",
        resource_name="Demo",
        storage=FakeStorage(tmp_path),
    )

    assert "[Example](https://example.com)" in result.markdown
