# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PowerPoint embedded-image extraction regressions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from pptx.enum.shapes import MSO_SHAPE_TYPE

from openviking.parse.parsers.powerpoint import PowerPointParser


def test_convert_to_markdown_persists_picture_shapes_in_slide_order(tmp_path: Path):
    parser = PowerPointParser()
    media_dir = tmp_path / "media"
    saved_path = media_dir / "deck" / "image1.png"
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
        "deck", b"embedded-png", filename="image1", extension=".png"
    )
    assert "![image1](deck/image1.png)" in markdown
    assert markdown.index("![image1]") < markdown.index("Caption after image")


def test_convert_picture_failure_is_non_fatal(tmp_path: Path):
    parser = PowerPointParser()
    storage = SimpleNamespace(
        media_dir=tmp_path,
        save_image=MagicMock(side_effect=OSError("media unavailable")),
    )
    picture = SimpleNamespace(image=SimpleNamespace(blob=b"data", ext="jpeg"))

    assert parser._convert_picture(picture, "deck", storage, [0]) == ""
