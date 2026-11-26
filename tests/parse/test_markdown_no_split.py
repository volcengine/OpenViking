# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for parsed documents whose Markdown body must remain in one file."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from openviking.parse.image_rewrite import IMAGE_MAPPINGS_FILENAME, _rewrite_content
from openviking.parse.mode import ParseMode, normalize_parse_mode
from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.config.parser_config import ParserConfig, PDFConfig


class _FakeVikingFS:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.directories: set[str] = set()

    def create_temp_uri(self) -> str:
        return "viking://temp/pdf-no-split"

    async def mkdir(self, uri: str, exist_ok: bool = False) -> None:
        if uri in self.directories and not exist_ok:
            raise FileExistsError(uri)
        self.directories.add(uri)

    async def write_file(self, uri: str, content: str) -> None:
        self.files[uri] = content


def _long_markdown(*, with_headings: bool = False) -> str:
    sections = []
    for index in range(30):
        heading = f"## Scene {index}\n\n" if with_headings else ""
        sections.append(f"{heading}paragraph {index} " + "x" * 500)
    return "\n\n".join(sections)


def test_normalize_parse_mode_accepts_no_split_and_rejects_no_parse() -> None:
    assert normalize_parse_mode("default") is ParseMode.DEFAULT
    assert normalize_parse_mode("no_split") is ParseMode.NO_SPLIT
    assert normalize_parse_mode(ParseMode.NO_SPLIT) is ParseMode.NO_SPLIT

    with pytest.raises(InvalidArgumentError, match="default, no_split"):
        normalize_parse_mode("no_parse")


@pytest.mark.asyncio
async def test_flattened_no_split_markdown_creates_temp_root_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "document.md"
    content = "# Document\n\nBody"
    source.write_text(content, encoding="utf-8")
    fake_fs = _FakeVikingFS()
    parser = MarkdownParser()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fake_fs)

    result = await parser.parse(
        source,
        split_content=False,
        flatten_single_output=True,
    )

    assert result.warnings == []
    assert fake_fs.directories == {"viking://temp/pdf-no-split"}
    assert fake_fs.files["viking://temp/pdf-no-split/document.md"] == content


@pytest.mark.asyncio
@pytest.mark.parametrize("with_headings", [False, True])
async def test_long_markdown_no_split_plans_one_complete_file(
    with_headings: bool,
) -> None:
    content = _long_markdown(with_headings=with_headings)
    parser = MarkdownParser(config=ParserConfig(max_section_size=32, max_section_chars=128))

    layout = await parser._compute_layout(
        content,
        "viking://temp/test",
        source_path="/tmp/社交网络中英文剧本.pdf",
        resource_name="社交网络中英文剧本",
        split_content=False,
    )

    writes = [op for op in layout.ops if op.kind == "write"]
    assert [(op.uri, op.content) for op in writes] == [
        (
            "viking://temp/test/社交网络中英文剧本/社交网络中英文剧本.md",
            content,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("same_basename", [False, True])
@pytest.mark.parametrize("split_content", [False, True])
async def test_long_markdown_default_mode_still_splits(tmp_path, same_basename, split_content):
    """Splitting preserves each source image even when sibling sections share an output folder."""
    references = []
    sections = []
    for label, color in [("Left", "red"), ("Right", "blue")]:
        name = "chart.png" if same_basename else f"{label}.png"
        reference = f"{label}/{name}"
        image = tmp_path / reference
        image.parent.mkdir()
        Image.new("RGB", (64, 64), color).save(image)
        references.append(reference)
        sections.append(f"# {label}\n\n" + f"{label} text. " * 65 + f"\n\n![chart]({reference})")
    source = tmp_path / "guide.md"
    source.write_text("\n\n".join(sections), encoding="utf-8")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    result = await MarkdownParser(config=ParserConfig(max_section_chars=1000)).parse(
        source, parse_output_store=store, split_content=split_content
    )
    assert result.artifact_ref is not None
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    documents = list(root.glob("*.md"))
    assert len(documents) == (2 if split_content else 1)
    mapping = json.loads((root / IMAGE_MAPPINGS_FILENAME).read_text(encoding="utf-8"))
    filenames = {name for values in mapping.values() for name in values.values()}
    assert len(filenames) == len(list(root.glob("*.png"))) == 2
    manifest = await read_artifact_manifest(store, result.artifact_ref)
    seen = set()
    for document in documents:
        values = mapping[document.name]
        for reference, name in values.items():
            assert (root / name).read_bytes() == (tmp_path / reference).read_bytes()
            assert f"guide/{name}" in manifest
            seen.add(reference)
        _, count = _rewrite_content(
            document.read_text(encoding="utf-8"), "viking://resources/guide", filenames, values
        )
        assert count == len(values)
    assert seen == set(references)


@pytest.mark.asyncio
async def test_split_preserves_preface_and_body_after_empty_headings() -> None:
    body = "\n\n".join(f"paragraph {index} " + "x" * 500 for index in range(10))
    preface = (
        "## Preface\n\nPREFACE\n\n"
        "#\n\nThe first paragraph remains body text.\n\n"
        "# \t\r\n\r\nThe second paragraph remains body text.\r\n\r\n"
        "# \t\r\n\r\nThe third paragraph remains body text."
    )
    content = f"{preface}\n\n{body}\n\n# Chapter 1\n\n{body}\n\n# Chapter 2\n\n{body}"
    parser = MarkdownParser(config=ParserConfig(max_section_size=512, max_section_chars=2048))

    layout = await parser._compute_layout(content, "viking://temp/test", resource_name="book")

    writes = [op for op in layout.ops if op.kind == "write"]
    assert len(writes) > 1
    assert len({op.uri for op in writes}) == len(writes)
    assert preface in "".join(op.content for op in writes)


@pytest.mark.asyncio
async def test_pdf_no_split_converts_to_one_complete_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "社交网络中英文剧本.pdf"
    source.write_bytes(b"%PDF-fixture")
    content = _long_markdown(with_headings=True)
    fake_fs = _FakeVikingFS()
    parser = PDFParser(PDFConfig(strategy="local", max_section_size=32, max_section_chars=128))
    markdown_parser = parser._get_markdown_parser()
    monkeypatch.setattr(markdown_parser, "_get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        parser,
        "_convert_to_markdown",
        AsyncMock(return_value=(content, {})),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.storage.get_storage",
        lambda: SimpleNamespace(media_dir=tmp_path),
    )

    result = await parser.parse(
        source,
        resource_name="社交网络中英文剧本",
        split_content=False,
    )

    assert result.parser_name == "PDFParser"
    assert (
        fake_fs.files["viking://temp/pdf-no-split/社交网络中英文剧本/社交网络中英文剧本.md"]
        == content
    )
    assert not any(uri.endswith(".pdf") for uri in fake_fs.files)
