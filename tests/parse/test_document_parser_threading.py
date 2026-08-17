# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract tests for the unified AnyDoc document parser."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers import anydoc


class _FakeStorage:
    def __init__(self, media_dir: Path) -> None:
        self.media_dir = media_dir
        self.saved: list[tuple[str, bytes, str, str]] = []

    def save_image(
        self,
        resource_name: str,
        data: bytes,
        *,
        filename: str,
        extension: str,
    ) -> Path:
        self.saved.append((resource_name, data, filename, extension))
        return self.media_dir / resource_name / "images" / f"{filename}{extension}"


def _model(**values: Any) -> SimpleNamespace:
    return SimpleNamespace(**values)


def _text(value: str) -> SimpleNamespace:
    return _model(kind="text", text=value, style=None)


def _paragraph(*content: SimpleNamespace) -> SimpleNamespace:
    return _model(kind="paragraph", content=list(content))


def _document(*blocks: SimpleNamespace, assets=None, notes=None) -> SimpleNamespace:
    return _model(blocks=list(blocks), assets=assets or [], notes=notes or [])


def _stub_markdown_parse(parser: anydoc.AnyDocParser) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def parse_content(
        content: str,
        source_path: str | None = None,
        instruction: str = "",
        **kwargs,
    ):
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

    parser._markdown_parser.parse_content = parse_content
    return seen


@pytest.mark.asyncio
async def test_anydoc_parser_offloads_conversion_and_forwards_markdown_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parser = anydoc.AnyDocParser()
    seen = _stub_markdown_parse(parser)
    calls: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    converted = anydoc._RenderedDocument(
        markdown="# Converted\n",
        detected_format="docx",
        warnings=["conversion warning"],
        assets_referenced=2,
        images_extracted=1,
    )
    monkeypatch.setattr(anydoc.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(parser, "_convert", lambda *args, **kwargs: converted)
    monkeypatch.setattr(anydoc, "version", lambda _package: "0.1.8")
    storage = _FakeStorage(tmp_path / "media")
    monkeypatch.setattr("openviking_cli.utils.storage.get_storage", lambda: storage)

    source = tmp_path / "upload.docx"
    source.write_bytes(b"placeholder")
    caller_media = tmp_path / "caller-media"
    result = await parser.parse(
        source,
        instruction="preserve tables",
        source_name="季度报告.docx",
        allowed_media_dirs=[caller_media],
        split_content=False,
    )

    assert len(calls) == 1
    assert calls[0][0] is parser._convert
    assert calls[0][1] == (source,)
    assert calls[0][2] == {
        "resource_name": "季度报告.docx",
        "storage": storage,
    }
    assert seen == {
        "content": "# Converted\n",
        "source_path": str(source),
        "instruction": "preserve tables",
        "kwargs": {
            "base_dir": tmp_path,
            "allowed_media_dirs": [caller_media, storage.media_dir],
            "source_name": "季度报告.docx",
            "split_content": False,
        },
    }
    assert result.source_format == "docx"
    assert result.parser_name == "AnyDocParser"
    assert result.warnings == ["conversion warning"]
    assert result.meta["images_extracted"] == 1


def test_anydoc_renderer_preserves_image_position_and_saves_repeated_asset_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = _FakeStorage(tmp_path / "media")
    asset = _model(
        id=7,
        media_type="image/png",
        origin_part="word/media/image.png",
        data=b"valid-image",
    )
    embedded = _model(
        kind="image",
        alt="diagram",
        source=_model(kind="asset", asset_id=7),
    )
    external = _model(
        kind="image",
        alt="remote",
        source=_model(kind="external", url="https://example.com/image.png"),
    )
    document = _document(
        _paragraph(_text("before "), embedded, _text(" middle "), embedded, external),
        assets=[asset],
    )
    monkeypatch.setattr(anydoc, "is_valid_image", lambda *_args: True)

    renderer = anydoc._AnyDocMarkdownRenderer(
        document,
        source_format="docx",
        resource_name="report",
        storage=storage,
    )
    markdown = renderer.render()

    image_ref = "report/images/anydoc_asset_7.png"
    assert markdown == (
        f"before ![diagram]({image_ref}) middle ![diagram]({image_ref})"
        "![remote](https://example.com/image.png)\n"
    )
    assert storage.saved == [("report", b"valid-image", "anydoc_asset_7", ".png")]
    assert renderer.images_extracted == 1
    assert renderer.assets_referenced == {7}


def test_anydoc_renderer_keeps_ppt_speaker_notes_in_document_order(tmp_path: Path) -> None:
    heading = _model(kind="heading", level=1, content=[_text("Slide One")], anchor=None)
    notes = _model(kind="block_quote", blocks=[_paragraph(_text("Presenter detail"))])
    document = _document(heading, _paragraph(_text("Slide body")), notes)

    markdown = anydoc._AnyDocMarkdownRenderer(
        document,
        source_format="pptx",
        resource_name="slides",
        storage=_FakeStorage(tmp_path / "media"),
    ).render()

    assert markdown == ("# Slide One\n\nSlide body\n\n### Speaker Notes\n\nPresenter detail\n")


def test_anydoc_renderer_warns_and_keeps_alt_for_unusable_assets(tmp_path: Path) -> None:
    document = _document(
        _paragraph(
            _model(
                kind="image",
                alt="attachment",
                source=_model(kind="asset", asset_id=3),
            ),
            _text(" "),
            _model(
                kind="image",
                alt="missing preview",
                source=_model(kind="unavailable"),
            ),
        ),
        assets=[
            _model(
                id=3,
                media_type="application/octet-stream",
                origin_part="embedding.bin",
                data=b"not-an-image",
            )
        ],
    )
    renderer = anydoc._AnyDocMarkdownRenderer(
        document,
        source_format="xlsx",
        resource_name="book",
        storage=_FakeStorage(tmp_path / "media"),
    )

    assert renderer.render() == "attachment missing preview\n"
    assert renderer.warnings == [
        "AnyDoc asset 3 is not an image (application/octet-stream); kept as alt text",
        "Embedded image is unavailable: missing preview",
    ]
