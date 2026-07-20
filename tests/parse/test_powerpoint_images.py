# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PowerPoint embedded-image extraction regressions."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pptx
from PIL import Image
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

from openviking.parse.parsers.powerpoint import PowerPointParser
from openviking_cli.utils.storage import StoragePath


def _write_png(path: Path, color: tuple[int, int, int]) -> bytes:
    Image.new("RGB", (32, 24), color=color).save(path)
    return path.read_bytes()


def _picture_placeholder_layout(prs):
    for layout in prs.slide_layouts:
        if any(
            placeholder.placeholder_format.type == PP_PLACEHOLDER.PICTURE
            for placeholder in layout.placeholders
        ):
            return layout
    raise AssertionError("default PowerPoint template has no picture placeholder")


def test_convert_to_markdown_persists_picture_shapes_in_slide_order(tmp_path: Path):
    parser = PowerPointParser()
    media_dir = tmp_path / "media"
    digest = hashlib.sha256(b"embedded-png").hexdigest()[:12]
    filename = f"image1_{digest}"
    saved_path = media_dir / "deck" / f"{filename}.png"
    storage = SimpleNamespace(media_dir=media_dir, save_image=MagicMock(return_value=saved_path))

    picture = SimpleNamespace(
        is_placeholder=False,
        shape_type=MSO_SHAPE_TYPE.PICTURE,
        image=SimpleNamespace(blob=b"embedded-png", ext="png"),
    )
    text = SimpleNamespace(
        is_placeholder=False,
        shape_type=MSO_SHAPE_TYPE.TEXT_BOX,
        text="Caption after image",
        has_table=False,
    )
    presentation = SimpleNamespace(
        slides=[SimpleNamespace(shapes=[picture, text], has_notes_slide=False)]
    )
    pptx_module = SimpleNamespace(Presentation=lambda _path: presentation)

    markdown = parser._convert_to_markdown(
        tmp_path / "deck.pptx", pptx_module, resource_name="deck", storage=storage
    )

    storage.save_image.assert_called_once_with(
        "deck", b"embedded-png", filename=filename, extension=".png"
    )
    assert f"![{filename}](deck/{filename}.png)" in markdown
    assert markdown.index(f"![{filename}]") < markdown.index("Caption after image")


def test_convert_to_markdown_recurses_into_grouped_pictures(tmp_path: Path):
    parser = PowerPointParser()
    media_dir = tmp_path / "media"
    digest = hashlib.sha256(b"grouped-png").hexdigest()[:12]
    filename = f"image1_{digest}"
    saved_path = media_dir / "deck" / f"{filename}.png"
    storage = SimpleNamespace(media_dir=media_dir, save_image=MagicMock(return_value=saved_path))
    picture = SimpleNamespace(
        is_placeholder=False,
        shape_type=MSO_SHAPE_TYPE.PICTURE,
        image=SimpleNamespace(blob=b"grouped-png", ext="png"),
    )
    group = SimpleNamespace(
        is_placeholder=False,
        shape_type=MSO_SHAPE_TYPE.GROUP,
        shapes=[picture],
    )
    slide = SimpleNamespace(shapes=[group], has_notes_slide=False)

    markdown = parser._extract_slide_content(
        slide, resource_name="deck", storage=storage, image_counter=[0]
    )

    assert markdown == f"![{filename}](deck/{filename}.png)"


def test_real_picture_placeholder_is_extracted(tmp_path: Path):
    parser = PowerPointParser()
    storage = StoragePath(base_path=tmp_path)
    image_path = tmp_path / "placeholder.png"
    image_bytes = _write_png(image_path, (220, 40, 40))
    presentation_path = tmp_path / "placeholder.pptx"

    prs = pptx.Presentation()
    slide = prs.slides.add_slide(_picture_placeholder_layout(prs))
    picture_placeholder = next(
        placeholder
        for placeholder in slide.placeholders
        if placeholder.placeholder_format.type == PP_PLACEHOLDER.PICTURE
    )
    picture_placeholder.insert_picture(str(image_path))
    prs.save(presentation_path)

    markdown = parser._convert_to_markdown(
        presentation_path, pptx, resource_name="placeholder", storage=storage
    )

    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    assert f"![image1_{digest}]" in markdown
    assert (storage.media_dir / "placeholder" / "images" / f"image1_{digest}.png").exists()


def test_same_stem_presentations_do_not_overwrite_extracted_images(tmp_path: Path):
    parser = PowerPointParser()
    storage = StoragePath(base_path=tmp_path)
    markdown_outputs = []
    expected_blobs = []

    for directory, color in (("first", (20, 40, 220)), ("second", (40, 180, 60))):
        source_dir = tmp_path / directory
        source_dir.mkdir()
        image_path = source_dir / "picture.png"
        expected_blobs.append(_write_png(image_path, color))
        presentation_path = source_dir / "deck.pptx"
        prs = pptx.Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_picture(str(image_path), 0, 0)
        prs.save(presentation_path)
        markdown_outputs.append(
            parser._convert_to_markdown(
                presentation_path, pptx, resource_name="deck", storage=storage
            )
        )

    stored_images = sorted((storage.media_dir / "deck" / "images").glob("image1_*.png"))
    assert len(stored_images) == 2
    assert {path.read_bytes() for path in stored_images} == set(expected_blobs)
    assert markdown_outputs[0] != markdown_outputs[1]


def test_convert_picture_failure_is_non_fatal(tmp_path: Path):
    parser = PowerPointParser()
    storage = SimpleNamespace(
        media_dir=tmp_path,
        save_image=MagicMock(side_effect=OSError("media unavailable")),
    )
    picture = SimpleNamespace(image=SimpleNamespace(blob=b"data", ext="jpeg"))

    assert parser._convert_picture(picture, "deck", storage, [0]) == ""
