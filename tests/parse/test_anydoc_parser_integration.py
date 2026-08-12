from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers import anydoc_converter, epub, excel, legacy_doc, powerpoint, word
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.parser_config import AnydocConfig, ParserConfig


class FakeStorage:
    def __init__(self, media_dir: Path):
        self.media_dir = media_dir
        self.saved = []

    def save_image(self, resource_name, image_data, filename=None, extension=".png"):
        path = self.media_dir / resource_name / "images" / f"{filename}{extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_data)
        self.saved.append(path)
        return path


def _stub_markdown_parse(parser):
    seen = {}

    async def parse_content(content, source_path=None, instruction="", **kwargs):
        seen.update(
            content=content,
            source_path=source_path,
            instruction=instruction,
            kwargs=kwargs,
        )
        return create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=source_path,
            source_format="markdown",
            parser_name="MarkdownParser",
        )

    parser._md_parser.parse_content = parse_content
    return seen


def _patch_storage(monkeypatch, tmp_path):
    storage = FakeStorage(tmp_path / "media")
    monkeypatch.setattr("openviking_cli.utils.storage.get_storage", lambda: storage)
    return storage


@pytest.mark.asyncio
async def test_word_parser_anydoc_rewrites_image_and_allows_media_dir(tmp_path, monkeypatch):
    storage = _patch_storage(monkeypatch, tmp_path)
    parser = word.WordParser(anydoc_config=AnydocConfig())
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "report.docm"
    source.write_bytes(b"placeholder")

    asset = SimpleNamespace(
        id=0,
        media_type="image/png",
        origin_part="word/media/chart.png",
        bytes=b"\x89PNG\r\n\x1a\n",
    )
    image = SimpleNamespace(
        kind="image",
        alt="chart",
        source=SimpleNamespace(kind="asset", asset_id=0),
    )
    document = SimpleNamespace(
        blocks=[SimpleNamespace(kind="paragraph", content=[image])],
        notes=[],
        assets=[asset],
    )
    monkeypatch.setattr(
        anydoc_converter,
        "_load_document",
        lambda path, format_hint=None: ("docm", document),
    )

    result = await parser.parse(source, source_name="Quarterly Report.docm")

    assert parser.supported_extensions == [".docx", ".docm", ".odt", ".rtf"]
    assert "image1.png" in seen["content"]
    assert seen["kwargs"]["allowed_media_dirs"] == [storage.media_dir]
    assert result.source_format == "docm"
    assert result.parser_name == "WordParser"
    assert storage.saved


@pytest.mark.asyncio
async def test_word_parser_uses_legacy_conversion_when_anydoc_disabled(tmp_path, monkeypatch):
    _patch_storage(monkeypatch, tmp_path)
    parser = word.WordParser(anydoc_config=AnydocConfig(enable=False))
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "report.docx"
    source.write_bytes(b"placeholder")
    fake_docx = SimpleNamespace()
    monkeypatch.setitem(__import__("sys").modules, "docx", fake_docx)
    monkeypatch.setattr(
        parser,
        "_convert_to_markdown",
        lambda path, docx_module, resource_name=None, storage=None: "# legacy",
    )
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda *args, **kwargs: pytest.fail("anydoc must not run"),
    )

    await parser.parse(source)

    assert seen["content"] == "# legacy"


@pytest.mark.asyncio
async def test_word_parser_falls_back_after_anydoc_failure(tmp_path, monkeypatch):
    _patch_storage(monkeypatch, tmp_path)
    parser = word.WordParser(anydoc_config=AnydocConfig(fallback_to_legacy=True))
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "report.docx"
    source.write_bytes(b"placeholder")
    monkeypatch.setitem(__import__("sys").modules, "docx", SimpleNamespace())
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("conversion failed")),
    )
    monkeypatch.setattr(
        parser,
        "_convert_to_markdown",
        lambda *args, **kwargs: "# legacy fallback",
    )

    await parser.parse(source)

    assert seen["content"] == "# legacy fallback"


@pytest.mark.asyncio
async def test_word_parser_rejects_odt_when_anydoc_disabled(tmp_path, monkeypatch):
    _patch_storage(monkeypatch, tmp_path)
    parser = word.WordParser(anydoc_config=AnydocConfig(enable=False))
    source = tmp_path / "report.odt"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        parser,
        "_legacy_convert",
        lambda *args, **kwargs: pytest.fail("python-docx must not handle ODT"),
    )

    with pytest.raises(RuntimeError, match=r"anydoc.*disabled.*\.odt"):
        await parser.parse(source)


@pytest.mark.asyncio
async def test_word_parser_reraises_anydoc_error_for_rtf_without_legacy_converter(
    tmp_path, monkeypatch
):
    _patch_storage(monkeypatch, tmp_path)
    parser = word.WordParser(anydoc_config=AnydocConfig(fallback_to_legacy=True))
    source = tmp_path / "report.rtf"
    source.write_bytes(b"placeholder")
    anydoc_error = RuntimeError("conversion failed")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda *args, **kwargs: (_ for _ in ()).throw(anydoc_error),
    )
    monkeypatch.setattr(
        parser,
        "_legacy_convert",
        lambda *args, **kwargs: pytest.fail("python-docx must not handle RTF"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await parser.parse(source)

    assert exc_info.value is anydoc_error


@pytest.mark.asyncio
async def test_legacy_doc_parser_uses_anydoc_for_real_doc(tmp_path, monkeypatch):
    storage = _patch_storage(monkeypatch, tmp_path)
    parser = legacy_doc.LegacyDocParser(anydoc_config=AnydocConfig())
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0placeholder")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda self, path, **kwargs: SimpleNamespace(
            markdown="# converted doc",
            source_format="doc",
        ),
    )

    result = await parser.parse(source)

    assert seen["content"] == "# converted doc"
    assert seen["kwargs"]["allowed_media_dirs"] == [storage.media_dir]
    assert result.source_format == "doc"
    assert result.parser_name == "LegacyDocParser"


@pytest.mark.asyncio
async def test_legacy_doc_parser_uses_ole_extractor_when_anydoc_disabled(tmp_path, monkeypatch):
    parser = legacy_doc.LegacyDocParser(anydoc_config=AnydocConfig(enable=False))
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0placeholder")
    monkeypatch.setattr(parser, "_extract_text", lambda path: "# ole fallback")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda *args, **kwargs: pytest.fail("anydoc must not run"),
    )

    await parser.parse(source)

    assert seen["content"] == "# ole fallback"


@pytest.mark.asyncio
async def test_powerpoint_parser_uses_anydoc_and_allows_media_dir(tmp_path, monkeypatch):
    storage = _patch_storage(monkeypatch, tmp_path)
    parser = powerpoint.PowerPointParser(anydoc_config=AnydocConfig())
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "slides.pps"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda self, path, **kwargs: SimpleNamespace(
            markdown="# converted slides\n\n## Notes\n\nPresenter note",
            source_format="pps",
        ),
    )

    result = await parser.parse(source)

    assert parser.supported_extensions == [
        ".pptx",
        ".ppt",
        ".pptm",
        ".pps",
        ".ppsx",
        ".ppsm",
        ".pot",
        ".odp",
    ]
    assert seen["content"] == "# converted slides\n\n## Notes\n\nPresenter note"
    assert seen["kwargs"]["allowed_media_dirs"] == [storage.media_dir]
    assert result.source_format == "pps"
    assert result.parser_name == "PowerPointParser"


@pytest.mark.asyncio
async def test_powerpoint_parser_rejects_ppt_when_anydoc_disabled(tmp_path, monkeypatch):
    _patch_storage(monkeypatch, tmp_path)
    parser = powerpoint.PowerPointParser(anydoc_config=AnydocConfig(enable=False))
    source = tmp_path / "legacy.ppt"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        parser,
        "_legacy_convert",
        lambda *args, **kwargs: pytest.fail("python-pptx must not handle PPT"),
        raising=False,
    )

    with pytest.raises(RuntimeError, match=r"anydoc.*disabled.*\.ppt"):
        await parser.parse(source)


@pytest.mark.asyncio
async def test_excel_parser_uses_anydoc_truncates_rows_and_allows_media_dir(tmp_path, monkeypatch):
    storage = _patch_storage(monkeypatch, tmp_path)
    parser = excel.ExcelParser(anydoc_config=AnydocConfig(), max_rows_per_sheet=1)
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "book.ods"
    import_root = tmp_path / "import"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda self, path, **kwargs: SimpleNamespace(
            markdown="## Sheet1\n\n|a|b|\n|---|---|\n|1|2|\n|3|4|\n",
            source_format="ods",
        ),
    )

    result = await parser.parse(
        source,
        source_name="Budget.ods",
        enable_link_rewrite=True,
        link_rewrite_root=str(import_root),
        allowed_media_dirs=[import_root],
        flatten_single_output=True,
        split_content=False,
    )

    assert parser.supported_extensions == [".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".csv"]
    assert "|1|2|" in seen["content"]
    assert "|3|4|" not in seen["content"]
    assert seen["kwargs"]["enable_link_rewrite"] is True
    assert seen["kwargs"]["link_rewrite_root"] == str(import_root)
    assert seen["kwargs"]["allowed_media_dirs"] == [import_root, storage.media_dir]
    assert seen["kwargs"]["flatten_single_output"] is True
    assert seen["kwargs"]["split_content"] is False
    assert seen["kwargs"]["base_dir"] == source.parent
    assert result.source_format == "ods"
    assert result.parser_name == "ExcelParser"


@pytest.mark.asyncio
async def test_excel_parser_rejects_ods_when_anydoc_disabled(tmp_path, monkeypatch):
    _patch_storage(monkeypatch, tmp_path)
    parser = excel.ExcelParser(anydoc_config=AnydocConfig(enable=False))
    source = tmp_path / "book.ods"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        parser,
        "_convert_to_markdown",
        lambda *args, **kwargs: pytest.fail("openpyxl must not handle ODS"),
    )

    with pytest.raises(RuntimeError, match=r"anydoc.*disabled.*\.ods"):
        await parser.parse(source)


@pytest.mark.asyncio
async def test_epub_parser_uses_anydoc_and_allows_media_dir(tmp_path, monkeypatch):
    storage = _patch_storage(monkeypatch, tmp_path)
    parser = epub.EPubParser(anydoc_config=AnydocConfig())
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "book.epub"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda self, path, **kwargs: SimpleNamespace(
            markdown="# converted book",
            source_format="epub",
        ),
    )

    result = await parser.parse(source)

    assert seen["content"] == "# converted book"
    assert seen["kwargs"]["allowed_media_dirs"] == [storage.media_dir]
    assert result.source_format == "epub"
    assert result.parser_name == "EPubParser"


def test_registry_passes_anydoc_config_to_office_parsers():
    anydoc_config = AnydocConfig(enable=False, fallback_to_legacy=True)
    registry = ParserRegistry(
        parser_configs={
            "word": ParserConfig(),
            "legacy_doc": ParserConfig(),
            "powerpoint": ParserConfig(),
            "excel": ParserConfig(),
            "epub": ParserConfig(),
            "anydoc": anydoc_config,
        }
    )

    assert registry._parsers["word"].anydoc_config is anydoc_config
    assert registry._parsers["legacy_doc"].anydoc_config is anydoc_config
    assert registry._parsers["powerpoint"].anydoc_config is anydoc_config
    assert registry._parsers["excel"].anydoc_config is anydoc_config
    assert registry._parsers["epub"].anydoc_config is anydoc_config
