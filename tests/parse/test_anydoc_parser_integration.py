from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers import anydoc, anydoc_converter
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
async def test_anydoc_parser_converts_and_forwards_markdown_options(tmp_path, monkeypatch):
    storage = _patch_storage(monkeypatch, tmp_path)
    parser = anydoc.AnyDocParser()
    seen = _stub_markdown_parse(parser)
    source = tmp_path / "slides.pps"
    import_root = tmp_path / "import"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda self, path, **kwargs: SimpleNamespace(
            markdown="# converted slides",
            source_format="pps",
            images_saved=2,
            assets_referenced=3,
            warnings=("skipped tiny image",),
        ),
    )

    result = await parser.parse(
        source,
        source_name="Slides.pps",
        enable_link_rewrite=True,
        link_rewrite_root=str(import_root),
        allowed_media_dirs=[import_root],
        flatten_single_output=True,
        split_content=False,
    )

    assert ".pps" in parser.supported_extensions
    assert ".ods" in parser.supported_extensions
    assert ".rtf" in parser.supported_extensions
    assert seen["content"] == "# converted slides"
    assert seen["source_path"] == str(source)
    assert seen["kwargs"]["source_name"] == "Slides.pps"
    assert seen["kwargs"]["enable_link_rewrite"] is True
    assert seen["kwargs"]["link_rewrite_root"] == str(import_root)
    assert seen["kwargs"]["allowed_media_dirs"] == [import_root, storage.media_dir]
    assert seen["kwargs"]["flatten_single_output"] is True
    assert seen["kwargs"]["split_content"] is False
    assert seen["kwargs"]["base_dir"] == source.parent
    assert result.source_format == "pps"
    assert result.parser_name == "AnyDocParser"
    assert result.parser_version == "1.0"
    assert result.meta["converter"] == "firecrawl-anydoc"
    assert result.meta["intermediate_markdown_length"] == len("# converted slides")
    assert result.meta["images_extracted"] == 2
    assert result.meta["assets_referenced"] == 3
    assert result.warnings == ["skipped tiny image"]


@pytest.mark.asyncio
async def test_anydoc_parser_reraises_conversion_failure(tmp_path, monkeypatch):
    _patch_storage(monkeypatch, tmp_path)
    parser = anydoc.AnyDocParser(anydoc_config=AnydocConfig())
    source = tmp_path / "report.docx"
    source.write_bytes(b"placeholder")
    anydoc_error = RuntimeError("conversion failed")
    monkeypatch.setattr(
        anydoc_converter.AnyDocConverter,
        "convert",
        lambda *args, **kwargs: (_ for _ in ()).throw(anydoc_error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await parser.parse(source)

    assert exc_info.value is anydoc_error


@pytest.mark.asyncio
async def test_anydoc_parser_disabled_rejects_office_file(tmp_path):
    parser = anydoc.AnyDocParser(anydoc_config=AnydocConfig(enabled=False))
    source = tmp_path / "book.epub"
    source.write_bytes(b"placeholder")

    with pytest.raises(RuntimeError, match="AnyDoc parser is disabled"):
        await parser.parse(source)


@pytest.mark.asyncio
async def test_anydoc_parser_parse_content_delegates_to_markdown_parser():
    parser = anydoc.AnyDocParser()
    seen = _stub_markdown_parse(parser)

    result = await parser.parse_content(
        "# converted",
        source_path="/tmp/report.docx",
        instruction="keep headings",
        source_name="report.docx",
        split_content=False,
    )

    assert seen["content"] == "# converted"
    assert seen["source_path"] == "/tmp/report.docx"
    assert seen["instruction"] == "keep headings"
    assert seen["kwargs"]["source_name"] == "report.docx"
    assert seen["kwargs"]["split_content"] is False
    assert result.source_format == "docx"
    assert result.parser_name == "AnyDocParser"
    assert result.parser_version == "1.0"


def test_registry_routes_office_and_epub_extensions_to_anydoc_parser():
    anydoc_config = AnydocConfig(enabled=False)
    registry = ParserRegistry(
        parser_configs={
            "markdown": ParserConfig(),
            "anydoc": anydoc_config,
        }
    )

    assert set(registry._parsers) >= {"anydoc", "markdown", "pdf", "html", "text"}
    assert "word" not in registry._parsers
    assert "excel" not in registry._parsers
    assert registry._parsers["anydoc"].anydoc_config is anydoc_config

    for extension in anydoc.AnyDocParser().supported_extensions:
        parser = registry.get_parser_for_file(Path("sample" + extension))
        assert isinstance(parser, anydoc.AnyDocParser)
